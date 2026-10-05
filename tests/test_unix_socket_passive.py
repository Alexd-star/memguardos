from __future__ import annotations

from contextlib import contextmanager
import errno
import os
import select
import socket
import struct
import sys

import pytest

from memguard.os_guard import unix_socket_trust as trust


def _attr(kind, data):
    length = 4 + len(data)
    return struct.pack("=HH", length, kind) + data + b"\0" * (trust._aligned(length) - length)


def _record(uid=1000, inode=88, device=0x12300456, name=b"/run/test.sock\0"):
    return (struct.pack("=BBBBIII", 1, 1, 10, 0, 99, 111, 222)
            + _attr(0, name) + _attr(1, struct.pack("=II", inode, device))
            + _attr(7, struct.pack("=I", uid)))


def _message(kind, payload=b"", sequence=42, pid=123, flags=0):
    length = 16 + len(payload)
    return (struct.pack("=IHHII", length, kind, flags, sequence, pid)
            + payload + b"\0" * (trust._aligned(length) - length))


def test_aligned_multipart_parser_and_vfs_field_order():
    data = _message(20, _record(), flags=2) + _message(3, struct.pack("=i", 0))
    messages = trust._parse_netlink_datagram(data, 42, 123)
    assert [message[0] for message in messages] == [20, 3]
    record = trust._parse_unix_diag(messages[0][2])
    assert record == {"type": 1, "state": 10, "socket_inode": 99,
                      "cookie": (111, 222), "name": b"/run/test.sock",
                      "vfs_inode": 88, "vfs_device": 0x12300456, "uid": 1000}


@pytest.mark.parametrize("data", [
    b"", b"short", _message(20, _record())[:-1],
    _message(20, _record(), sequence=43), _message(20, _record(), pid=0),
    _message(20, _record(), flags=0x10), _message(4), _message(1),
    _message(2, b"\0" * 4), _message(3, b"x"),
    b"\0" * (trust._DIAG_MAX_DATAGRAM + 1),
], ids=["empty", "header", "truncated", "sequence", "port", "interrupted",
        "overrun", "type", "error", "done", "oversized"])
def test_netlink_parser_rejects_untrusted_or_malformed_replies(data):
    with pytest.raises(ValueError):
        trust._parse_netlink_datagram(data, 42, 123)


@pytest.mark.parametrize("kind", [2, 3])
def test_kernel_errors_fail_closed(kind):
    payload = struct.pack("=i", -errno.EOPNOTSUPP)
    if kind == 2:
        payload += struct.pack("=IHHII", 40, 20, 0x301, 42, 123)
    with pytest.raises(OSError) as error:
        trust._parse_netlink_datagram(_message(kind, payload), 42, 123)
    assert error.value.errno == errno.EOPNOTSUPP


@pytest.mark.parametrize("payload", [
    b"short", _record()[:-1], _record() + _attr(7, struct.pack("=I", 1000)),
    struct.pack("=BBBBIII", 1, 1, 10, 0, 99, 111, 222) + _attr(1, b"x"),
    struct.pack("=BBBBIII", 1, 1, 10, 0, 99, 111, 222) + b"\x03\0\0\0",
])
def test_unix_diag_parser_rejects_bad_attributes(payload):
    with pytest.raises(ValueError):
        trust._parse_unix_diag(payload)


LINUX = sys.platform.startswith("linux") and hasattr(socket, "AF_NETLINK")
linux_only = pytest.mark.skipif(not LINUX, reason="real Linux NETLINK_SOCK_DIAG required")


@contextmanager
def _listener(tmp_path, listen=True):
    path = tmp_path / "passive.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(path))
        if listen:
            server.listen(4)
        yield path, server
    finally:
        server.close()


def _inspect(path, **kwargs):
    return trust.inspect_unix_socket(str(path), expected_uid=os.getuid(),
                                     probe_mode="passive", **kwargs)


def _assert_closed(result):
    assert result["trusted"] is False
    assert result["socket_ready"] is False
    assert result["connection"]["attempted"] is False
    assert result["peer_credentials"]["verified"] is False


@linux_only
def test_repeated_passive_inspection_never_enqueues_business_connections(tmp_path):
    with _listener(tmp_path) as (path, server):
        for _ in range(5):
            result = _inspect(path)
            assert result["trusted"] is True, result
            assert result["socket_ready"] is True
            assert result["connection"]["attempted"] is False
            assert result["connection"]["connected"] is False
            assert result["peer_credentials"]["verified"] is False
            listener = result["listener_credentials"]
            assert listener["method"] == "NETLINK_SOCK_DIAG"
            assert listener["uid"] == os.getuid()
            assert listener["vfs_inode"] == path.lstat().st_ino
            assert listener["socket_inode"] == os.fstat(server.fileno()).st_ino
            assert listener["state"] == "LISTEN"
        assert select.select([server], [], [], 0)[0] == []
        server.setblocking(False)
        with pytest.raises(BlockingIOError):
            server.accept()


