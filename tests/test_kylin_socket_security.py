from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os
import socket
import stat
import threading
import time

import pytest

from memguard.os_guard import openkylin_native
from memguard.os_guard import unix_socket_trust
from memguard.retrieval import kylin_sdk
from memguard.retrieval.kylin_sdk import KylinEmbeddingBackend, KylinVectorStoreProbe


HAS_UNIX_PEERCRED = bool(
    hasattr(socket, "AF_UNIX")
    and hasattr(socket, "SO_PEERCRED")
    and hasattr(os, "getuid")
)


@contextmanager
def _unix_server(path: Path):
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("当前平台不支持 AF_UNIX")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(8)
    server.settimeout(0.05)
    accepted = {"count": 0}
    stopped = threading.Event()

    def serve() -> None:
        while not stopped.is_set():
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            accepted["count"] += 1
            connection.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield accepted
    finally:
        stopped.set()
        server.close()
        thread.join(timeout=1)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


@pytest.mark.skipif(not HAS_UNIX_PEERCRED, reason="需要 Linux AF_UNIX SO_PEERCRED")
def test_trusted_real_unix_socket_verifies_owner_and_peer(tmp_path):
    path = tmp_path / "trusted.sock"
    with _unix_server(path):
        trust = unix_socket_trust.inspect_unix_socket(str(path), expected_uid=os.getuid())

    assert trust["trusted"] is True
    assert trust["socket_ready"] is True
    assert trust["socket"]["is_socket"] is True
    assert trust["socket"]["owner"] == os.getuid()
    assert trust["peer_credentials"]["verified"] is True
    assert trust["peer_credentials"]["uid"] == os.getuid()
    assert "可信" in trust["reason"]


def test_regular_file_and_symlink_are_rejected_without_connecting(tmp_path):
    regular = tmp_path / "regular.sock"
    regular.write_text("not a socket", encoding="utf-8")
    symlink = tmp_path / "link.sock"
    try:
        symlink.symlink_to(regular)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不能创建 symlink")

    regular_trust = unix_socket_trust.inspect_unix_socket(str(regular), expected_uid=0)
    symlink_trust = unix_socket_trust.inspect_unix_socket(str(symlink), expected_uid=0)

    assert regular_trust["trusted"] is False
    assert regular_trust["socket"]["is_socket"] is False
    assert "socket" in regular_trust["reason"].lower()
    assert symlink_trust["trusted"] is False
    assert symlink_trust["socket"]["is_symlink"] is True
    assert "符号链接" in symlink_trust["reason"]


def test_connection_probe_is_always_closed(monkeypatch, tmp_path):
    path = tmp_path / "fake.sock"
    path.write_text("placeholder", encoding="utf-8")
    original_lstat = unix_socket_trust.os.lstat
    target_stat = original_lstat(path)
    fields = list(target_stat)
    fields[0] = stat.S_IFSOCK | 0o600
    fields[4] = 0
    fake_socket_stat = os.stat_result(fields)
    observed = {"closed": False}

    class FakeSocket:
        def settimeout(self, _timeout):
            return None

        def connect(self, _path):
            return None

        def close(self):
            observed["closed"] = True

    def fake_lstat(raw_path):
        if os.fspath(raw_path) == str(path):
            return fake_socket_stat
        return original_lstat(raw_path)

    monkeypatch.setattr(unix_socket_trust.os, "lstat", fake_lstat)
    monkeypatch.setattr(unix_socket_trust.socket, "AF_UNIX", 1, raising=False)
    monkeypatch.setattr(unix_socket_trust.socket, "socket", lambda *_args: FakeSocket())
    monkeypatch.setattr(
        unix_socket_trust,
        "_read_peer_credentials",
        lambda _sock: {
            "supported": True,
            "verified": True,
            "pid": 123,
            "uid": 0,
            "gid": 0,
        },
    )

    trust = unix_socket_trust.inspect_unix_socket(str(path), expected_uid=0)

    assert trust["trusted"] is True
    assert observed["closed"] is True
    assert trust["connection"]["closed"] is True


