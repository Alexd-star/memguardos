from __future__ import annotations

from pathlib import Path
from typing import Dict, List
import json
import os
import platform
import shutil
import subprocess

from memguard.config import namespace_status, normalize_namespace
from memguard.os_guard.service_control import service_resource_status
from memguard.os_guard.unix_socket_trust import inspect_unix_socket
from memguard.retrieval.kylin_sdk import kylin_sdk_status


def openkylin_native_status(
    namespace: str | None = None,
    probe_sdk: bool = False,
    probe_timeout: float = 45.0,
    fast: bool = False,
) -> Dict[str, object]:
    namespace = normalize_namespace(namespace)
    services = _service_status(["memguardd.service", "kylin-ai-vector-engine.service", "kytensor.service"])
    sdk = _fast_sdk_status(services) if fast and not probe_sdk else kylin_sdk_status(probe_embedding=probe_sdk, probe_timeout=probe_timeout)
    _augment_embedding_product_state(sdk["embedding"])
    _augment_vector_product_state(sdk["vector_store"])
    desktop = _desktop_status()
    security = _security_status()
    resources = _resource_status(namespace)
    system = _system_status()
    embedding_ready = sdk["embedding"].get("component_ready")
    components = [
        _component("systemd", services["systemd"]["available"], services["systemd"]["active"], "systemd 服务管理"),
        _component("auditd", security["auditd"]["available"], security["auditd"]["rules_tool_available"], "审计规则工具"),
        _component(
            "kylin_embedding",
            sdk["embedding"].get("available"),
            embedding_ready,
            "Kylin embedding SDK",
            user_message=str(sdk["embedding"].get("user_message", "")),
            next_action=str(sdk["embedding"].get("next_action", "")),
            capability_state=str(sdk["embedding"].get("capability_state", "")),
        ),
        _component(
            "kylin_vector",
            sdk["vector_store"].get("available"),
            sdk["vector_store"].get("component_ready"),
            "Kylin vector SDK",
            user_message=str(sdk["vector_store"].get("user_message", "")),
            next_action=str(sdk["vector_store"].get("next_action", "")),
            capability_state=str(sdk["vector_store"].get("capability_state", "")),
        ),
        _component("peony", desktop["peony"]["available"], desktop["peony"]["plugin_installed"], "Peony 右键插件"),
        _component("ukui_control_center", desktop["ukui_control_center"]["available"], desktop["ukui_control_center"]["plugin_installed"], "UKUI 控制中心插件"),
        _component("cgroup_v2", resources["cgroup_v2"], resources["cgroup_v2"], "cgroup v2 资源治理"),
    ]
    recommendations = _recommendations(components, sdk=sdk, desktop=desktop, security=security)
    return {
        "schema": "memguardos-openkylin-native-status-v1",
        "system": system,
        "services": services,
        "sdk": sdk,
        "desktop": desktop,
        "security": security,
        "resources": resources,
        "namespace": namespace_status(namespace),
        "summary": _summary(components),
        "components": components,
        "recommendations": recommendations,
    }


