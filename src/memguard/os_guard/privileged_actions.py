from __future__ import annotations

from typing import Dict
import shutil
import subprocess


HELPER_PATH = "/usr/lib/memguardos/memguardos-native-helper.sh"
ALLOWED_ACTIONS = {
    "start-service": "启动 MemGuardOS 服务",
    "stop-service": "停止 MemGuardOS 服务",
    "restart-service": "重启 MemGuardOS 服务",
    "refresh-services": "刷新 MemGuardOS systemd 单元",
    "install-auditd-rules": "安装 MemGuardOS 审计规则",
    "run-bpf-lsm-test": "运行安全的 BPF LSM 验证",
}


def run_privileged_action(action: str, timeout: float = 180.0) -> Dict[str, object]:
    if action not in ALLOWED_ACTIONS:
        return {
            "schema": "memguardos-native-action-v1",
            "ok": False,
            "action": action,
            "error_code": "unsupported_action",
            "message": "不支持的系统操作。",
        }
    pkexec = shutil.which("pkexec")
    if not pkexec:
        return {
            "schema": "memguardos-native-action-v1",
            "ok": False,
            "action": action,
            "error_code": "pkexec_unavailable",
            "message": "系统未安装或未启用 Polkit，无法显示管理员授权窗口。",
        }
    try:
        completed = subprocess.run(
            [pkexec, HELPER_PATH, action],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1.0, timeout),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "schema": "memguardos-native-action-v1",
            "ok": False,
            "action": action,
            "error_code": "authorization_timeout",
            "message": "管理员授权或系统操作超时。",
        }
    except OSError as exc:
        return {
            "schema": "memguardos-native-action-v1",
            "ok": False,
            "action": action,
            "error_code": "execution_failed",
            "message": str(exc),
        }
    ok = completed.returncode == 0
    error_code = "" if ok else ("authorization_cancelled" if completed.returncode in {126, 127} else "action_failed")
    return {
        "schema": "memguardos-native-action-v1",
        "ok": ok,
        "action": action,
        "description": ALLOWED_ACTIONS[action],
        "exit_code": completed.returncode,
        "error_code": error_code,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "message": "系统操作已完成。" if ok else "系统操作未完成，请查看错误详情。",
    }
