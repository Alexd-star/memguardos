"""Evidence loading for MemGuardOS publication figures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SHOT_ROOT = ROOT / "可视化证据包" / "20260608_161339_MemGuardOS截图证据包" / "screenshots"


def load_json(rel_path: str, default: Any | None = None) -> Any:
    path = ROOT / rel_path
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_text(rel_path: str) -> str:
    path = ROOT / rel_path
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


@dataclass(frozen=True)
class Evidence:
    eval_result: dict[str, Any]
    openkylin_1k: dict[str, Any]
    openkylin_10k: dict[str, Any]
    ready: dict[str, Any]
    evidence_manifest: dict[str, Any]
    sdk_status: str
    embedding_cpp_probe: str
    kylin_retrieval: str
    vector_probe: str

    @property
    def ready_summary(self) -> dict[str, Any]:
        return self.ready.get("summary", {})


def load_evidence() -> Evidence:
    return Evidence(
        eval_result=load_json("docs/eval_result.json", {}),
        openkylin_1k=load_json("docs/evidence/latest/eval_result_openkylin.json", {}),
        openkylin_10k=load_json("docs/evidence/latest/eval_result_openkylin_10k.json", {}),
        ready=load_json("docs/competition_ready.json", {}),
        evidence_manifest=load_json("docs/evidence/latest/manifest.json", {}),
        sdk_status=load_text("docs/evidence/latest/kylin_embedding_diag/sdk_status.txt"),
        embedding_cpp_probe=load_text("docs/evidence/latest/embedding_cpp_probe.txt"),
        kylin_retrieval=load_text("docs/evidence/latest/kylin_retrieval.txt"),
        vector_probe=load_text("docs/evidence/latest/vector_probe.txt"),
    )


def screenshot_path(name_prefix: str) -> Path | None:
    if not SHOT_ROOT.exists():
        return None
    matches = sorted(SHOT_ROOT.glob(f"{name_prefix}*.png"))
    return matches[0] if matches else None

