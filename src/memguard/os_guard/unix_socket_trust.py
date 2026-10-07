from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, Optional
import os
import errno
import secrets
import socket
import stat
import struct
import sys
import time


ROOT_USER_SOCKET_OPT_IN_ENV = "MEMGUARD_ALLOW_ROOT_KYLIN_USER_SOCKET"
"""Explicit opt-in for root to use Kylin user sockets after endpoint verification."""

# These are platform-owned directories that are validated for root ownership,
# sticky-bit protection, and an exact symlink target before they are trusted.
_OPENKYLIN_TMP_SYMLINK = Path("/tmp")  # nosec B108
_OPENKYLIN_TMP_TARGET = Path("/var/tmp")  # nosec B108

# Linux UAPI unix_diag.h / net/unix/diag.c (VFS is inode, then raw dev_t).
_NL_HEADER = struct.Struct("=IHHII")
_DIAG_REQUEST = struct.Struct("=BBHIIIII")
_DIAG_MESSAGE = struct.Struct("=BBBBIII")
_DIAG_MAX_DATAGRAM = 65536
_DIAG_MAX_BYTES = 1024 * 1024
_DIAG_MAX_RECORDS = 4096
_DIAG_MAX_DATAGRAMS = 64
_SOCK_DIAG_BY_FAMILY = 20
_TCP_LISTEN = 10


def _aligned(length: int) -> int:
    return (length + 3) & ~3


def _parse_netlink_datagram(data: bytes, sequence: int, port_id: int) -> list:
    """Strict NLMSG framing; malformed, interrupted or foreign replies fail closed."""
    if not data or len(data) > _DIAG_MAX_DATAGRAM:
        raise ValueError("invalid netlink datagram length")
    messages = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < _NL_HEADER.size:
            raise ValueError("truncated netlink header")
        length, kind, flags, seq, pid = _NL_HEADER.unpack_from(data, offset)
        end = offset + length
        aligned_end = offset + _aligned(length)
        if length < _NL_HEADER.size or end > len(data) or aligned_end > len(data):
            raise ValueError("invalid netlink message length/alignment")
        if seq != sequence or pid != port_id:
            raise ValueError("netlink sequence/port mismatch")
        if flags & 0x10 or kind == 4:  # NLM_F_DUMP_INTR / NLMSG_OVERRUN
            raise ValueError("interrupted/overrun netlink dump")
        payload = data[offset + _NL_HEADER.size:end]
        if kind == 2:  # NLMSG_ERROR contains errno and the original nlmsghdr.
            if len(payload) < 4 + _NL_HEADER.size:
                raise ValueError("truncated netlink error")
            error = struct.unpack_from("=i", payload)[0]
            original = _NL_HEADER.unpack_from(payload, 4)
            if original[1] != _SOCK_DIAG_BY_FAMILY or original[3] != sequence:
                raise ValueError("netlink error request mismatch")
            if error > 0:
                raise ValueError("invalid netlink errno")
            if error:
                raise OSError(-error, os.strerror(-error))
        elif kind == 3:  # NLMSG_DONE may contain the dump result errno.
            if payload:
                if len(payload) < 4:
                    raise ValueError("truncated netlink dump result")
                error = struct.unpack_from("=i", payload)[0]
                if error:
                    raise OSError(abs(error), "netlink dump failed")
        elif kind != _SOCK_DIAG_BY_FAMILY:
            raise ValueError("unexpected netlink message type")
        messages.append((kind, flags, payload))
        offset = aligned_end
    return messages


def _parse_unix_diag(payload: bytes) -> Dict[str, object]:
    if len(payload) < _DIAG_MESSAGE.size:
        raise ValueError("truncated unix_diag_msg")
    family, kind, state, pad, inode, cookie0, cookie1 = _DIAG_MESSAGE.unpack_from(payload)
    if family != 1 or pad != 0 or inode == 0:
        raise ValueError("invalid unix_diag_msg identity")
    attrs = {}
    offset = _DIAG_MESSAGE.size
    while offset < len(payload):
        if len(payload) - offset < 4:
            raise ValueError("truncated unix_diag attribute")
        length, attr = struct.unpack_from("=HH", payload, offset)
        end = offset + length
        aligned_end = offset + _aligned(length)
        if length < 4 or aligned_end > len(payload):
            raise ValueError("invalid unix_diag attribute length/alignment")
        if attr in attrs:
            raise ValueError("duplicate unix_diag attribute")
        attrs[attr] = payload[offset + 4:end]
        offset = aligned_end
    record = {"type": kind, "state": state, "socket_inode": inode,
              "cookie": (cookie0, cookie1)}
    if 0 in attrs:
        name = attrs[0]
        if not 1 <= len(name) <= 108:
            raise ValueError("invalid unix_diag name length")
        record["name"] = name[:-1] if name.endswith(b"\0") else name
    for attr, length in ((1, 8), (7, 4)):
        if attr in attrs and len(attrs[attr]) != length:
            raise ValueError("invalid unix_diag VFS/UID length")
    if 1 in attrs:
        record["vfs_inode"], record["vfs_device"] = struct.unpack("=II", attrs[1])
    if 7 in attrs:
        record["uid"] = struct.unpack("=I", attrs[7])[0]
    return record