@linux_only
@pytest.mark.parametrize("listen", [False, True])
def test_bound_or_closed_socket_is_not_ready(tmp_path, listen):
    with _listener(tmp_path, listen=listen) as (path, server):
        if listen:
            server.close()
        result = _inspect(path)
        _assert_closed(result)
        assert result["exists"] is True
        assert result["listener_credentials"]["verified"] is False


@linux_only
@pytest.mark.parametrize("field", ["uid", "vfs_inode", "vfs_device", "name", "state", "type"])
def test_kernel_identity_mismatch_fails_closed(tmp_path, monkeypatch, field):
    original = trust._parse_unix_diag

    def altered(payload):
        record = original(payload)
        if field in record:
            record[field] = b"/wrong.sock" if field == "name" else int(record[field]) + 1
        return record

    monkeypatch.setattr(trust, "_parse_unix_diag", altered)
    with _listener(tmp_path) as (path, _server):
        _assert_closed(_inspect(path))


@linux_only
def test_closure_between_dump_and_exact_query_fails_closed(tmp_path, monkeypatch):
    original = trust._parse_unix_diag
    with _listener(tmp_path) as (path, server):
        def close_after_dump(payload):
            record = original(payload)
            if record.get("name") == os.fsencode(path):
                server.close()
            return record

        monkeypatch.setattr(trust, "_parse_unix_diag", close_after_dump)
        _assert_closed(_inspect(path))


@linux_only
@pytest.mark.parametrize("failure", [errno.EPROTONOSUPPORT, errno.EPERM])
def test_netlink_unavailable_or_denied_never_falls_back_to_connect(tmp_path, monkeypatch, failure):
    with _listener(tmp_path) as (path, _server):
        def unavailable(*args):
            assert args[0] == socket.AF_NETLINK
            raise OSError(failure, "diagnostics unavailable")

        monkeypatch.setattr(trust.socket, "socket", unavailable)
        result = _inspect(path)
        _assert_closed(result)
        assert result["listener_credentials"]["verified"] is False
        if failure == errno.EPROTONOSUPPORT:
            assert result["listener_credentials"]["supported"] is False


@linux_only
def test_passive_keeps_root_opt_in_and_filesystem_checks(tmp_path, monkeypatch):
    with _listener(tmp_path) as (path, _server):
        monkeypatch.setattr(trust, "_current_uid", lambda: 0)
        monkeypatch.delenv(trust.ROOT_USER_SOCKET_OPT_IN_ENV, raising=False)
        _assert_closed(_inspect(path, root_user_runtime=True))
        monkeypatch.setenv(trust.ROOT_USER_SOCKET_OPT_IN_ENV, "1")
        assert _inspect(path, root_user_runtime=True)["trusted"] is True
        link = tmp_path / "link.sock"
        link.symlink_to(path)
        _assert_closed(_inspect(link))
        result = trust.inspect_unix_socket(str(path), expected_uid=os.getuid() + 1,
                                           probe_mode="passive")
        _assert_closed(result)


@linux_only
def test_path_replacement_during_passive_probe_is_rejected(tmp_path, monkeypatch):
    original = trust._read_listener_credentials
    with _listener(tmp_path) as (path, _server):
        def replace_after_verification(*args):
            result = original(*args)
            path.unlink()
            path.write_text("replaced", encoding="ascii")
            return result

        monkeypatch.setattr(trust, "_read_listener_credentials", replace_after_verification)
        _assert_closed(_inspect(path))


@linux_only
@pytest.mark.parametrize("limit", ["_DIAG_MAX_BYTES", "_DIAG_MAX_RECORDS", "_DIAG_MAX_DATAGRAMS"])
def test_netlink_dump_limits_fail_closed(tmp_path, monkeypatch, limit):
    monkeypatch.setattr(trust, limit, 0)
    with _listener(tmp_path) as (path, _server):
        _assert_closed(_inspect(path))


@linux_only
def test_netlink_deadline_fails_closed_before_request(tmp_path, monkeypatch):
    with _listener(tmp_path) as (path, _server):
        with monkeypatch.context() as patch:
            ticks = iter([0.0, 2.0])
            patch.setattr(trust.time, "monotonic", lambda: next(ticks))
            result = _inspect(path)
        _assert_closed(result)
        assert "deadline" in result["reason"]


@linux_only
def test_passive_rejects_regular_file_and_symlinked_parent(tmp_path):
    regular = tmp_path / "regular.sock"
    regular.write_text("not a socket", encoding="ascii")
    _assert_closed(_inspect(regular))
    parent = tmp_path / "real"
    parent.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(parent, target_is_directory=True)
    with _listener(parent) as (path, _server):
        _assert_closed(_inspect(alias / path.name))


def test_invalid_probe_mode_never_creates_socket(monkeypatch):
    def forbidden(*args):
        pytest.fail("invalid mode must not create a socket")

    monkeypatch.setattr(trust.socket, "socket", forbidden)
    _assert_closed(trust.inspect_unix_socket("unused", probe_mode="invalid"))