@pytest.mark.skipif(not HAS_UNIX_PEERCRED, reason="需要 Linux AF_UNIX SO_PEERCRED")
def test_symlinked_parent_directory_is_rejected_before_connect(tmp_path):
    real_parent = tmp_path / "real-runtime"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-runtime"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不能创建目录 symlink")
    path = real_parent / "runtime.sock"

    with _unix_server(path) as accepted:
        trust = unix_socket_trust.inspect_unix_socket(
            str(linked_parent / path.name),
            expected_uid=os.getuid(),
        )
        time.sleep(0.1)

    assert trust["trusted"] is False
    assert "父目录链包含符号链接" in trust["reason"]
    assert accepted["count"] == 0


@pytest.mark.skipif(os.name != "posix", reason="需要 POSIX 绝对路径语义")
def test_openkylin_root_owned_tmp_symlink_to_sticky_var_tmp_is_allowed(monkeypatch):
    path = "/tmp/.kylin-ai-runtime-unix/1000/core-textembedding.sock"
    original_lstat = unix_socket_trust.os.lstat

    def fake_stat(mode: int, uid: int) -> os.stat_result:
        fields = [0] * 10
        fields[0] = mode
        fields[4] = uid
        return os.stat_result(fields)

    stats = {
        "/": fake_stat(stat.S_IFDIR | 0o755, 0),
        "/tmp": fake_stat(stat.S_IFLNK | 0o777, 0),
        "/var/tmp": fake_stat(stat.S_IFDIR | 0o1777, 0),
        "/tmp/.kylin-ai-runtime-unix": fake_stat(stat.S_IFDIR | 0o777, os.getuid()),
        "/tmp/.kylin-ai-runtime-unix/1000": fake_stat(stat.S_IFDIR | 0o777, os.getuid()),
    }

    def fake_lstat(raw_path):
        normalized = os.fspath(raw_path)
        if normalized in stats:
            return stats[normalized]
        return original_lstat(raw_path)

    monkeypatch.setattr(unix_socket_trust.os, "lstat", fake_lstat)
    monkeypatch.setattr(
        unix_socket_trust.os.path,
        "realpath",
        lambda raw_path: "/var/tmp" if os.fspath(raw_path) == "/tmp" else os.fspath(raw_path),
    )

    immediate, unsafe_parent, parent_error = unix_socket_trust._parent_stats(path)

    assert immediate is not None
    assert immediate.st_uid == os.getuid()
    assert unsafe_parent is None
    assert parent_error is None


@pytest.mark.skipif(not HAS_UNIX_PEERCRED, reason="需要 Linux AF_UNIX SO_PEERCRED")
def test_wrong_socket_owner_is_rejected_before_connect(monkeypatch, tmp_path):
    path = tmp_path / "wrong-owner.sock"
    original_lstat = unix_socket_trust.os.lstat

    def wrong_owner_lstat(raw_path):
        result = original_lstat(raw_path)
        if os.fspath(raw_path) == str(path):
            fields = list(result)
            fields[4] = os.getuid() + 1
            return os.stat_result(fields)
        return result

    with _unix_server(path) as accepted:
        monkeypatch.setattr(unix_socket_trust.os, "lstat", wrong_owner_lstat)
        trust = unix_socket_trust.inspect_unix_socket(str(path), expected_uid=os.getuid())
        time.sleep(0.1)

    assert trust["trusted"] is False
    assert "所有者" in trust["reason"]
    assert accepted["count"] == 0


