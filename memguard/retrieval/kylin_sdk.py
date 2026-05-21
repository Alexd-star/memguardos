from __future__ import annotations

from ctypes import CDLL, POINTER, byref, c_bool, c_char_p, c_float, c_int, c_void_p
from ctypes.util import find_library
from pathlib import Path
from typing import Dict, Optional
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request


EMBEDDING_HEADERS = [
    "/usr/include/kylin-ai/coreai/embedding/embedding.h",
    "/usr/include/kylin-ai/plugins/ai-engines/ondeviceembeddingengine.h",
]
VECTOR_HEADERS = [
    "/usr/include/kysdk-vector-engine-client/Database.h",
]
RUNTIME_SOCKET_NAMES = [
    "core-textembedding.sock",
    "config.sock",
]
AI_ENGINE_PLUGIN_DIRS = [
    "/usr/lib/kylin-ai/plugins/ai-engines",
    "/usr/lib/x86_64-linux-gnu/kylin-ai/plugins/ai-engines",
    "/usr/lib/aarch64-linux-gnu/kylin-ai/plugins/ai-engines",
    "/usr/lib/loongarch64-linux-gnu/kylin-ai/plugins/ai-engines",
]
EMBEDDING_PACKAGES = [
    "kylin-ai-runtime",
    "kylin-ai-engine-plugins",
    "libkylin-coreai-embedding",
    "libkylin-coreai-embedding-dev",
    "libkylin-ondevice-embedding-engine",
    "libkylin-ondevice-embedding-engine-dev",
    "libkylin-ondevice-ai-engine-plugin",
    "libkylin-ondevice-traditional-ai-engine-plugin",
]
KYTENSOR_MODEL_REPOSITORIES = [
    "/opt/appdata/kylin-ai/model-repository",
    "/usr/share/kylin-ai/model-repository",
]
TEXT_EMBEDDING_MODELS = [
    "ensemble-embd_gte-base_uint8-text",
    "embd_gte-base_uint8-text",
    "tokenizer_gte-base_uint8-text",
]


def kylin_sdk_status(probe_embedding: bool = False, probe_timeout: float = 45.0) -> Dict[str, object]:
    embedding = KylinEmbeddingBackend()
    vector = KylinVectorStoreProbe()
    result = {
        "embedding": embedding.status(probe=probe_embedding, probe_timeout=probe_timeout),
        "vector_store": vector.status(),
    }
    return result


