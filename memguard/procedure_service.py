"""Procedure service integration, without executing suggested steps.

Host requirements: namespace, detector.classify, meta.get_memory,
meta.procedure_stats, meta.record_procedure_feedback, search and
_persist_new_memory. MemoryRecord must serialize ``dependencies``. Persistence
must transactionally compare dependency versions/eligibility; feedback must
CAS both the procedure and its dependencies and enforce use-id idempotency.

Feedback receives ONLY (id, version, session, use, status). It returns
``{recorded: bool, duplicate: bool, status: pass|fail|unknown}``;
recorded=False/duplicate=True is an idempotent replay of the same status. CAS/conflicting use-id
rejection must raise ValueError (legacy bool results are also supported). No checks,
paths, arguments, content or environment values cross that write boundary.
"""

from __future__ import annotations

import json
from pathlib import Path
import platform
from typing import Any, Protocol
from uuid import uuid4

from memguard.models import MemoryRecord, MemorySource, make_memory
from memguard.procedural import (
    MAX_PARAMETERS,
    MAX_TEXT_LENGTH,
    ProcedureSpec,
    ProcedureValidationError,
    _relative_parts,
    _workspace,
    check_environment,
    verify_postconditions,
)
from memguard.sensitivity.detector import SensitiveDetector


class ProcedureMeta(Protocol):
    def get_memory(self, memory_id: str) -> dict[str, Any] | None: ...
    def procedure_stats(self, memory_id: str) -> dict[str, Any]: ...
    def record_procedure_feedback(
        self, memory_id: str, version_id: str, session_id: str,
        use_id: str, status: str,
    ) -> bool | dict[str, Any]: ...