def _fast_sdk_status(services: Dict[str, object]) -> Dict[str, object]:
    uid = os.getuid() if hasattr(os, "getuid") else 1000
    embedding_library_candidates = [
        Path("/lib/x86_64-linux-gnu/libkysdk-coreai-embedding.so.1"),
        Path("/usr/lib/x86_64-linux-gnu/libkysdk-coreai-embedding.so.1"),
        Path("/lib/aarch64-linux-gnu/libkysdk-coreai-embedding.so.1"),
        Path("/usr/lib/aarch64-linux-gnu/libkysdk-coreai-embedding.so.1"),
        Path("/lib/loongarch64-linux-gnu/libkysdk-coreai-embedding.so.1"),
        Path("/usr/lib/loongarch64-linux-gnu/libkysdk-coreai-embedding.so.1"),
    ]
    embedding_headers = {
        "/usr/include/kylin-ai/coreai/embedding/embedding.h": Path("/usr/include/kylin-ai/coreai/embedding/embedding.h").exists(),
        "/usr/include/kylin-ai/plugins/ai-engines/ondeviceembeddingengine.h": Path("/usr/include/kylin-ai/plugins/ai-engines/ondeviceembeddingengine.h").exists(),
    }
    repositories = [
        Path("/opt/appdata/kylin-ai/model-repository"),
        Path("/usr/share/kylin-ai/model-repository"),
    ]
    text_models = [
        "ensemble-embd_gte-base_uint8-text",
        "embd_gte-base_uint8-text",
        "tokenizer_gte-base_uint8-text",
    ]
    model_entries = sorted({entry.name for repo in repositories if repo.exists() for entry in repo.iterdir()})
    has_text_models = any(name in model_entries for name in text_models)
    library_file = next((str(path) for path in embedding_library_candidates if path.exists()), "")
    warmup_state = _read_kylin_warmup_state()
    warmup_ready = _warmup_state_is_ready(warmup_state)
    runtime_base = Path(_embedding_runtime_base(uid))
    runtime_sockets = {
        "core-textembedding.sock": str(runtime_base / "core-textembedding.sock"),
        "config.sock": str(runtime_base / "config.sock"),
    }
    runtime_trust = {
        name: inspect_unix_socket(
            path,
            expected_uid=uid,
            timeout=0.2,
            root_user_runtime=True,
        )
        for name, path in runtime_sockets.items()
    }
    runtime_trusted = bool(runtime_trust) and all(item["trusted"] for item in runtime_trust.values())
    runtime_failures = [
        f"{name}: {item['reason']}"
        for name, item in runtime_trust.items()
        if not item["trusted"]
    ]
    runtime_warnings = [
        f"{name}: {item['reason']}"
        for name, item in runtime_trust.items()
        if item["trusted"] and item["parent_directory_warning"]
    ]
    runtime_reason = "; ".join(runtime_failures or runtime_warnings) if (runtime_failures or runtime_warnings) else "Kylin runtime Unix socket 全部可信"
    vector_socket = Path(_vector_socket_path(uid))
    vector_trust = inspect_unix_socket(
        str(vector_socket),
        expected_uid=uid,
        timeout=0.2,
        root_user_runtime=True,
    )
    vector_binary = Path("/usr/bin/kylin-ai-vector-engine")
    vector_header = Path("/usr/include/kysdk-vector-engine-client/Database.h")
    embedding_installed = bool(library_file or any(embedding_headers.values()) or has_text_models)
    embedding_available = bool(embedding_installed and runtime_trusted)
    if embedding_installed and not runtime_trusted:
        capability_state = "runtime_untrusted"
        user_message = "已检测到 SDK，但运行时 socket 未通过信任校验"
        next_action = "已禁用端侧 SDK 并使用 hash fallback；请修复 socket owner、类型或对端身份"
    elif has_text_models and embedding_available:
        if warmup_ready:
            capability_state = "recently_ready"
            user_message = "已安装，最近一次端侧向量预热成功"
            next_action = "可继续使用；需要复核时点击“检查端侧向量能力”"
        else:
            capability_state = "installed_unwarmed"
            user_message = "已安装，当前未预热"
            next_action = "首次使用可能稍慢，可点击“预热 Kylin SDK”"
    elif embedding_available:
        capability_state = "metadata_incomplete"
        user_message = "已检测到部分 SDK 文件，模型或运行时信息不完整"
        next_action = "进入端侧能力页运行检查"
    else:
        capability_state = "not_installed"
        user_message = "未检测到 Kylin embedding SDK"
        next_action = "可继续使用稳定检索路径，或安装 openKylin AI 组件"
    product_state = _embedding_product_state(
        installed=bool(has_text_models and embedding_installed),
        warmup_state=warmup_state,
        fallback="hash_embedding",
    )
    vector_service = services.get("kylin-ai-vector-engine.service", {}) if isinstance(services.get("kylin-ai-vector-engine.service"), dict) else {}
    vector_socket_exists = bool(vector_trust["exists"])
    vector_socket_ready = bool(vector_trust["socket_ready"])
    vector_available = vector_binary.exists() or vector_header.exists() or vector_socket_ready
    if vector_socket_ready:
        vector_state = "socket_ready"
        vector_message = "向量服务接口可连接"
        vector_action = "可继续使用；如需服务管理状态，可在系统维护中刷新服务"
    elif vector_service.get("active"):
        vector_state = "service_active_socket_untrusted"
        vector_message = "向量服务正在运行，但 socket 未通过信任校验"
        vector_action = "保持 fallback；请检查 socket 类型、owner、连接与 SO_PEERCRED"
    elif vector_available:
        vector_state = "metadata_available"
        vector_message = "已检测到向量组件，服务接口未连接"
        vector_action = "需要端侧向量库时重启 Kylin vector engine"
    else:
        vector_state = "not_installed"
        vector_message = "未检测到 Kylin vector SDK"
        vector_action = "系统将继续使用 SQLite FTS 与本地向量 fallback"
    return {
        "embedding": {
            "backend": "kylin_coreai_embedding",
            "status_mode": "fast",
            "library": "libkysdk-coreai-embedding.so.1",
            "library_file": library_file,
            "library_loadable": None,
            "headers": embedding_headers,
            "runtime": {
                "binary": "/usr/bin/kylin-ai-runtime",
                "binary_exists": Path("/usr/bin/kylin-ai-runtime").exists(),
                "base_dir": str(runtime_base),
                "base_dir_exists": runtime_base.exists(),
                "sockets": runtime_sockets,
                "socket_exists": {name: item["exists"] for name, item in runtime_trust.items()},
                "socket_ready": {name: item["socket_ready"] for name, item in runtime_trust.items()},
                "socket_trust": runtime_trust,
                "trusted": runtime_trusted,
                "reason": runtime_reason,
                "parent_directory_warning": any(item["parent_directory_warning"] for item in runtime_trust.values()),
            },
            "kytensor": {
                "service": services.get("kytensor.service", {}),
                "repository_index": {
                    "available": bool(model_entries),
                    "ok": has_text_models,
                    "models": model_entries[:32],
                    "has_text_embedding_models": has_text_models,
                },
            },
            "session": None,
            "model_info": {"name": "gte text embedding model detected", "dim": 768, "ondevice": True} if has_text_models else None,
            "embedding_probe": None,
            "available": embedding_available,
            "inference_ready": None,
            "ready_for_default_retrieval": False,
            "capability_state": capability_state,
            "component_ready": runtime_trusted and capability_state in {"installed_unwarmed", "recently_ready"},
            "product_state": product_state,
            "user_message": user_message,
            "next_action": next_action,
            "fallback": "hash_embedding",
            "diagnosis": {
                "availability_meaning": "fast mode checks files, sockets and services only; run sdk-status --probe-embedding for real inference.",
                "probe_meaning": "not executed in fast mode to keep UKUI settings responsive.",
            },
        },
        "vector_store": {
            "backend": "kysdk_vector_engine_client",
            "status_mode": "fast",
            "library": "libkysdk-vector-engine-client.so.1",
            "library_loadable": None,
            "headers": {str(vector_header): vector_header.exists()},
            "binary": str(vector_binary),
            "default_binary": "/usr/bin/kylin-ai-vector-engine-default",
            "user_service": vector_service,
            "socket": str(vector_socket),
            "socket_exists": vector_socket_exists,
            "socket_ready": vector_socket_ready,
            "trusted": bool(vector_trust["trusted"]),
            "reason": str(vector_trust["reason"]),
            "socket_trust": vector_trust,
            "parent_directory_warning": bool(vector_trust["parent_directory_warning"]),
            "available": vector_available,
            "capability_state": vector_state,
            "component_ready": vector_state == "socket_ready",
            "user_message": vector_message,
            "next_action": vector_action,
            "fallback": "sqlite_fts_hash_vector",
        },
    }