def _read_listener_credentials(
    path: str, socket_stat: os.stat_result, timeout: float,
) -> Dict[str, object]:
    evidence = {"method": "NETLINK_SOCK_DIAG", "supported": False, "verified": False}
    if not sys.platform.startswith("linux") or not hasattr(socket, "AF_NETLINK"):
        evidence["reason"] = "Linux NETLINK_SOCK_DIAG unavailable"
        return evidence
    evidence["supported"] = True
    diag = None
    try:
        # Kernel dev_t is NOT the userspace stat.st_dev encoding (kdev_t.h).
        vfs_device = (os.major(socket_stat.st_dev) << 20) | os.minor(socket_stat.st_dev)
        if not 0 < socket_stat.st_ino <= 0xFFFFFFFF or vfs_device > 0xFFFFFFFF:
            raise ValueError("VFS identity cannot be represented by unix_diag")
        name = os.fsencode(path)
        deadline = time.monotonic() + timeout
        diag = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 4)  # NETLINK_SOCK_DIAG
        diag.bind((0, 0))
        port_id = diag.getsockname()[0]
        sequence = secrets.randbelow(0xFFFFFFFE) + 1
        byte_count = record_count = datagram_count = 0

        def request(seq: int, inode: int = 0, cookie: tuple = (0xFFFFFFFF, 0xFFFFFFFF)):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("netlink deadline exceeded")
            diag.settimeout(remaining)
            body = _DIAG_REQUEST.pack(1, 0, 0, 1 << _TCP_LISTEN, inode, 0x43, *cookie)
            flags = 1 if inode else 0x301  # NLM_F_REQUEST [+ NLM_F_DUMP]
            message = _NL_HEADER.pack(_NL_HEADER.size + len(body), 20, flags, seq, port_id) + body
            if diag.sendto(message, (0, 0)) != len(message):
                raise ValueError("short netlink request")

        def receive(seq: int) -> list:
            nonlocal byte_count, record_count, datagram_count
            remaining = deadline - time.monotonic()
            if remaining <= 0 or datagram_count >= _DIAG_MAX_DATAGRAMS:
                raise TimeoutError("netlink time/datagram limit exceeded")
            diag.settimeout(remaining)
            data, _ancillary, flags, sender = diag.recvmsg(_DIAG_MAX_DATAGRAM)
            datagram_count += 1
            byte_count += len(data)
            if flags & socket.MSG_TRUNC or sender != (0, 0):
                raise ValueError("truncated/non-kernel netlink reply")
            if byte_count > _DIAG_MAX_BYTES:
                raise ValueError("netlink byte limit exceeded")
            messages = _parse_netlink_datagram(data, seq, port_id)
            record_count += len(messages)
            if record_count > _DIAG_MAX_RECORDS:
                raise ValueError("netlink record limit exceeded")
            return messages

        def matches(record: dict) -> bool:
            return bool(record.get("name") == name
                        and record.get("vfs_inode") == socket_stat.st_ino
                        and record.get("vfs_device") == vfs_device)

        request(sequence)
        found = None
        done = False
        while not done:
            for kind, flags, payload in receive(sequence):
                if done:
                    raise ValueError("netlink data after dump completion")
                if kind == 3:
                    done = True
                elif kind == 20:
                    if not flags & 2:
                        raise ValueError("non-multipart dump record")
                    record = _parse_unix_diag(payload)
                    if matches(record):
                        if found is not None:
                            raise ValueError("ambiguous listener identity")
                        found = record
        if found is None:
            raise ValueError("no kernel listener matches path and VFS identity")
        if found["cookie"] == (0xFFFFFFFF, 0xFFFFFFFF):
            raise ValueError("kernel listener identity cookie unavailable")
        # Exact query with cookie prevents trusting a closed/reused dump entry.
        sequence += 1
        request(sequence, found["socket_inode"], found["cookie"])
        replies = receive(sequence)
        if len(replies) != 1 or replies[0][0] != 20 or replies[0][1] & 2:
            raise ValueError("invalid exact listener reply")
        live = _parse_unix_diag(replies[0][2])
        if (not matches(live) or live != found or live["type"] != socket.SOCK_STREAM
                or live["state"] != _TCP_LISTEN or live.get("uid") != socket_stat.st_uid):
            raise ValueError("kernel listener state/UID/identity mismatch")
        evidence.update({"verified": True, "uid": live["uid"],
                         "socket_inode": live["socket_inode"],
                         "vfs_inode": live["vfs_inode"], "vfs_device": live["vfs_device"],
                         "path": path, "state": "LISTEN", "cookie": live["cookie"]})
    except (OSError, ValueError, struct.error) as exc:
        if isinstance(exc, OSError) and exc.errno in {
            errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.ENOSYS, errno.EOPNOTSUPP,
        }:
            evidence["supported"] = False
        evidence["reason"] = f"NETLINK_SOCK_DIAG listener verification failed: {exc}"
        evidence["error"] = str(exc)
    finally:
        if diag is not None:
            diag.close()
    return evidence