def _identifier(value: Any, label: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 256 or "\x00" in value or (not empty and not value.strip()):
        raise ProcedureValidationError(f"{label} must be a bounded identifier")
    return value


def _arguments(arguments: Any) -> dict[str, str] | None:
    if arguments is None:
        return None
    if not isinstance(arguments, dict) or len(arguments) > MAX_PARAMETERS:
        raise ProcedureValidationError("arguments must be a bounded dictionary")
    for key, value in arguments.items():
        if not isinstance(key, str) or len(key) > 64 or not isinstance(value, str) or len(value) > MAX_TEXT_LENGTH or "\x00" in value:
            raise ProcedureValidationError("arguments must contain bounded string names and values")
    return dict(arguments)


def _host_environment() -> dict[str, str]:
    """Only explicitly measured facts, not arbitrary environment variables.

    os is lowercase; platform/system/platform_system/platform.system retain
    platform.system() spelling. python is major.minor; python_version and
    platform.python_version are the full running interpreter version.
    """
    system = platform.system()
    version = platform.python_version()
    return {
        "os": system.lower(), "platform": system, "system": system,
        "platform_system": system, "platform.system": system,
        "python": ".".join(platform.python_version_tuple()[:2]),
        "python_version": version, "platform.python_version": version,
        "python_implementation": platform.python_implementation(),
        "machine": platform.machine(),
    }


def _environment(spec: ProcedureSpec) -> dict[str, Any]:
    if not spec.environment:
        return {"overall": "pass", "checks": []}
    return check_environment(spec.environment, _host_environment())


def _blocked(reason: str, overall: str = "unknown") -> dict[str, Any]:
    return {"status": "blocked", "blocked": True, "reason": reason,
            "stored": False, "recorded": False, "verified": False, "overall": overall}


class ProcedureServiceMixin:
    namespace: str
    detector: SensitiveDetector
    meta: ProcedureMeta

    def _procedure_sensitive(self, *payloads: Any) -> bool:
        # JSON alone obscures assignment context (e.g. a password parameter).
        # Inspect full payloads, every leaf, and each string key/value pairing.
        def texts(value: Any):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for key, item in value.items():
                    yield str(key)
                    if isinstance(item, str):
                        yield f"{key}={item}"
                    yield from texts(item)
            elif isinstance(value, list):
                for item in value:
                    yield from texts(item)

        for payload in payloads:
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if self.detector.classify(encoded).level == "high":
                return True
            if any(self.detector.classify(text).level == "high" for text in texts(payload)):
                return True
        return False

    def _procedure_eligible(self, row: dict[str, Any] | None, project: str, session: str) -> bool:
        return bool(row) and (
            row.get("scope") == self.namespace
            and not row.get("deleted")
            and row.get("governance_state") == "active"
            and row.get("trust_level") in {"trusted", "verified", "reviewed"}
            and row.get("project_root", "") in {"", project}
            and row.get("retention_session_id", "") in {"", session}
            and row.get("preference_scope", "global") in {"global", "project"}
            and not row.get("preference_session_id")
            and not row.get("valid_to") and not row.get("superseded_by")
            and isinstance(row.get("version_id"), str) and bool(row["version_id"])
        )

    def _procedure_dependencies(self, row: dict[str, Any]) -> list[dict[str, str]]:
        value = row.get("dependencies", row.get("dependencies_json"))
        if value is None:
            # Older metadata projections may omit the dependency column.
            store = getattr(self, "store", None)
            record = store.get_latest(row["memory_id"]) if store is not None else None
            if record is None or record.version.version_id != row["version_id"]:
                raise ProcedureValidationError("dependency snapshot unavailable")
            value = getattr(record, "dependencies", None)
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, list) or not 1 <= len(value) <= 32:
            raise ProcedureValidationError("procedure requires 1..32 dependencies")
        result = []
        seen = set()
        for item in value:
            if not isinstance(item, dict) or set(item) != {"memory_id", "version_id", "relation"} or item["relation"] != "derived_from":
                raise ProcedureValidationError("invalid source dependency")
            memory_id = _identifier(item["memory_id"], "source memory id")
            version_id = _identifier(item["version_id"], "source version id")
            if memory_id in seen or memory_id == row["memory_id"]:
                raise ProcedureValidationError("duplicate or self dependency")
            seen.add(memory_id)
            result.append({"memory_id": memory_id, "version_id": version_id, "relation": "derived_from"})
        return result

    def _procedure_sources_current(self, dependencies: list[dict[str, str]], project: str, session: str) -> bool:
        for dependency in dependencies:
            row = self.meta.get_memory(dependency["memory_id"])
            if not self._procedure_eligible(row, project, session) or row["version_id"] != dependency["version_id"]:
                return False
        return bool(dependencies)

    def _procedure_current(
        self, memory_id: str, project: str, session: str, expected_version: str = "",
    ) -> tuple[dict[str, Any], ProcedureSpec, list[dict[str, str]]] | None:
        row = self.meta.get_memory(memory_id)
        if not self._procedure_eligible(row, project, session) or row.get("type") != "procedure":
            return None
        if row.get("committed", True) is False or row.get("write_state", "committed") != "committed":
            return None
        if expected_version and row["version_id"] != expected_version:
            return None
        try:
            dependencies = self._procedure_dependencies(row)
            spec = ProcedureSpec.from_dict(json.loads(row["content"]))
        except (ValueError, TypeError, KeyError):
            return None
        if not self._procedure_sources_current(dependencies, project, session):
            return None
        return row, spec, dependencies

    def learn_procedure(
        self, spec: dict[str, Any], workspace: str | Path,
        source_memory_ids: list[str], session_id: str = "",
        arguments: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        session_id = _identifier(session_id, "session id", empty=True)
        arguments = _arguments(arguments)
        procedure = ProcedureSpec.from_dict(spec)
        if self._procedure_sensitive(procedure.to_dict(), arguments):
            return _blocked("sensitive_high")
        project = str(_workspace(workspace))
        if not isinstance(source_memory_ids, list) or not 1 <= len(source_memory_ids) <= 32:
            raise ProcedureValidationError("source_memory_ids must contain 1..32 ids")
        source_ids = [_identifier(value, "source memory id") for value in source_memory_ids]
        if len(set(source_ids)) != len(source_ids):
            raise ProcedureValidationError("duplicate source memory ids")
        dependencies = []
        for memory_id in source_ids:
            row = self.meta.get_memory(memory_id)
            if not self._procedure_eligible(row, project, session_id):
                return _blocked("source_unavailable")
            dependencies.append({"memory_id": memory_id, "version_id": row["version_id"], "relation": "derived_from"})
        environment = _environment(procedure)
        if environment["overall"] != "pass":
            return _blocked("environment_not_applicable", environment["overall"])
        rendered = procedure.render(arguments)
        if self._procedure_sensitive(rendered):
            return _blocked("sensitive_high")
        verification = verify_postconditions(procedure, project, arguments)
        if verification["overall"] != "pass":
            return {"status": "not_verified", "stored": False, "verified": False,
                    "overall": verification["overall"], "verification": verification}
        if not self._procedure_sources_current(dependencies, project, session_id):
            return _blocked("source_changed")
        record: MemoryRecord = make_memory(
            content=json.dumps(procedure.to_dict(), ensure_ascii=False, sort_keys=True),
            memory_type="procedure", subtype="workflow", scope=self.namespace,
            project_root=project, source=MemorySource(kind="verified_procedure"),
            summary=procedure.goal, trust_level="verified", governance_state="active",
        )
        if "dependencies" not in record.__dataclass_fields__:
            raise RuntimeError("MemoryRecord must serialize dependencies before procedure integration")
        record.dependencies = dependencies
        record.finalize_hash()
        try:
            record = self._persist_new_memory(record)
        except ValueError:
            return _blocked("source_snapshot_changed")
        return {"status": "learned", "stored": True, "verified": True, "overall": "pass",
                "record": record, "memory_id": record.memory_id, "version_id": record.version.version_id,
                "dependencies": dependencies, "verification": verification,
                "environment": environment, "rendered": rendered}

    def recall_procedures(
        self, task: str, workspace: str | Path, arguments: dict[str, str] | None = None,
        limit: int = 5, session_id: str = "",
    ) -> list[dict[str, Any]]:
        if not isinstance(task, str) or len(task) > MAX_TEXT_LENGTH or "\x00" in task:
            raise ProcedureValidationError("task must be a bounded string")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ProcedureValidationError("limit must be an integer in 1..50")
        session_id = _identifier(session_id, "session id", empty=True)
        arguments = _arguments(arguments)
        project = str(_workspace(workspace))
        if not task.strip() or self._procedure_sensitive(task, arguments):
            return []
        candidates = self.search(task, limit=min(200, max(20, limit * 8)),
                                 project_root=project, session_id=session_id, audit=False)
        results = []
        seen = set()
        for candidate in candidates:
            memory_id = candidate.get("memory_id")
            if not isinstance(memory_id, str) or not memory_id or memory_id in seen:
                continue
            seen.add(memory_id)
            current = self._procedure_current(memory_id, project, session_id, str(candidate.get("version_id") or ""))
            if current is None:
                continue
            row, procedure, dependencies = current
            if self._procedure_sensitive(procedure.to_dict()) or _environment(procedure)["overall"] != "pass":
                continue
            missing = [name for name, default in procedure.parameters.items() if default is None and name not in (arguments or {})]
            item = {"memory_id": memory_id, "version_id": row["version_id"],
                    "goal_template": procedure.goal, "dependencies": dependencies,
                    "feedback_stats": self.meta.procedure_stats(memory_id),
                    "steps": [], "failure_guards": [], "postconditions": [],
                    "required_arguments": missing, "status": "requires_arguments"}
            if not missing:
                try:
                    rendered = procedure.render(arguments)
                    for condition in rendered["postconditions"]:
                        _relative_parts(condition["path"])
                except ProcedureValidationError:
                    item["status"] = "invalid_arguments"
                else:
                    if self._procedure_sensitive(rendered):
                        continue
                    item.update(status="ready", goal=rendered["goal"], steps=rendered["steps"],
                                failure_guards=rendered["failure_guards"], postconditions=rendered["postconditions"])
            results.append(item)
            if len(results) >= limit:
                break
        return results

    def procedure_context_line(self, memory_id: str, project_root: str | Path) -> tuple[str, str]:
        """Return one complete template, never a truncated proof or execution.

        get_memory must project committed records, not staged writes. Required
        arguments are displayed as requirements; defaults are not replaced and
        missing arguments never block a valid template. Callers must include
        this entire line within their budget or omit it entirely.
        """
        try:
            memory_id = _identifier(memory_id, "memory id")
            project = str(_workspace(project_root))
        except ProcedureValidationError:
            return "", "invalid_workspace_or_memory_id"
        current = self._procedure_current(memory_id, project, "")
        if current is None:
            return "", "procedure_or_source_unavailable"
        row, procedure, dependencies = current
        payload = procedure.to_dict()
        if self._procedure_sensitive(payload):
            return "", "sensitive_high"
        if _environment(procedure)["overall"] != "pass":
            return "", "environment_not_applicable"
        # Keep every constraint in a single data line, including escaped newlines.
        def readable(value: Any) -> str:
            return json.dumps(value, ensure_ascii=False)

        required = [name for name, default in procedure.parameters.items() if default is None]
        line = "; ".join([
            f"Procedure {memory_id} version={row['version_id']} (suggestions only)",
            f"goal={readable(procedure.goal)}",
            f"steps={readable(procedure.steps)}",
            f"failure_guards={readable(procedure.failure_guards)}",
            f"postconditions={readable(procedure.postconditions)}",
            f"parameters={readable(procedure.parameters)}",
            f"arguments must be provided for={readable(required)}",
            f"environment conditions={readable(procedure.environment)}",
            f"dependencies={readable(dependencies)}",
        ])
        return line, ""

    def verify_procedure(
        self, memory_id: str, workspace: str | Path, session_id: str = "",
        arguments: dict[str, str] | None = None, use_id: str = "",
    ) -> dict[str, Any]:
        memory_id = _identifier(memory_id, "memory id")
        session_id = _identifier(session_id, "session id", empty=True)
        use_id = _identifier(use_id, "use id", empty=True) or uuid4().hex
        arguments = _arguments(arguments)
        if self._procedure_sensitive(arguments):
            return _blocked("sensitive_high")
        project = str(_workspace(workspace))
        current = self._procedure_current(memory_id, project, session_id)
        if current is None:
            return _blocked("procedure_or_source_unavailable")
        row, procedure, dependencies = current
        if self._procedure_sensitive(procedure.to_dict()):
            return _blocked("sensitive_high")
        environment = _environment(procedure)
        if environment["overall"] != "pass":
            return _blocked("environment_not_applicable", environment["overall"])
        rendered = procedure.render(arguments)
        if self._procedure_sensitive(rendered):
            return _blocked("sensitive_high")
        verification = verify_postconditions(procedure, project, arguments)
        if self._procedure_current(memory_id, project, session_id, row["version_id"]) is None:
            return _blocked("procedure_or_source_changed")
        try:
            feedback = self.meta.record_procedure_feedback(
                memory_id, row["version_id"], session_id, use_id, verification["overall"],
            )
        except ValueError:
            return _blocked("feedback_snapshot_changed")
        accepted = feedback is True or (
            isinstance(feedback, dict) and type(feedback.get("recorded")) is bool
            and (feedback["recorded"] or feedback.get("duplicate") is True)
            and feedback.get("status") == verification["overall"]
        )
        if not accepted:
            return _blocked("feedback_snapshot_changed")
        feedback_status = feedback.get("status", verification["overall"]) if isinstance(feedback, dict) else verification["overall"]
        return {**verification, "status": verification["overall"], "verified": verification["overall"] == "pass",
                "memory_id": memory_id, "version_id": row["version_id"], "use_id": use_id,
                "goal": rendered["goal"], "steps": rendered["steps"],
                "failure_guards": rendered["failure_guards"], "postconditions": rendered["postconditions"],
                "dependencies": dependencies, "environment": environment,
                "recorded": feedback is True or feedback.get("recorded") is True,
                "duplicate": isinstance(feedback, dict) and feedback.get("duplicate") is True,
                "feedback_status": feedback_status}
