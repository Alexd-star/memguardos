"""Bounded procedure suggestions and read-only, local postcondition evidence.

No step is executed. Postconditions use ``type``/``path`` and, for content
checks, ``value``. Missing optional fields default to empty containers. A
parameter default of None requires an argument; substitution is single-pass.
Environment conditions are literal fingerprints, not templates or a claim
that the complete host environment has been inspected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import errno
import os
from pathlib import Path, PureWindowsPath
import re
import stat
from string import Template
from typing import Any, Literal, TypedDict


MAX_FILE_BYTES = 1024 * 1024
MAX_TEXT_LENGTH = 4096
MAX_PATH_LENGTH = 1024
MAX_PARAMETERS = 32
MAX_ENVIRONMENT = 32
Status = Literal["pass", "fail", "unknown"]


class ProcedureValidationError(ValueError):
    """Invalid schema, arguments, or explicitly selected workspace."""


class CheckResult(TypedDict):
    type: str
    path: str
    status: Status
    reason: str


class VerificationResult(TypedDict):
    overall: Status
    checks: list[CheckResult]
    rendered_steps: list[str]


class EnvironmentCheck(TypedDict):
    key: str
    expected: str
    actual: str | None
    status: Status


class EnvironmentResult(TypedDict):
    overall: Status
    checks: list[EnvironmentCheck]


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z", re.ASCII)
_KINDS = {"path_exists", "file_contains", "file_not_contains"}
_FIELDS = {"goal", "steps", "failure_guards", "parameters", "postconditions", "environment"}
_CREDENTIAL_DIRS = {".ssh", ".gnupg", ".aws", ".azure", ".kube"}
_SYSTEM_ROOTS = (
    "/etc", "/proc", "/sys", "/dev", "/boot", "/root", "/usr",
    "/bin", "/sbin", "/lib", "/lib64", "/run", "/var",
)
_WINDOWS_DEVICES = {"con", "prn", "aux", "nul", "conin$", "conout$"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
}


def _string(value: Any, label: str, limit: int = MAX_TEXT_LENGTH, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ProcedureValidationError(f"{label} must be a bounded string without NUL")
    if not empty and not value.strip():
        raise ProcedureValidationError(f"{label} must not be empty")
    return value


def _strings(value: Any, label: str, minimum: int, maximum: int) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ProcedureValidationError(f"{label} must be a list of {minimum}..{maximum} strings")
    return [_string(item, label) for item in value]


def _environment(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > MAX_ENVIRONMENT:
        raise ProcedureValidationError(f"{label} must be a dictionary of at most {MAX_ENVIRONMENT} strings")
    return {_string(key, label, 128): _string(item, label, empty=True) for key, item in value.items()}


def _relative_parts(value: str) -> tuple[str, ...]:
    _string(value, "postcondition path", MAX_PATH_LENGTH)
    win = PureWindowsPath(value)
    if value.startswith(("/", "\\")) or win.drive or ":" in value:
        raise ProcedureValidationError("postcondition path must be workspace-relative")
    parts = tuple(value.replace("\\", "/").split("/"))
    for part in parts:
        if part in {"", ".", ".."} or part.endswith((" ", ".")):
            raise ProcedureValidationError("empty, dot, traversal or ambiguous path component")
        if any(ord(char) < 32 for char in part) or any(char in '<>"|?*' for char in part):
            raise ProcedureValidationError("invalid path component")
        if part.casefold().split(".", 1)[0] in _WINDOWS_DEVICES:
            raise ProcedureValidationError("device paths are forbidden")
        if part.casefold() in _CREDENTIAL_DIRS:
            raise ProcedureValidationError("sensitive credential paths are forbidden")
    return parts


def _template(value: str, names: set[str]) -> None:
    for match in Template.pattern.finditer(value):
        if match.group("invalid") is not None:
            raise ProcedureValidationError("invalid string.Template placeholder; escape literal dollars as $$")
        name = match.group("named") or match.group("braced")
        if name and name not in names:
            raise ProcedureValidationError("undeclared parameter placeholder")


def _validate(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProcedureValidationError("procedure must be a dictionary")
    if set(payload) - _FIELDS:
        raise ProcedureValidationError("unknown procedure fields")
    if {"goal", "steps", "postconditions"} - set(payload):
        raise ProcedureValidationError("goal, steps and postconditions are required")
    parameters = payload.get("parameters", {})
    if not isinstance(parameters, dict) or len(parameters) > MAX_PARAMETERS:
        raise ProcedureValidationError("parameters must be a bounded dictionary")
    defaults: dict[str, str | None] = {}
    for name, default in parameters.items():
        if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
            raise ProcedureValidationError("parameter names must be bounded ASCII identifiers")
        defaults[name] = None if default is None else _string(default, "parameter default", empty=True)
    goal = _string(payload["goal"], "goal", 1024)
    steps = _strings(payload["steps"], "steps", 1, 20)
    guards = _strings(payload.get("failure_guards", []), "failure_guards", 0, 20)
    conditions = payload["postconditions"]
    if not isinstance(conditions, list) or not 1 <= len(conditions) <= 16:
        raise ProcedureValidationError("postconditions must contain 1..16 checks")
    copied = []
    texts = [goal, *steps, *guards]
    for condition in conditions:
        if not isinstance(condition, dict):
            raise ProcedureValidationError("each postcondition must be a dictionary")
        kind = condition.get("type")
        if not isinstance(kind, str) or kind not in _KINDS:
            raise ProcedureValidationError("unsupported postcondition type")
        fields = {"type", "path"} if kind == "path_exists" else {"type", "path", "value"}
        if set(condition) != fields:
            raise ProcedureValidationError("missing or unknown postcondition fields")
        path = _string(condition["path"], "postcondition path", MAX_PATH_LENGTH)
        _relative_parts(path)
        item = {"type": kind, "path": path}
        texts.append(path)
        if kind != "path_exists":
            item["value"] = _string(condition["value"], "postcondition value")
            texts.append(item["value"])
        copied.append(item)
    for text in texts:
        _template(text, set(defaults))
    return {
        "goal": goal, "steps": steps, "failure_guards": guards,
        "parameters": defaults, "postconditions": copied,
        "environment": _environment(payload.get("environment", {}), "environment"),
    }


@dataclass(frozen=True)
class ProcedureSpec:
    goal: str
    steps: list[str]
    postconditions: list[dict[str, str]]
    failure_guards: list[str] = field(default_factory=list)
    parameters: dict[str, str | None] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in _validate(self._payload()).items():
            object.__setattr__(self, name, value)

    def _payload(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in _FIELDS}

    @classmethod
    def from_dict(cls, payload: Any) -> ProcedureSpec:
        return cls(**_validate(payload))

    def to_dict(self) -> dict[str, Any]:
        """Return detached containers and revalidate possible nested mutation."""
        return _validate(self._payload())

    def render(self, arguments: dict[str, str] | None = None) -> dict[str, Any]:
        """Render suggestions and checks without executing or evaluating text."""
        payload = self.to_dict()
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict) or set(arguments) - set(self.parameters):
            raise ProcedureValidationError("arguments must contain only declared parameters")
        values = {name: value for name, value in self.parameters.items() if value is not None}
        for name, value in arguments.items():
            values[name] = _string(value, "argument", empty=True)
        if set(self.parameters) - set(values):
            raise ProcedureValidationError("missing required parameter arguments")

        def substitute(text: str, limit: int = MAX_TEXT_LENGTH) -> str:
            return _string(Template(text).substitute(values), "rendered text", limit)

        payload["goal"] = substitute(payload["goal"], 1024)
        payload["steps"] = [substitute(text) for text in payload["steps"]]
        payload["failure_guards"] = [substitute(text) for text in payload["failure_guards"]]
        for condition in payload["postconditions"]:
            condition["path"] = substitute(condition["path"], MAX_PATH_LENGTH)
            if "value" in condition:
                condition["value"] = substitute(condition["value"])
        return payload

    def render_steps(self, arguments: dict[str, str] | None = None) -> list[str]:
        return self.render(arguments)["steps"]


def _overall(statuses: list[Status]) -> Status:
    if "fail" in statuses:
        return "fail"
    return "pass" if statuses and all(status == "pass" for status in statuses) else "unknown"


def check_environment(expected: dict[str, str], actual: dict[str, str] | None = None) -> EnvironmentResult:
    """Pure equality comparison of supplied conditions and supplied host facts.

    This function never probes the host. The caller must collect actual facts;
    missing facts are unknown, and additional actual keys are not checked.
    """
    expected = _environment(expected, "expected environment")
    actual = {} if actual is None else _environment(actual, "actual environment")
    checks: list[EnvironmentCheck] = []
    for key, value in expected.items():
        status: Status = "unknown" if key not in actual else "pass" if actual[key] == value else "fail"
        checks.append({"key": key, "expected": value, "actual": actual.get(key), "status": status})
    return {"overall": _overall([check["status"] for check in checks]), "checks": checks}


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _sensitive(path: Path) -> bool:
    if path == Path(path.anchor) or any(part.casefold() in _CREDENTIAL_DIRS for part in path.parts):
        return True
    if os.name == "nt":
        roots = [Path(path.anchor) / name for name in ("Windows", "Program Files", "Program Files (x86)", "ProgramData")]
        roots.extend(Path(value) for key in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData") if (value := os.environ.get(key)))
    else:
        # openKylin's /tmp resolves to /var/tmp. Explicit child workspaces
        # remain bounded by pinned handles; other /var state stays protected.
        temporary_root = Path("/var/tmp")
        if temporary_root in path.parents:
            return False
        roots = [Path(value) for value in _SYSTEM_ROOTS]
    return any(_within(path, root) for root in roots)


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _no_links(path: Path) -> None:
    for part in [*reversed(path.parents), path]:
        if _is_link(part.lstat()):
            raise ProcedureValidationError("symlinks and reparse points are forbidden")


def _workspace(value: Any) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise ProcedureValidationError("workspace must be explicitly supplied")
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise ProcedureValidationError("workspace must be an explicit local path")
    path = Path(raw)
    if PureWindowsPath(raw).drive.startswith("\\\\"):
        raise ProcedureValidationError("network workspaces are forbidden")
    if not path.is_absolute() or ".." in path.parts:
        raise ProcedureValidationError("workspace must be an absolute path without traversal")
    if _sensitive(path):
        raise ProcedureValidationError("sensitive system workspace is forbidden")
    try:
        _no_links(path)
        path = path.resolve(strict=True)
        if not path.is_dir() or _sensitive(path):
            raise ProcedureValidationError("workspace must be a non-sensitive directory")
    except (OSError, RuntimeError) as exc:
        raise ProcedureValidationError("workspace is unavailable") from exc
    return path


def _windows_handle_path(handle: int) -> Path:
    # Check the actual opened object, not just a pre-open pathname (TOCTOU).
    import ctypes
    from ctypes import wintypes

    function = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
    function.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    function.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    size = function(handle, buffer, len(buffer), 0)
    if not size or size >= len(buffer):
        raise OSError("cannot establish opened file location")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def _windows_opened_path(fd: int) -> Path:
    import msvcrt

    return _windows_handle_path(msvcrt.get_osfhandle(fd))


def _windows_exists_mode(root: Path, parts: tuple[str, ...]) -> int:
    """Inspect a pinned file/directory handle, without following final links."""
    import ctypes
    from ctypes import wintypes

    class FileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD), ("created", wintypes.FILETIME),
            ("accessed", wintypes.FILETIME), ("written", wintypes.FILETIME),
            ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    information = kernel.GetFileInformationByHandle
    information.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    information.restype = wintypes.BOOL
    file_type = kernel.GetFileType
    file_type.argtypes = [wintypes.HANDLE]
    file_type.restype = wintypes.DWORD
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    # READ_ATTRIBUTES, share read/write/delete, OPEN_EXISTING,
    # BACKUP_SEMANTICS (directories) and OPEN_REPARSE_POINT (no final follow).
    target = root.joinpath(*parts)
    handle = create(str(target), 0x80, 7, None, 3, 0x02000000 | 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = FileInformation()
        if not information(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & 0x400:
            raise ProcedureValidationError("reparse point evidence is forbidden")
        opened = _windows_handle_path(handle)
        if not _within(opened, root) or _sensitive(opened):
            raise ProcedureValidationError("opened object escaped workspace or is sensitive")
        _no_links(target)
        if file_type(handle) != 1:
            raise OSError("evidence is not a disk file or directory")
        return stat.S_IFDIR if info.attributes & 0x10 else stat.S_IFREG
    finally:
        close(handle)


def _open_local(root: Path, parts: tuple[str, ...]) -> int:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    if os.name != "nt":
        # Pin every parent directory and refuse links, including concurrent swaps.
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        parent = os.open(root.anchor, directory_flags)
        try:
            for component in (*root.parts[1:], *parts[:-1]):
                child = os.open(component, directory_flags, dir_fd=parent)
                os.close(parent)
                parent = child
            return os.open(parts[-1], flags | os.O_NOFOLLOW, dir_fd=parent)
        finally:
            os.close(parent)
    fd = os.open(root.joinpath(*parts), flags)
    try:
        opened = _windows_opened_path(fd)
        if not _within(opened, root) or _sensitive(opened):
            raise ProcedureValidationError("opened file escaped workspace or is sensitive")
        _no_links(root.joinpath(*parts))
        return fd
    except BaseException:
        os.close(fd)
        raise


def _existence_mode(root: Path, parts: tuple[str, ...]) -> int:
    if os.name == "nt":
        return _windows_exists_mode(root, parts)
    fd = _open_local(root, parts)
    try:
        return os.fstat(fd).st_mode
    finally:
        os.close(fd)


def _check(condition: dict[str, str], root: Path) -> CheckResult:
    result: CheckResult = {"type": condition["type"], "path": condition["path"], "status": "unknown", "reason": "unavailable"}
    try:
        parts = _relative_parts(condition["path"])
        target = root.joinpath(*parts)
        if _sensitive(target):
            raise ProcedureValidationError("sensitive system path is forbidden")
        _no_links(target)
        if not _within(target.resolve(strict=True), root):
            raise ProcedureValidationError("path escaped workspace")
        if condition["type"] == "path_exists":
            mode = _existence_mode(root, parts)
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                result["reason"] = "not_regular_file_or_directory"
                return result
            result.update(status="pass", reason="exists")
            return result
        fd = _open_local(root, parts)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                result["reason"] = "not_regular_file"
                return result
            if before.st_size > MAX_FILE_BYTES:
                result["reason"] = "file_too_large"
                return result
            data = stream.read(MAX_FILE_BYTES)
            after = os.fstat(stream.fileno())
        if after.st_size > MAX_FILE_BYTES:
            result["reason"] = "file_too_large"
            return result
        if len(data) != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            result["reason"] = "file_changed_during_read"
            return result
        text = data.decode("utf-8")
        contains = condition["value"] in text
        passed = contains if condition["type"] == "file_contains" else not contains
        result.update(status="pass" if passed else "fail", reason="matched" if passed else "not_matched")
    except ProcedureValidationError:
        result.update(status="fail", reason="unsafe_path")
    except (FileNotFoundError, NotADirectoryError):
        result.update(status="fail", reason="missing_path")
    except UnicodeError:
        result["reason"] = "not_utf8_text"
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            result.update(status="fail", reason="unsafe_path")
        else:
            result["reason"] = "unavailable"
    except RuntimeError:
        result["reason"] = "unavailable"
    return result


def verify_postconditions(
    spec: ProcedureSpec | dict[str, Any],
    workspace: str | os.PathLike[str],
    arguments: dict[str, str] | None = None,
) -> VerificationResult:
    """Read local evidence only; caller assertions and environment are not proof.

    Invalid schema/arguments/workspace raise ProcedureValidationError. Unsafe
    rendered targets fail; inaccessible, non-text or oversized evidence is
    unknown. All checks must pass for overall pass. Links are conservatively
    denied even when they currently resolve inside the workspace.
    """
    spec = spec if isinstance(spec, ProcedureSpec) else ProcedureSpec.from_dict(spec)
    rendered = spec.render(arguments)
    root = _workspace(workspace)
    checks = [_check(condition, root) for condition in rendered["postconditions"]]
    return {"overall": _overall([check["status"] for check in checks]), "checks": checks, "rendered_steps": rendered["steps"]}