def _embedding_runtime_base(uid: int) -> str:
    # Kylin platform protocol path; every socket is fully verified before status use.
    return f"/tmp/.kylin-ai-runtime-unix/{uid}"  # nosec B108


def _vector_socket_path(uid: int) -> str:
    # Kylin platform protocol path; the socket is fully verified before status use.
    return f"/tmp/kylin-ai-vector-engine-{uid}.sock"  # nosec B108


def _augment_embedding_product_state(embedding: Dict[str, object]) -> None:
    if embedding.get("capability_state"):
        embedding.setdefault(
            "product_state",
            _embedding_product_state(
                installed=bool(embedding.get("available") and embedding.get("model_info")),
                warmup_state=_read_kylin_warmup_state(),
                fallback=str(embedding.get("fallback", "hash_embedding")),
            ),
        )
        return
    available = bool(embedding.get("available"))
    inference_value = embedding.get("inference_ready")
    probe = embedding.get("embedding_probe")
    model_info = embedding.get("model_info")
    warmup_state = _read_kylin_warmup_state()
    warmup_ready = _warmup_state_is_ready(warmup_state)
    if inference_value is True:
        embedding["capability_state"] = "inference_ready"
        embedding["component_ready"] = True
        embedding["user_message"] = "推理就绪，可作为端侧检索增强"
        embedding["next_action"] = "可继续使用，也可定期导出诊断报告"
    elif inference_value is False:
        embedding["capability_state"] = "probe_failed"
        embedding["component_ready"] = False
        embedding["user_message"] = "已安装，但端侧增强检查未通过"
        embedding["next_action"] = "默认检索不受影响；需要端侧增强时请点击“预热 Kylin SDK”后重试"
    elif available and model_info and warmup_ready:
        embedding["capability_state"] = "recently_ready"
        embedding["component_ready"] = True
        embedding["user_message"] = "已安装，最近一次端侧向量预热成功"
        embedding["next_action"] = "可继续使用；需要复核时点击“检查端侧向量能力”"
    elif available and model_info:
        embedding["capability_state"] = "installed_unwarmed"
        embedding["component_ready"] = True
        embedding["user_message"] = "已安装，当前未预热"
        embedding["next_action"] = "首次使用可能稍慢，可点击“预热 Kylin SDK”"
    elif available:
        embedding["capability_state"] = "metadata_incomplete"
        embedding["component_ready"] = False
        embedding["user_message"] = "已检测到部分 SDK 文件，模型或运行时信息不完整"
        embedding["next_action"] = "进入端侧能力页运行检查"
    else:
        embedding["capability_state"] = "not_installed"
        embedding["component_ready"] = False
        embedding["user_message"] = "未检测到 Kylin embedding SDK"
        embedding["next_action"] = "可继续使用稳定检索路径，或安装 openKylin AI 组件"
    embedding["product_state"] = _embedding_product_state(
        installed=bool(available and model_info),
        warmup_state=warmup_state,
        fallback=str(embedding.get("fallback", "hash_embedding")),
        inference_ready=inference_value is True,
        probe=probe if isinstance(probe, dict) else None,
    )
    if probe is None and embedding["capability_state"] == "installed_unwarmed":
        diagnosis = embedding.setdefault("diagnosis", {})
        if isinstance(diagnosis, dict):
            diagnosis["product_status_meaning"] = "未预热不是故障；快速状态用于保持 UKUI 设置页流畅。"