def _current_uid() -> Optional[int]:
    return int(os.getuid()) if hasattr(os, "getuid") else None


def _mode_text(mode: int) -> str:
    return oct(stat.S_IMODE(mode))


def _stat_payload(result: os.stat_result) -> Dict[str, object]:
    mode = int(result.st_mode)
    return {
        "owner": int(result.st_uid),
        "mode": _mode_text(mode),
        "world_writable": bool(mode & stat.S_IWOTH),
        "sticky": bool(mode & stat.S_ISVTX),
        "is_symlink": stat.S_ISLNK(mode),
        "is_socket": stat.S_ISSOCK(mode),
        "device": int(result.st_dev),
        "inode": int(result.st_ino),
    }


def _root_opt_in_enabled() -> bool:
    return os.environ.get(ROOT_USER_SOCKET_OPT_IN_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _read_peer_credentials(sock: socket.socket) -> Dict[str, object]:
    if not hasattr(socket, "SO_PEERCRED"):
        return {
            "method": "SO_PEERCRED",
            "supported": False,
            "verified": False,
            "reason": "当前平台不支持 SO_PEERCRED，无法验证 Unix socket 对端身份",
        }
    size = struct.calcsize("3i")
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, size)
        pid, uid, gid = struct.unpack("3i", raw)
    except (OSError, struct.error) as exc:
        return {
            "method": "SO_PEERCRED",
            "supported": True,
            "verified": False,
            "error": str(exc),
            "reason": "读取 SO_PEERCRED 失败，无法验证 Unix socket 对端身份",
        }
    return {
        "method": "SO_PEERCRED",
        "supported": True,
        "verified": True,
        "pid": int(pid),
        "uid": int(uid),
        "gid": int(gid),
    }


def _base_result(path: str, timeout: float) -> Dict[str, object]:
    return {
        "path": path,
        "exists": False,
        "trusted": False,
        "socket_ready": False,
        "reason": "",
        "socket": {
            "owner": None,
            "mode": None,
            "world_writable": False,
            "sticky": False,
            "is_symlink": False,
            "is_socket": False,
        },
        "parent_directory": {
            "path": str(Path(path).parent),
            "owner": None,
            "mode": None,
            "world_writable": False,
            "sticky": False,
            "is_symlink": False,
        },
        "parent_directory_warning": False,
        "connection": {
            "attempted": False,
            "connected": False,
            "closed": False,
            "timeout_seconds": timeout,
        },
        "peer_credentials": {
            "method": "SO_PEERCRED",
            "supported": hasattr(socket, "SO_PEERCRED"),
            "verified": False,
        },
        "root_policy": {
            "applies": False,
            "allowed": True,
            "environment_variable": ROOT_USER_SOCKET_OPT_IN_ENV,
            "explicit_opt_in": _root_opt_in_enabled(),
        },
    }