class KylinEmbeddingBackend:
    """Optional ctypes adapter for openKylin's Kylin AI text embedding SDK."""

    def __init__(self, library_name: str = "libkysdk-coreai-embedding.so"):
        self.library_name = library_name

    def status(self, probe: bool = False, probe_timeout: float = 45.0) -> Dict[str, object]:
        lib_path = find_library("kysdk-coreai-embedding") or self.library_name
        lib_file = self._resolve_library_file(lib_path)
        headers = {path: Path(path).exists() for path in EMBEDDING_HEADERS}
        report: Dict[str, object] = {
            "backend": "kylin_coreai_embedding",
            "library": lib_path,
            "library_file": lib_file,
            "library_loadable": False,
            "headers": headers,
            "runtime": self._runtime_status(),
            "kytensor": self._kytensor_status(),
            "plugins": self._plugin_status(),
            "packages": self._package_status(EMBEDDING_PACKAGES),
            "symbols": self._library_symbols(lib_file or lib_path),
            "session": None,
            "model_info": None,
            "embedding_probe": None,
            "available": False,
            "inference_ready": None if not probe else False,
            "ready_for_default_retrieval": False,
            "fallback": "hash_embedding",
            "diagnosis": {
                "success_condition": "library_loadable and model_info and embedding_probe.ok",
                "availability_meaning": "available means SDK metadata is reachable; inference_ready means a real embedding call succeeded.",
                "timeout_hint": "If model_info is present but embedding_probe returns 'Timeout was reached', inspect kylin-ai-runtime sockets, kytensor.service, Triton repository index, ondevice plugin logs, and model repository links.",
            },
        }
        try:
            lib = self._load_library(lib_path)
            report["library_loadable"] = True
            info, session_report = self.model_info_with_session_report(lib)
            report["session"] = session_report
            report["model_info"] = info
            report["available"] = bool(info)
            if probe:
                report["embedding_probe"] = self.embed_in_subprocess("openKylin agent memory", probe_timeout, include_vector=False)
                report["inference_ready"] = bool(isinstance(report["embedding_probe"], dict) and report["embedding_probe"].get("ok"))
                report["ready_for_default_retrieval"] = bool(report["inference_ready"])
        except Exception as exc:  # pragma: no cover - platform-dependent SDK path
            report["error"] = str(exc)
        return report

    def model_info(self, lib: Optional[CDLL] = None) -> Optional[Dict[str, object]]:
        info, _ = self.model_info_with_session_report(lib)
        return info

    def model_info_with_session_report(self, lib: Optional[CDLL] = None) -> tuple[Optional[Dict[str, object]], Dict[str, object]]:
        lib = lib or self._load_library()
        session, init_code = self._create_initialized_session(lib)
        report: Dict[str, object] = {
            "created": bool(session.value),
            "init_code": init_code,
            "init_ok": init_code == 0,
            "init_code_hint": self._embedding_error_hint(init_code),
        }
        if not session.value:
            return None, report
        if init_code != 0:
            self._destroy_session(lib, session)
            return None, report
        try:
            raw = c_char_p()
            ok = lib.text_embedding_get_model_info(session, byref(raw))
            if not ok or not raw.value:
                report["model_info_ok"] = False
                return None, report
            text = raw.value.decode("utf-8", "replace")
            lib.embedding_model_info_destroy(raw)
            report["model_info_ok"] = True
            try:
                return json.loads(text), report
            except json.JSONDecodeError:
                return {"raw": text}, report
        finally:
            self._destroy_session(lib, session)

    def embed(self, text: str, lib: Optional[CDLL] = None, raise_on_error: bool = True) -> Dict[str, object]:
        lib = lib or self._load_library()
        session, init_code = self._create_initialized_session(lib)
        if not session.value:
            result = {"ok": False, "error": "failed to create text embedding session"}
            if raise_on_error:
                raise RuntimeError(result["error"])
            return result
        if init_code != 0:
            result = {
                "ok": False,
                "error": "failed to initialize text embedding session",
                "init_code": init_code,
                "init_code_hint": self._embedding_error_hint(init_code),
            }
            if raise_on_error:
                raise RuntimeError(result["error"])
            self._destroy_session(lib, session)
            return result
        try:
            result_ptr = c_void_p()
            ok = lib.text_embedding(session, text.encode("utf-8"), byref(result_ptr))
            if not ok or not result_ptr.value:
                result = {"ok": False, "error": "text_embedding returned no result"}
                if raise_on_error:
                    raise RuntimeError(result["error"])
                return result
            try:
                error_code = lib.embedding_result_get_error_code(result_ptr)
                error_message_ptr = lib.embedding_result_get_error_message(result_ptr)
                error_message = error_message_ptr.decode("utf-8", "replace") if error_message_ptr else ""
                length = lib.embedding_result_get_vector_length(result_ptr)
                data = lib.embedding_result_get_vector_data(result_ptr)
                if error_code != 0 or length <= 0 or not data:
                    result = {
                        "ok": False,
                        "error_code": error_code,
                        "error_message": error_message,
                        "length": length,
                    }
                    if raise_on_error:
                        raise RuntimeError(error_message or "Kylin embedding failed")
                    return result
                vector = [float(data[idx]) for idx in range(length)]
                return {
                    "ok": True,
                    "dim": length,
                    "sample": [round(value, 6) for value in vector[:5]],
                    "vector": vector,
                }
            finally:
                lib.embedding_result_destroy(byref(result_ptr))
        finally:
            self._destroy_session(lib, session)

    @staticmethod
    def embed_in_subprocess(text: str, timeout_seconds: float, include_vector: bool = True) -> Dict[str, object]:
        code = """
import json
from memguard.retrieval.kylin_sdk import KylinEmbeddingBackend

try:
    result = KylinEmbeddingBackend().embed(%r, raise_on_error=False)
except Exception as exc:
    result = {"ok": False, "error": str(exc)}
if not %r:
    result.pop("vector", None)
print(json.dumps(result, ensure_ascii=False))
""" % (text, include_vector)
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                text=True,
                capture_output=True,
                timeout=max(1.0, timeout_seconds),
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "ok": False,
                "error": "embedding probe subprocess timeout",
                "timeout_seconds": timeout_seconds,
                "stdout_tail": (exc.stdout or "")[-1000:] if isinstance(exc.stdout, str) else "",
                "stderr_tail": (exc.stderr or "")[-1000:] if isinstance(exc.stderr, str) else "",
            }
        output = proc.stdout.strip().splitlines()
        parsed = None
        if output:
            try:
                parsed = json.loads(output[-1])
            except json.JSONDecodeError:
                parsed = None
        if isinstance(parsed, dict):
            parsed["subprocess_returncode"] = proc.returncode
            if proc.stderr.strip():
                parsed["stderr_tail"] = proc.stderr.strip()[-1000:]
            return parsed
        return {
            "ok": False,
            "error": "embedding probe subprocess returned non-json output",
            "subprocess_returncode": proc.returncode,
            "stdout_tail": proc.stdout.strip()[-1000:],
            "stderr_tail": proc.stderr.strip()[-1000:],
        }

    def _load_library(self, lib_path: Optional[str] = None) -> CDLL:
        lib = CDLL(lib_path or self.library_name)
        lib.text_embedding_create_session.restype = c_void_p
        lib.text_embedding_init_session.argtypes = [c_void_p]
        lib.text_embedding_init_session.restype = c_int
        lib.text_embedding_destroy_session.argtypes = [POINTER(c_void_p)]
        lib.text_embedding_destroy_session.restype = None
        lib.text_embedding_get_model_info.argtypes = [c_void_p, POINTER(c_char_p)]
        lib.text_embedding_get_model_info.restype = c_bool
        lib.embedding_model_info_destroy.argtypes = [c_char_p]
        lib.embedding_model_info_destroy.restype = None
        lib.text_embedding.argtypes = [c_void_p, c_char_p, POINTER(c_void_p)]
        lib.text_embedding.restype = c_bool
        lib.embedding_result_get_error_code.argtypes = [c_void_p]
        lib.embedding_result_get_error_code.restype = c_int
        lib.embedding_result_get_error_message.argtypes = [c_void_p]
        lib.embedding_result_get_error_message.restype = c_char_p
        lib.embedding_result_get_vector_length.argtypes = [c_void_p]
        lib.embedding_result_get_vector_length.restype = c_int
        lib.embedding_result_get_vector_data.argtypes = [c_void_p]
        lib.embedding_result_get_vector_data.restype = POINTER(c_float)
        lib.embedding_result_destroy.argtypes = [POINTER(c_void_p)]
        lib.embedding_result_destroy.restype = None
        return lib

    @staticmethod
    def _create_initialized_session(lib: CDLL) -> tuple[c_void_p, Optional[int]]:
        session = lib.text_embedding_create_session()
        init_code: Optional[int] = None
        if session:
            init_code = int(lib.text_embedding_init_session(session))
        return c_void_p(session), init_code

    @staticmethod
    def _destroy_session(lib: CDLL, session: c_void_p) -> None:
        holder = c_void_p(session.value)
        lib.text_embedding_destroy_session(byref(holder))

    @staticmethod
    def _embedding_error_hint(code: Optional[int]) -> str:
        hints = {
            None: "session was not created",
            0: "success",
            1: "input error",
            2: "init error; inspect model loading and ondevice engine plugins",
            3: "runtime connection error; recreate session after checking kylin-ai-runtime",
            4: "runtime error; inspect engine callback and model logs",
            99: "unknown Kylin embedding SDK error",
        }
        return hints.get(code, "unknown SDK init code; inspect kylin-ai-runtime logs")

    @staticmethod
    def _runtime_base_dir() -> str:
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        return f"/tmp/.kylin-ai-runtime-unix/{uid}"

    def _runtime_status(self) -> Dict[str, object]:
        base = self._runtime_base_dir()
        sockets = {name: f"{base}/{name}" for name in RUNTIME_SOCKET_NAMES}
        return {
            "binary": shutil.which("kylin-ai-runtime"),
            "user_service": self._user_service_state("kylin-ai-runtime.service"),
            "base_dir": base,
            "base_dir_exists": Path(base).exists(),
            "sockets": sockets,
            "socket_exists": {name: Path(path).exists() for name, path in sockets.items()},
            "session_bus": bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS")),
            "xdg_runtime_dir": os.environ.get("XDG_RUNTIME_DIR", ""),
        }

    @staticmethod
    def _kytensor_status() -> Dict[str, object]:
        repositories = []
        expected = set(TEXT_EMBEDDING_MODELS)
        for raw_path in KYTENSOR_MODEL_REPOSITORIES:
            path = Path(raw_path)
            entries = []
            if path.exists() and path.is_dir():
                try:
                    entries = sorted(child.name for child in path.iterdir())
                except OSError:
                    entries = []
            repositories.append(
                {
                    "path": raw_path,
                    "exists": path.exists(),
                    "entries": entries[:80],
                    "has_text_embedding_models": expected.issubset(set(entries)),
                }
            )
        return {
            "service": KylinEmbeddingBackend._system_service_state("kytensor.service"),
            "repository_index": KylinEmbeddingBackend._triton_repository_index(),
            "repositories": repositories,
            "expected_text_embedding_models": TEXT_EMBEDDING_MODELS,
            "fix_hint": "If /opt/appdata/kylin-ai/model-repository is empty while /usr/share/kylin-ai/model-repository has models, run scripts/fix_kylin_embedding_runtime.sh to link the repositories and restart kytensor/runtime.",
        }

    @staticmethod
    def _triton_repository_index() -> Dict[str, object]:
        request = urllib.request.Request("http://127.0.0.1:8000/v2/repository/index", method="POST")
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                raw = response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {"available": False, "ok": False, "error": str(exc)}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"available": True, "ok": False, "raw": raw[:1000]}
        models = [item.get("name") for item in parsed if isinstance(item, dict)]
        expected = set(TEXT_EMBEDDING_MODELS)
        return {
            "available": True,
            "ok": True,
            "models": models,
            "has_text_embedding_models": expected.issubset(set(models)),
        }

    @staticmethod
    def _plugin_status() -> Dict[str, object]:
        dirs = []
        for raw_dir in AI_ENGINE_PLUGIN_DIRS:
            path = Path(raw_dir)
            entries = sorted(child.name for child in path.glob("*.so")) if path.exists() else []
            dirs.append(
                {
                    "path": raw_dir,
                    "exists": path.exists(),
                    "shared_objects": entries,
                    "has_ondevice_plugin": any("ondevice" in entry for entry in entries),
                }
            )
        return {"dirs": dirs}

    @staticmethod
    def _package_status(packages: list[str]) -> Dict[str, object]:
        if not shutil.which("dpkg-query"):
            return {"available": False}
        status: Dict[str, object] = {"available": True, "packages": {}}
        for package in packages:
            try:
                proc = subprocess.run(
                    ["dpkg-query", "-W", "-f=${Status} ${Version}", package],
                    text=True,
                    capture_output=True,
                    timeout=3,
                )
                installed = proc.returncode == 0 and "install ok installed" in proc.stdout
                status["packages"][package] = {
                    "installed": installed,
                    "raw": (proc.stdout or proc.stderr).strip(),
                }
            except Exception as exc:  # pragma: no cover - platform-dependent package path
                status["packages"][package] = {"installed": False, "error": str(exc)}
        return status

    @staticmethod
    def _library_symbols(lib_path: str) -> Dict[str, object]:
        nm = shutil.which("nm")
        if not nm:
            return {"available": False}
        if not lib_path or ("/" in lib_path and not Path(lib_path).exists()):
            return {"available": True, "ok": False, "error": f"library file not found: {lib_path}"}
        if "/" not in lib_path:
            return {"available": True, "ok": False, "error": f"library file path unresolved: {lib_path}"}
        try:
            proc = subprocess.run([nm, "-D", lib_path], text=True, capture_output=True, timeout=5)
        except Exception as exc:  # pragma: no cover - platform-dependent SDK path
            return {"available": True, "error": str(exc)}
        symbols = [
            "text_embedding_create_session",
            "text_embedding_init_session",
            "text_embedding_get_model_info",
            "text_embedding",
            "embedding_result_get_vector_length",
            "embedding_result_get_vector_data",
        ]
        output = proc.stdout + proc.stderr
        return {
            "available": True,
            "ok": proc.returncode == 0,
            "symbols": {symbol: symbol in output for symbol in symbols},
        }

    @staticmethod
    def _resolve_library_file(lib_name: str) -> str:
        if not lib_name:
            return ""
        path = Path(lib_name)
        if path.is_absolute() and path.exists():
            return str(path)
        if shutil.which("ldconfig"):
            try:
                proc = subprocess.run(["ldconfig", "-p"], text=True, capture_output=True, timeout=5)
                for line in proc.stdout.splitlines():
                    if lib_name in line and "=>" in line:
                        candidate = line.split("=>", 1)[1].strip()
                        if candidate and Path(candidate).exists():
                            return candidate
            except Exception:
                pass
        for directory in ["/usr/lib", "/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu", "/usr/lib/loongarch64-linux-gnu", "/lib", "/lib/x86_64-linux-gnu"]:
            for candidate in Path(directory).glob(f"{lib_name}*") if Path(directory).exists() else []:
                if candidate.is_file():
                    return str(candidate)
        return ""

    @staticmethod
    def _user_service_state(unit: str) -> Dict[str, object]:
        if not shutil.which("systemctl"):
            return {"available": False}
        command = ["systemctl", "--user", "is-active", unit]
        try:
            proc = subprocess.run(command, text=True, capture_output=True, timeout=3)
            return {
                "available": True,
                "active": proc.returncode == 0,
                "state": proc.stdout.strip() or proc.stderr.strip(),
            }
        except Exception as exc:  # pragma: no cover - platform-dependent systemd path
            return {"available": True, "active": False, "error": str(exc)}

    @staticmethod
    def _system_service_state(unit: str) -> Dict[str, object]:
        if not shutil.which("systemctl"):
            return {"available": False}
        try:
            active = subprocess.run(["systemctl", "is-active", unit], text=True, capture_output=True, timeout=3)
            enabled = subprocess.run(["systemctl", "is-enabled", unit], text=True, capture_output=True, timeout=3)
            return {
                "available": True,
                "active": active.returncode == 0,
                "state": active.stdout.strip() or active.stderr.strip(),
                "enabled": enabled.stdout.strip() or enabled.stderr.strip(),
            }
        except Exception as exc:  # pragma: no cover - platform-dependent systemd path
            return {"available": True, "active": False, "error": str(exc)}