def _kylin_warmup_state_path() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home) / "memguardos" / "kylin-sdk-warmup.json"
    return Path.home() / ".local" / "state" / "memguardos" / "kylin-sdk-warmup.json"


def _read_kylin_warmup_state() -> Dict[str, object]:
    path = _kylin_warmup_state_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"read_error": True, "path": str(path)}
    return payload if isinstance(payload, dict) else {}


def _warmup_state_is_ready(state: Dict[str, object]) -> bool:
    return bool(state.get("ok") and state.get("inference_ready") and int(state.get("dim") or 0) == 768)


def _embedding_product_state(
    *,
    installed: bool,
    warmup_state: Dict[str, object],
    fallback: str,
    inference_ready: bool = False,
    probe: Dict[str, object] | None = None,
) -> Dict[str, object]:
    ready = inference_ready or _warmup_state_is_ready(warmup_state)
    dim = 0
    if probe:
        dim = int(probe.get("dim") or 0)
    if not dim:
        dim = int(warmup_state.get("dim") or 0)
    if ready:
        warmup_label = "ready"
    elif installed:
        warmup_label = "not_warmed"
    else:
        warmup_label = "unavailable"
    last_failure_reason = ""
    last_failure_at = ""
    if warmup_state and not warmup_state.get("ok"):
        last_failure_reason = str(warmup_state.get("recommendation") or warmup_state.get("error") or "")
        last_failure_at = str(warmup_state.get("completed_at", ""))
    return {
        "installed": installed,
        "last_probe_success": ready,
        "last_success_at": str(warmup_state.get("completed_at", "")) if ready else "",
        "last_success_dim": dim or None,
        "last_success_model": str(warmup_state.get("model", "")),
        "last_failure_at": last_failure_at,
        "last_failure_reason": last_failure_reason,
        "warmup_state": warmup_label,
        "fallback": fallback,
        "user_message": (
            "已安装，最近一次端侧向量预热成功"
            if ready
            else ("已安装，当前未预热" if installed else "未检测到端侧向量组件")
        ),
    }