def _trusted_openkylin_tmp_symlink(
    parent: Path,
    parent_stat: os.stat_result,
) -> bool:
    if parent != _OPENKYLIN_TMP_SYMLINK or int(parent_stat.st_uid) != 0:
        return False
    try:
        resolved = Path(os.path.realpath(parent))
        target_stat = os.lstat(resolved)
    except OSError:
        return False
    return bool(
        resolved == _OPENKYLIN_TMP_TARGET
        and stat.S_ISDIR(target_stat.st_mode)
        and int(target_stat.st_uid) == 0
        and target_stat.st_mode & stat.S_ISVTX
    )


def _parent_stats(path: str) -> tuple[Optional[os.stat_result], Optional[str], Optional[str]]:
    absolute = Path(os.path.abspath(path))
    immediate: Optional[os.stat_result] = None
    for index, parent in enumerate(absolute.parents):
        try:
            parent_stat = os.lstat(parent)
        except OSError as exc:
            return None, str(parent), str(exc)
        if index == 0:
            immediate = parent_stat
        if stat.S_ISLNK(parent_stat.st_mode):
            if _trusted_openkylin_tmp_symlink(parent, parent_stat):
                if index == 0:
                    try:
                        immediate = os.lstat(Path(os.path.realpath(parent)))
                    except OSError as exc:
                        return None, str(parent), str(exc)
                continue
            return immediate, str(parent), "symlink"
    return immediate, None, None