class KylinVectorStoreProbe:
    """Detects openKylin's vector database SDK and service without forcing it as default."""

    def status(self) -> Dict[str, object]:
        headers = {path: Path(path).exists() for path in VECTOR_HEADERS}
        lib_path = find_library("kysdk-vector-engine-client") or "libkysdk-vector-engine-client.so"
        service_socket = self._vector_socket()
        report = {
            "backend": "kysdk_vector_engine_client",
            "library": lib_path,
            "library_loadable": False,
            "headers": headers,
            "binary": shutil.which("kylin-ai-vector-engine"),
            "default_binary": shutil.which("kylin-ai-vector-engine-default"),
            "user_service": self._user_service_state("kylin-ai-vector-engine.service"),
            "socket": service_socket,
            "socket_exists": Path(service_socket).exists() if service_socket else False,
            "available": False,
            "fallback": "sqlite_fts_hash_vector",
        }
        try:
            CDLL(lib_path)
            report["library_loadable"] = True
        except Exception as exc:  # pragma: no cover - platform-dependent SDK path
            report["error"] = str(exc)
        report["available"] = bool(report["library_loadable"] and report["socket_exists"])
        return report

    @staticmethod
    def _vector_socket() -> str:
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        return "/tmp/kylin-ai-vector-engine-%s.sock" % uid

    @staticmethod
    def _user_service_state(unit: str) -> Dict[str, object]:
        if not shutil.which("systemctl"):
            return {"available": False}
        command = ["systemctl", "--user", "is-active", unit]
        try:
            proc = subprocess.run(command, text=True, capture_output=True, timeout=3)
            return {
                "available": True,
                "active": proc.returncode == 0,
                "state": proc.stdout.strip() or proc.stderr.strip(),
            }
        except Exception as exc:  # pragma: no cover - platform-dependent systemd path
            return {"available": True, "active": False, "error": str(exc)}