def _augment_vector_product_state(vector_store: Dict[str, object]) -> None:
    if vector_store.get("capability_state"):
        return
    available = bool(vector_store.get("available"))
    user_service = vector_store.get("user_service", {})
    service_active = bool(user_service.get("active")) if isinstance(user_service, dict) else False
    has_trust_status = any(
        key in vector_store
        for key in ("socket_ready", "trusted", "reason", "socket_trust")
    )
    socket_ready = bool(
        vector_store.get("socket_ready")
        if "socket_ready" in vector_store
        else vector_store.get("socket_exists")
    )
    if socket_ready:
        vector_store["capability_state"] = "socket_ready"
        vector_store["component_ready"] = True
        vector_store["user_message"] = "向量服务接口可连接"
        vector_store["next_action"] = "可继续使用；如需服务管理状态，可在系统维护中刷新服务"
    elif service_active:
        vector_store["capability_state"] = (
            "service_active_socket_untrusted" if has_trust_status else "service_active"
        )
        vector_store["component_ready"] = not has_trust_status
        vector_store["user_message"] = (
            "向量服务正在运行，但 socket 尚未通过信任校验"
            if has_trust_status
            else "向量服务正在运行"
        )
        vector_store["next_action"] = (
            "保持 fallback；请检查 socket 类型、owner、连接与 SO_PEERCRED"
            if has_trust_status
            else "可继续使用"
        )
    elif available:
        vector_store["capability_state"] = "metadata_available"
        vector_store["component_ready"] = False
        vector_store["user_message"] = "已检测到向量组件，服务接口未连接"
        vector_store["next_action"] = "需要端侧向量库时重启 Kylin vector engine"
    else:
        vector_store["capability_state"] = "not_installed"
        vector_store["component_ready"] = False
        vector_store["user_message"] = "未检测到 Kylin vector SDK"
        vector_store["next_action"] = "系统将继续使用 SQLite FTS 与本地向量 fallback"


def _system_status() -> Dict[str, object]:
    os_release = _read_os_release()
    return {
        "platform": platform.platform(),
        "kernel": platform.release(),
        "machine": platform.machine(),
        "distribution_id": os_release.get("ID", ""),
        "distribution_name": os_release.get("NAME", ""),
        "distribution_version": os_release.get("VERSION", ""),
        "is_openkylin_like": "openkylin" in " ".join(os_release.values()).lower(),
    }