def inspect_unix_socket(
    path: str,
    *,
    expected_uid: Optional[int] = None,
    trusted_uids: Iterable[int] = (),
    timeout: float = 0.2,
    root_user_runtime: bool = False,
    probe_mode: str = "connect",
) -> Dict[str, object]:
    """Validate Unix socket ownership and live endpoint evidence.

    ``connect`` (legacy default) connects and closes without protocol bytes.
    ``passive`` only queries Linux NETLINK_SOCK_DIAG; it never connects to the
    business server, and reports listener UID rather than SO_PEERCRED. Root callers
    may not use Kylin user-runtime sockets unless
    ``MEMGUARD_ALLOW_ROOT_KYLIN_USER_SOCKET=1`` is set, and the normal owner and
    endpoint-identity checks still have to pass.
    """

    socket_path = os.fspath(path)
    bounded_timeout = max(0.01, min(float(timeout), 1.0))
    result = _base_result(socket_path, bounded_timeout)
    result["probe_mode"] = probe_mode
    result["listener_credentials"] = {
        "method": "NETLINK_SOCK_DIAG", "supported": False, "verified": False,
    }
    if probe_mode not in {"connect", "passive"}:
        result["reason"] = "Unsupported Unix socket probe_mode"
        return result
    current_uid = _current_uid()
    owner_uid = current_uid if expected_uid is None else int(expected_uid)
    allowed_uids = {int(uid) for uid in trusted_uids}
    if owner_uid is not None:
        allowed_uids.add(owner_uid)

    root_policy = result["root_policy"]
    root_blocked = False
    if isinstance(root_policy, dict) and root_user_runtime and current_uid == 0:
        root_policy["applies"] = True
        root_policy["allowed"] = _root_opt_in_enabled()
        root_blocked = not bool(root_policy["allowed"])

    parent_stat, unsafe_parent, parent_error = _parent_stats(socket_path)
    if parent_stat is not None:
        parent_payload = _stat_payload(parent_stat)
        parent_payload["path"] = str(Path(socket_path).parent)
        result["parent_directory"] = parent_payload
    if parent_error == "symlink":
        result["reason"] = f"Unix socket 父目录链包含符号链接：{unsafe_parent}"
        return result
    if parent_error:
        result["reason"] = f"无法检查 Unix socket 父目录 {unsafe_parent}：{parent_error}"
        return result

    parent = result["parent_directory"]
    if isinstance(parent, dict):
        result["parent_directory_warning"] = bool(
            parent.get("world_writable") and not parent.get("sticky")
        )

    try:
        socket_stat = os.lstat(socket_path)
    except FileNotFoundError:
        result["reason"] = "Unix socket 路径不存在"
        return result
    except OSError as exc:
        result["reason"] = f"无法 lstat Unix socket：{exc}"
        return result

    result["exists"] = True
    socket_payload = _stat_payload(socket_stat)
    result["socket"] = socket_payload
    if socket_payload["is_symlink"]:
        result["reason"] = "Unix socket 路径是符号链接，已拒绝"
        return result
    if not socket_payload["is_socket"]:
        result["reason"] = "目标不是 Unix socket，已拒绝普通文件或其他文件类型"
        return result
    if owner_uid is None and not allowed_uids:
        result["reason"] = "当前平台无法确定进程 UID，不能验证 Unix socket 所有者"
        return result
    if int(socket_payload["owner"]) not in allowed_uids:
        result["reason"] = (
            f"Unix socket 所有者 UID {socket_payload['owner']} 不在可信 UID "
            f"{sorted(allowed_uids)} 中"
        )
        return result
    if root_blocked:
        result["reason"] = (
            "root 进程默认禁止连接 Kylin 用户态 Unix socket；"
            f"仅可显式设置 {ROOT_USER_SOCKET_OPT_IN_ENV}=1 后再执行完整对端校验"
        )
        return result
    if not hasattr(socket, "AF_UNIX"):
        result["reason"] = "当前平台不支持 AF_UNIX，不能验证 Unix socket"
        return result

    if probe_mode == "passive":
        listener = _read_listener_credentials(socket_path, socket_stat, bounded_timeout)
        result["listener_credentials"] = listener
        if not listener.get("verified"):
            result["reason"] = str(listener.get("reason") or "Kernel listener unverified")
            return result
        # Repeat path-chain validation as well as leaf identity after kernel queries.
        final_parent, unsafe_parent, parent_error = _parent_stats(socket_path)
        try:
            final_stat = os.lstat(socket_path)
        except OSError as exc:
            result["reason"] = f"Passive probe path recheck failed: {exc}"
            return result
        if (parent_error or final_parent is None or parent_stat is None
                or any(getattr(final_parent, field) != getattr(parent_stat, field)
                       for field in ("st_dev", "st_ino", "st_uid", "st_mode"))
                or any(getattr(final_stat, field) != getattr(socket_stat, field)
                       for field in ("st_dev", "st_ino", "st_uid", "st_mode"))):
            result["reason"] = "Unix socket path changed during passive verification"
            return result
        result["trusted"] = True
        result["socket_ready"] = True
        result["reason"] = "Unix socket trusted: kernel listener UID, path and VFS identity verified"
        if result["parent_directory_warning"]:
            result["reason"] += "; parent is world-writable without sticky bit"
        return result

    client: Optional[socket.socket] = None
    connection = result["connection"]
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(bounded_timeout)
        if isinstance(connection, dict):
            connection["attempted"] = True
        client.connect(socket_path)
        if isinstance(connection, dict):
            connection["connected"] = True
        peer = _read_peer_credentials(client)
        result["peer_credentials"] = peer
    except (OSError, TimeoutError) as exc:
        if isinstance(connection, dict):
            connection["error"] = str(exc)
        result["reason"] = f"Unix socket 最小连接探测失败：{exc}"
        return result
    finally:
        if client is not None:
            client.close()
            if isinstance(connection, dict):
                connection["closed"] = True

    peer = result["peer_credentials"]
    if not isinstance(peer, dict) or not peer.get("supported"):
        result["reason"] = "平台不支持 SO_PEERCRED，Unix socket 对端身份未验证，不能标记为可信"
        return result
    if not peer.get("verified"):
        result["reason"] = str(peer.get("reason") or "Unix socket 对端身份未验证")
        return result
    expected_peer_uid = int(socket_payload["owner"])
    if int(peer.get("uid", -1)) != expected_peer_uid:
        result["reason"] = (
            f"Unix socket 对端 UID {peer.get('uid')} 与预期 UID "
            f"{expected_peer_uid} 不匹配"
        )
        return result

    try:
        final_stat = os.lstat(socket_path)
    except OSError as exc:
        result["reason"] = f"连接后复核 Unix socket 失败：{exc}"
        return result
    if (
        final_stat.st_dev != socket_stat.st_dev
        or final_stat.st_ino != socket_stat.st_ino
        or final_stat.st_uid != socket_stat.st_uid
        or final_stat.st_mode != socket_stat.st_mode
    ):
        result["reason"] = "连接探测期间 Unix socket 路径发生变化，已拒绝"
        return result

    result["trusted"] = True
    result["socket_ready"] = True
    if result["parent_directory_warning"]:
        result["reason"] = (
            "Unix socket 可信；核心 owner 与 SO_PEERCRED 校验通过，"
            "但父目录 world-writable 且未设置 sticky bit"
        )
    else:
        result["reason"] = "Unix socket 可信：类型、所有者、连接与 SO_PEERCRED 校验通过"
    return result
