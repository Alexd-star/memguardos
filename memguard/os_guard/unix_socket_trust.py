from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, Optional
import os
import socket
import stat
import struct


ROOT_USER_SOCKET_OPT_IN_ENV = "MEMGUARD_ALLOW_ROOT_KYLIN_USER_SOCKET"
"""Explicit opt-in for root to use Kylin user-runtime sockets after peer verification."""

_OPENKYLIN_TMP_SYMLINK = Path("/tmp")
_OPENKYLIN_TMP_TARGET = Path("/var/tmp")


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
            "supported": True,
            "verified": False,
            "error": str(exc),
            "reason": "读取 SO_PEERCRED 失败，无法验证 Unix socket 对端身份",
        }
    return {
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
) -> Dict[str, object]:
    """Validate a Unix socket path, owner, connection, and Linux peer credentials.

    The probe only connects and closes; it never writes protocol bytes. Root callers
    may not use Kylin user-runtime sockets unless
    ``MEMGUARD_ALLOW_ROOT_KYLIN_USER_SOCKET=1`` is set, and the normal owner and
    peer-credential checks still have to pass.
    """

    socket_path = os.fspath(path)
    bounded_timeout = max(0.01, min(float(timeout), 1.0))
    result = _base_result(socket_path, bounded_timeout)
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