def _service_status(units: List[str]) -> Dict[str, object]:
    systemctl = shutil.which("systemctl")
    result: Dict[str, object] = {
        "systemd": {
            "available": bool(systemctl),
            "active": False,
            "note": "systemctl not found" if not systemctl else "systemctl available",
        }
    }
    if not systemctl:
        for unit in units:
            result[unit] = {"available": False, "active": False, "state": "unknown"}
        return result
    for unit in units:
        result[unit] = _systemctl_unit_status(systemctl, unit)
    result["systemd"]["active"] = True
    return result


def _systemctl_unit_status(systemctl: str, unit: str) -> Dict[str, object]:
    available = subprocess.run(
        [systemctl, "list-unit-files", unit, "--no-legend"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=5,
        check=False,
    )
    active = subprocess.run(
        [systemctl, "is-active", unit],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=5,
        check=False,
    )
    enabled = subprocess.run(
        [systemctl, "is-enabled", unit],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=5,
        check=False,
    )
    return {
        "available": bool(available.stdout.strip()),
        "active": active.stdout.strip() == "active",
        "state": active.stdout.strip() or "unknown",
        "enabled": enabled.stdout.strip() or "unknown",
    }


def _desktop_status() -> Dict[str, object]:
    multiarch = _multiarch()
    peony_plugin = Path("/usr/lib") / multiarch / "peony-extensions" / "libpeony-menu-plugin-memguardos.so"
    ukcc_plugin = Path("/usr/lib") / multiarch / "ukui-control-center" / "libmemguardos-ukcc-plugin.so"
    return {
        "session_desktop": _env_value("XDG_CURRENT_DESKTOP"),
        "desktop_session": _env_value("DESKTOP_SESSION"),
        "peony": {
            "available": bool(shutil.which("peony")),
            "plugin_path": str(peony_plugin),
            "plugin_installed": peony_plugin.exists(),
        },
        "ukui_control_center": {
            "available": bool(shutil.which("ukui-control-center") or shutil.which("ukcc")),
            "plugin_path": str(ukcc_plugin),
            "plugin_installed": ukcc_plugin.exists(),
        },
        "notify_send": {
            "available": bool(shutil.which("notify-send")),
        },
    }


def _security_status() -> Dict[str, object]:
    audit_rules = Path("/etc/audit/rules.d")
    return {
        "auditd": {
            "available": bool(shutil.which("auditctl")),
            "rules_tool_available": bool(
                shutil.which("auditctl") or _safe_path_exists(audit_rules)
            ),
            "rules_directory_accessible": _safe_path_exists(audit_rules),
        },
        "bpf_lsm": {
            "source_available": Path("native/bpf_lsm/memguard_lsm.bpf.c").exists(),
            "loader_available": Path("native/bpf_lsm/memguard_lsm_loader.c").exists(),
            "note": "可选强制拦截能力，默认仅用于安全测试路径和可审查 denylist。",
        },
        "polkit": {
            "available": bool(shutil.which("pkexec")),
            "policy_path": "/usr/share/polkit-1/actions/org.memguardos.policy",
            "policy_installed": Path("/usr/share/polkit-1/actions/org.memguardos.policy").exists(),
        },
    }


def _safe_path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _resource_status(namespace: str) -> Dict[str, object]:
    status = service_resource_status()
    memory = {
        "cgroup_memory_current": status.get("memory_current"),
        "cgroup_memory_max": status.get("memory_max"),
    }
    return {
        "cgroup_v2": bool(status.get("cgroup_v2")),
        "service_managed": bool(status.get("service_managed")),
        "control_group": status.get("control_group", ""),
        "diagnosis_code": status.get("diagnosis_code", "unknown"),
        "advice": status.get("advice", ""),
        "memory": memory,
        "cpu": {
            "cpu_max": status.get("cpu_max"),
            "cpu_quota_per_sec_usec": status.get("cpu_quota_per_sec_usec"),
            "cpu_stat": status.get("cpu_stat", {}),
        },
        "namespace": namespace,
    }


def _component(
    name: str,
    available: object,
    ready: object,
    label: str,
    *,
    user_message: str = "",
    next_action: str = "",
    capability_state: str = "",
) -> Dict[str, object]:
    if ready is True:
        level = "green"
    elif available:
        level = "yellow"
    else:
        level = "red"
    result = {"name": name, "label": label, "available": bool(available), "ready": bool(ready), "level": level}
    if user_message:
        result["user_message"] = user_message
    if next_action:
        result["next_action"] = next_action
    if capability_state:
        result["capability_state"] = capability_state
    return result


def _summary(components: List[Dict[str, object]]) -> Dict[str, object]:
    weights = {"green": 2, "yellow": 1, "red": 0}
    max_score = len(components) * 2
    score = sum(weights[str(item["level"])] for item in components)
    if any(item["level"] == "red" for item in components[:2]):
        overall = "red"
    elif any(item["level"] in {"yellow", "red"} for item in components):
        overall = "yellow"
    else:
        overall = "green"
    return {
        "overall": overall,
        "readiness_score": round(score / max_score, 4) if max_score else 0.0,
        "green": sum(1 for item in components if item["level"] == "green"),
        "yellow": sum(1 for item in components if item["level"] == "yellow"),
        "red": sum(1 for item in components if item["level"] == "red"),
    }


def _recommendations(components: List[Dict[str, object]], sdk: Dict[str, object], desktop: Dict[str, object], security: Dict[str, object]) -> List[str]:
    recommendations = []
    by_name = {str(item["name"]): item for item in components}
    embedding_state = str(sdk["embedding"].get("capability_state", ""))
    if embedding_state != "installed_not_probed" and by_name["kylin_embedding"]["level"] != "green":
        recommendations.append("Kylin embedding SDK 端侧增强暂未就绪，默认检索不受影响；需要端侧增强时请点击“预热 Kylin SDK”后重试。")
    if by_name["peony"]["level"] != "green" or by_name["ukui_control_center"]["level"] != "green":
        recommendations.append("如需桌面原生体验，请安装 memguardos UKUI/Peony 插件并重启 Peony 或 UKUI 控制中心。")
    if not security["polkit"]["policy_installed"]:
        recommendations.append("如需桌面授权弹窗，请安装 org.memguardos.policy 和 memguardos-native-helper.sh。")
    if sdk["vector_store"].get("available") and not sdk["vector_store"].get("component_ready"):
        recommendations.append("Kylin vector SDK 已安装但用户服务未激活，可重启 kylin-ai-vector-engine 后复验。")
    if not desktop["notify_send"]["available"]:
        recommendations.append("notify-send 不可用时，Peony 扫描仍会写入 JSON/审计，但不会显示桌面通知。")
    return recommendations


def _read_os_release() -> Dict[str, str]:
    path = Path("/etc/os-release")
    if not path.exists():
        return {}
    result = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        result[key] = value.strip().strip('"')
    return result


def _multiarch() -> str:
    command = shutil.which("dpkg-architecture")
    if command:
        try:
            completed = subprocess.run(
                [command, "-qDEB_HOST_MULTIARCH"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
                check=False,
            )
            value = completed.stdout.strip()
            if value:
                return value
        except (OSError, subprocess.SubprocessError):
            pass
    machine = platform.machine()
    if machine in {"x86_64", "AMD64"}:
        return "x86_64-linux-gnu"
    if machine in {"aarch64", "arm64"}:
        return "aarch64-linux-gnu"
    if machine == "loongarch64":
        return "loongarch64-linux-gnu"
    return f"{machine}-linux-gnu"


def _env_value(name: str) -> str:
    import os

    return os.environ.get(name, "")
