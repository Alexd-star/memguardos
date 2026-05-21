from __future__ import annotations

from ctypes import CDLL, POINTER, byref, c_bool, c_char_p, c_float, c_int, c_void_p
from ctypes.util import find_library
from pathlib import Path
from typing import Dict, Optional
import json
import os
import shutil
import subprocess


EMBEDDING_HEADERS = [
    "/usr/include/kylin-ai/coreai/embedding/embedding.h",
    "/usr/include/kylin-ai/plugins/ai-engines/ondeviceembeddingengine.h",
]
VECTOR_HEADERS = [
    "/usr/include/kysdk-vector-engine-client/Database.h",
]


def kylin_sdk_status(probe_embedding: bool = False) -> Dict[str, object]:
    embedding = KylinEmbeddingBackend()
    vector = KylinVectorStoreProbe()
    result = {
        "embedding": embedding.status(probe=probe_embedding),
        "vector_store": vector.status(),
    }
    return result


class KylinEmbeddingBackend:
    """Optional ctypes adapter for openKylin's Kylin AI text embedding SDK."""

    def __init__(self, library_name: str = "libkysdk-coreai-embedding.so"):
        self.library_name = library_name

    def status(self, probe: bool = False) -> Dict[str, object]:
        lib_path = find_library("kysdk-coreai-embedding") or self.library_name
        headers = {path: Path(path).exists() for path in EMBEDDING_HEADERS}
        report: Dict[str, object] = {
            "backend": "kylin_coreai_embedding",
            "library": lib_path,
            "library_loadable": False,
            "headers": headers,
            "model_info": None,
            "embedding_probe": None,
            "available": False,
            "fallback": "hash_embedding",
        }
        try:
            lib = self._load_library(lib_path)
            report["library_loadable"] = True
            info = self.model_info(lib)
            report["model_info"] = info
            report["available"] = bool(info)
            if probe:
                report["embedding_probe"] = self.embed("openKylin agent memory", lib=lib, raise_on_error=False)
        except Exception as exc:  # pragma: no cover - platform-dependent SDK path
            report["error"] = str(exc)
        return report

    def model_info(self, lib: Optional[CDLL] = None) -> Optional[Dict[str, object]]:
        lib = lib or self._load_library()
        session = self._create_session(lib)
        if not session:
            return None
        try:
            raw = c_char_p()
            ok = lib.text_embedding_get_model_info(session, byref(raw))
            if not ok or not raw.value:
                return None
            text = raw.value.decode("utf-8", "replace")
            lib.embedding_model_info_destroy(raw)
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}
        finally:
            self._destroy_session(lib, session)

    def embed(self, text: str, lib: Optional[CDLL] = None, raise_on_error: bool = True) -> Dict[str, object]:
        lib = lib or self._load_library()
        session = self._create_session(lib)
        if not session:
            result = {"ok": False, "error": "failed to create text embedding session"}
            if raise_on_error:
                raise RuntimeError(result["error"])
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

    def _load_library(self, lib_path: Optional[str] = None) -> CDLL:
        lib = CDLL(lib_path or self.library_name)
        lib.text_embedding_create_session.restype = c_void_p
        lib.text_embedding_init_session.argtypes = [c_void_p]
        lib.text_embedding_init_session.restype = c_int
        lib.text_embedding_destroy_session.argtypes = [POINTER(c_void_p)]
        lib.text_embedding_get_model_info.argtypes = [c_void_p, POINTER(c_char_p)]
        lib.text_embedding_get_model_info.restype = c_bool
        lib.embedding_model_info_destroy.argtypes = [c_char_p]
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
        return lib

    @staticmethod
    def _create_session(lib: CDLL) -> c_void_p:
        session = lib.text_embedding_create_session()
        if session:
            lib.text_embedding_init_session(session)
        return c_void_p(session)

    @staticmethod
    def _destroy_session(lib: CDLL, session: c_void_p) -> None:
        holder = c_void_p(session.value)
        lib.text_embedding_destroy_session(byref(holder))


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
