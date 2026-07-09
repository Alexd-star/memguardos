from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Optional
import json
import os
import re

from memguard.integrations.openhands_runner import OpenHandsMemoryRunner
from memguard.models import stable_hash, utc_now


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class AgentJobQueue:
    def __init__(
        self,
        root: Path,
        *,
        runner_factory: Callable[..., Any] = OpenHandsMemoryRunner,
        model: str = "",
        base_url: str = "",
        api_key: str = "",
    ):
        self.root = Path(root).expanduser().resolve()
        self.runner_factory = runner_factory
        self.model = str(model)
        self.base_url = str(base_url)
        self.api_key = str(api_key)
        for name in ("inbox", "processing", "outbox", "archive", "workspaces", "state"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def submit(self, job: Dict[str, object]) -> Path:
        job_id = str(job.get("job_id") or "").strip()
        if not _SAFE_ID.fullmatch(job_id):
            raise ValueError("job_id must use safe ASCII identifier characters")
        target = self.root / "inbox" / ("%s.json" % job_id)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(job, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(str(temporary), str(target))
        return target

    def process_next(self, service: Any) -> Optional[Dict[str, object]]:
        queued = sorted((self.root / "inbox").glob("*.json"))
        if not queued:
            return None
        source = queued[0]
        processing = self.root / "processing" / source.name
        try:
            os.replace(str(source), str(processing))
        except FileNotFoundError:
            return None
        job_id = source.stem
        raw: Dict[str, object] = {}
        try:
            raw = json.loads(processing.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("job payload must be an object")
            validated = self._validate_job(raw)
            runner = self.runner_factory(
                service=service,
                session_id=validated["session_id"],
                workspace=self.root / "workspaces" / validated["session_id"],
                state_dir=self.root / "state" / validated["session_id"],
                project_root=str(self.root / "workspaces" / validated["session_id"]),
                task_type=validated["task_type"],
                model=self.model,
                base_url=self.base_url,
                api_key=self.api_key,
            )
            result = dict(runner.run(validated["task"]))
            result.update(
                {
                    "job_id": validated["job_id"],
                    "processed_at": utc_now(),
                }
            )
        except Exception as exc:
            result = {
                "ok": False,
                "job_id": job_id if _SAFE_ID.fullmatch(job_id) else "invalid",
                "error": "invalid_job" if isinstance(exc, ValueError) else "agent_run_failed",
                "error_type": exc.__class__.__name__,
                "error_id": stable_hash("%s:%s" % (job_id, exc.__class__.__name__))[:16],
                "processed_at": utc_now(),
            }
        self._write_result(job_id, result)
        self._write_archive_receipt(job_id, raw, result)
        processing.unlink(missing_ok=True)
        return result

    def _validate_job(self, job: Dict[str, object]) -> Dict[str, str]:
        job_id = str(job.get("job_id") or "").strip()
        session_id = str(job.get("session_id") or "").strip()
        task = str(job.get("task") or "").strip()
        task_type = str(job.get("task_type") or "code_assistance").strip()
        if not _SAFE_ID.fullmatch(job_id):
            raise ValueError("invalid job_id")
        if not _SAFE_ID.fullmatch(session_id):
            raise ValueError("invalid session_id")
        if not task or len(task) > 10000:
            raise ValueError("task must contain 1-10000 characters")
        if not _SAFE_ID.fullmatch(task_type):
            raise ValueError("invalid task_type")
        return {
            "job_id": job_id,
            "session_id": session_id,
            "task": task,
            "task_type": task_type,
        }

    def _write_result(self, job_id: str, result: Dict[str, object]) -> None:
        safe_job_id = job_id if _SAFE_ID.fullmatch(job_id) else "invalid"
        target = self.root / "outbox" / ("%s.result.json" % safe_job_id)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(str(temporary), str(target))

    def _write_archive_receipt(
        self,
        job_id: str,
        raw: Dict[str, object],
        result: Dict[str, object],
    ) -> None:
        safe_job_id = job_id if _SAFE_ID.fullmatch(job_id) else "invalid"
        session_id = str(raw.get("session_id") or "")
        receipt = {
            "job_id": safe_job_id,
            "session_id": session_id if _SAFE_ID.fullmatch(session_id) else "invalid",
            "task_hash": stable_hash(str(raw.get("task") or "")),
            "task_chars": len(str(raw.get("task") or "")),
            "ok": bool(result.get("ok")),
            "error": result.get("error", ""),
            "error_id": result.get("error_id", ""),
            "processed_at": result.get("processed_at", utc_now()),
            "raw_task_retained": False,
        }
        target = self.root / "archive" / ("%s.json" % safe_job_id)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(str(temporary), str(target))