@pytest.mark.skipif(not HAS_UNIX_PEERCRED, reason="需要 Linux AF_UNIX SO_PEERCRED")
def test_refused_connection_is_not_ready(tmp_path):
    path = tmp_path / "refused.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.close()
    try:
        trust = unix_socket_trust.inspect_unix_socket(str(path), expected_uid=os.getuid())
    finally:
        path.unlink(missing_ok=True)

    assert trust["trusted"] is False
    assert trust["socket_ready"] is False
    assert trust["connection"]["connected"] is False
    assert "连接" in trust["reason"]


@pytest.mark.skipif(not HAS_UNIX_PEERCRED, reason="需要 Linux AF_UNIX SO_PEERCRED")
def test_peer_uid_mismatch_is_rejected(monkeypatch, tmp_path):
    path = tmp_path / "peer-mismatch.sock"
    monkeypatch.setattr(
        unix_socket_trust,
        "_read_peer_credentials",
        lambda _sock: {
            "supported": True,
            "verified": True,
            "pid": 123,
            "uid": os.getuid() + 1,
            "gid": os.getgid(),
        },
    )
    with _unix_server(path):
        trust = unix_socket_trust.inspect_unix_socket(str(path), expected_uid=os.getuid())

    assert trust["trusted"] is False
    assert trust["peer_credentials"]["verified"] is True
    assert "对端 UID" in trust["reason"]


@pytest.mark.skipif(
    not (hasattr(socket, "AF_UNIX") and hasattr(os, "getuid")),
    reason="需要可校验 owner 的 AF_UNIX 平台",
)
def test_unsupported_peer_credentials_never_report_trusted(monkeypatch, tmp_path):
    path = tmp_path / "unsupported-peercred.sock"
    monkeypatch.setattr(
        unix_socket_trust,
        "_read_peer_credentials",
        lambda _sock: {
            "supported": False,
            "verified": False,
            "reason": "当前平台不支持 SO_PEERCRED",
        },
    )
    with _unix_server(path):
        trust = unix_socket_trust.inspect_unix_socket(str(path), expected_uid=os.getuid())

    assert trust["trusted"] is False
    assert trust["socket_ready"] is False
    assert trust["peer_credentials"]["supported"] is False
    assert "未验证" in trust["reason"]


@pytest.mark.skipif(not HAS_UNIX_PEERCRED, reason="需要 Linux AF_UNIX SO_PEERCRED")
def test_world_writable_parent_without_sticky_is_warning_after_core_checks(tmp_path):
    parent = tmp_path / "runtime"
    parent.mkdir()
    parent.chmod(0o777)
    path = parent / "core-textembedding.sock"
    try:
        with _unix_server(path):
            trust = unix_socket_trust.inspect_unix_socket(str(path), expected_uid=os.getuid())
    finally:
        parent.chmod(0o700)

    assert trust["trusted"] is True
    assert trust["parent_directory"]["world_writable"] is True
    assert trust["parent_directory"]["sticky"] is False
    assert trust["parent_directory_warning"] is True
    assert "父目录" in trust["reason"]


def test_root_user_runtime_is_disabled_by_default(monkeypatch, tmp_path):
    path = tmp_path / "runtime.sock"
    path.write_text("placeholder", encoding="utf-8")
    original_lstat = unix_socket_trust.os.lstat
    fields = list(original_lstat(path))
    fields[0] = stat.S_IFSOCK | 0o600
    fields[4] = 0
    fake_socket_stat = os.stat_result(fields)

    def fake_lstat(raw_path):
        if os.fspath(raw_path) == str(path):
            return fake_socket_stat
        return original_lstat(raw_path)

    monkeypatch.delenv("MEMGUARD_ALLOW_ROOT_KYLIN_USER_SOCKET", raising=False)
    monkeypatch.setattr(unix_socket_trust, "_current_uid", lambda: 0)
    monkeypatch.setattr(unix_socket_trust.os, "lstat", fake_lstat)
    trust = unix_socket_trust.inspect_unix_socket(
        str(path),
        expected_uid=0,
        root_user_runtime=True,
    )

    assert trust["trusted"] is False
    assert trust["exists"] is True
    assert trust["socket"]["is_socket"] is True
    assert trust["connection"]["attempted"] is False
    assert trust["root_policy"]["allowed"] is False
    assert "MEMGUARD_ALLOW_ROOT_KYLIN_USER_SOCKET" in trust["reason"]


def test_embedding_backend_root_default_falls_back_before_sdk_load(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    paths = set()
    for name in kylin_sdk.RUNTIME_SOCKET_NAMES:
        path = runtime / name
        path.write_text("placeholder", encoding="utf-8")
        paths.add(os.path.normcase(os.path.normpath(str(path))))
    original_lstat = unix_socket_trust.os.lstat

    def fake_lstat(raw_path):
        result = original_lstat(raw_path)
        normalized = os.path.normcase(os.path.normpath(os.fspath(raw_path)))
        if normalized in paths:
            fields = list(result)
            fields[0] = stat.S_IFSOCK | 0o600
            fields[4] = 0
            return os.stat_result(fields)
        return result

    monkeypatch.delenv("MEMGUARD_ALLOW_ROOT_KYLIN_USER_SOCKET", raising=False)
    monkeypatch.setattr(kylin_sdk.os, "getuid", lambda: 0, raising=False)
    monkeypatch.setattr(unix_socket_trust.os, "lstat", fake_lstat)
    backend = KylinEmbeddingBackend()
    monkeypatch.setattr(backend, "_runtime_base_dir", lambda: str(runtime))
    loaded = {"called": False}

    def fail_if_loaded(_lib_path=None):
        loaded["called"] = True
        raise AssertionError("root 默认策略应在 SDK 加载前阻断")

    monkeypatch.setattr(backend, "_load_library", fail_if_loaded)
    result = backend.embed("root 进程不得发送的正文", raise_on_error=False)

    assert result["ok"] is False
    assert result["fallback"] == "hash_embedding"
    assert result["runtime"]["trusted"] is False
    assert "MEMGUARD_ALLOW_ROOT_KYLIN_USER_SOCKET" in result["error"]
    assert loaded["called"] is False


@pytest.mark.skipif(not HAS_UNIX_PEERCRED, reason="需要 Linux AF_UNIX SO_PEERCRED")
def test_root_opt_in_still_requires_peer_credential(monkeypatch, tmp_path):
    path = tmp_path / "root-opt-in.sock"
    original_lstat = unix_socket_trust.os.lstat

    def root_owned_lstat(raw_path):
        result = original_lstat(raw_path)
        if os.fspath(raw_path) == str(path):
            fields = list(result)
            fields[4] = 0
            return os.stat_result(fields)
        return result

    monkeypatch.setenv("MEMGUARD_ALLOW_ROOT_KYLIN_USER_SOCKET", "1")
    monkeypatch.setattr(unix_socket_trust, "_current_uid", lambda: 0)
    monkeypatch.setattr(unix_socket_trust.os, "lstat", root_owned_lstat)
    monkeypatch.setattr(
        unix_socket_trust,
        "_read_peer_credentials",
        lambda _sock: {
            "supported": True,
            "verified": True,
            "pid": 123,
            "uid": 1000,
            "gid": 1000,
        },
    )
    with _unix_server(path):
        trust = unix_socket_trust.inspect_unix_socket(
            str(path),
            expected_uid=0,
            root_user_runtime=True,
        )

    assert trust["root_policy"]["allowed"] is True
    assert trust["trusted"] is False
    assert "对端 UID" in trust["reason"]


def test_embedding_backend_blocks_untrusted_runtime_before_loading_sdk(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for name in kylin_sdk.RUNTIME_SOCKET_NAMES:
        (runtime / name).write_text("attacker controlled", encoding="utf-8")
    backend = KylinEmbeddingBackend()
    monkeypatch.setattr(backend, "_runtime_base_dir", lambda: str(runtime))
    loaded = {"called": False}

    def fail_if_loaded(_lib_path=None):
        loaded["called"] = True
        raise AssertionError("不可信 runtime 不得加载或初始化 SDK")

    monkeypatch.setattr(backend, "_load_library", fail_if_loaded)
    result = backend.embed("绝不能发送的查询正文", raise_on_error=False)

    assert result["ok"] is False
    assert result["fallback"] == "hash_embedding"
    assert result["runtime_trusted"] is False
    assert loaded["called"] is False


def test_embedding_status_disables_sdk_when_runtime_is_untrusted(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for name in kylin_sdk.RUNTIME_SOCKET_NAMES:
        (runtime / name).write_text("not a socket", encoding="utf-8")
    backend = KylinEmbeddingBackend()
    monkeypatch.setattr(backend, "_runtime_base_dir", lambda: str(runtime))
    monkeypatch.setattr(backend, "_load_library", lambda _path=None: pytest.fail("不应初始化 SDK session"))

    report = backend.status(probe=True, probe_timeout=0.1)

    assert report["available"] is False
    assert report["inference_ready"] is False
    assert report["ready_for_default_retrieval"] is False
    assert report["runtime"]["trusted"] is False
    assert report["fallback"] == "hash_embedding"
    assert "不可信" in report["disabled_reason"]


def test_vector_probe_rejects_existing_regular_file(monkeypatch, tmp_path):
    path = tmp_path / "vector.sock"
    path.write_text("not a socket", encoding="utf-8")
    monkeypatch.setattr(KylinVectorStoreProbe, "_vector_socket", staticmethod(lambda: str(path)))
    monkeypatch.setattr(kylin_sdk, "CDLL", lambda _path: object())

    report = KylinVectorStoreProbe().status()

    assert report["socket_exists"] is True
    assert report["socket_ready"] is False
    assert report["trusted"] is False
    assert report["available"] is False
    assert "socket" in report["reason"].lower()


def test_openkylin_fast_status_does_not_mark_regular_file_socket_ready(monkeypatch, tmp_path):
    vector_path = tmp_path / "vector.sock"
    vector_path.write_text("not a socket", encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(
        openkylin_native,
        "_vector_socket_path",
        lambda _uid: str(vector_path),
        raising=False,
    )
    monkeypatch.setattr(
        openkylin_native,
        "_embedding_runtime_base",
        lambda _uid: str(runtime),
        raising=False,
    )

    report = openkylin_native._fast_sdk_status(
        {"kylin-ai-vector-engine.service": {"available": True, "active": True, "state": "active"}}
    )
    vector = report["vector_store"]

    assert vector["socket_exists"] is True
    assert vector["socket_ready"] is False
    assert vector["trusted"] is False
    assert vector["component_ready"] is False
    assert vector["capability_state"] != "socket_ready"
    assert vector["reason"]


@pytest.mark.skipif(not HAS_UNIX_PEERCRED, reason="需要 Linux AF_UNIX SO_PEERCRED")
def test_openkylin_fast_status_reports_real_trusted_vector_socket(monkeypatch, tmp_path):
    vector_path = tmp_path / "vector.sock"
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(
        openkylin_native,
        "_vector_socket_path",
        lambda _uid: str(vector_path),
        raising=False,
    )
    monkeypatch.setattr(
        openkylin_native,
        "_embedding_runtime_base",
        lambda _uid: str(runtime),
        raising=False,
    )
    monkeypatch.setenv("MEMGUARD_ALLOW_ROOT_KYLIN_USER_SOCKET", "1")

    with _unix_server(vector_path):
        report = openkylin_native._fast_sdk_status(
            {"kylin-ai-vector-engine.service": {"available": False, "active": False, "state": "inactive"}}
        )

    vector = report["vector_store"]
    assert vector["socket_ready"] is True
    assert vector["trusted"] is True
    assert vector["component_ready"] is True
    assert vector["capability_state"] == "socket_ready"
