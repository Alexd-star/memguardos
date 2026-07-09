from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Dict, List, Optional
import hashlib
import json
import re
import threading

from memguard.eval.metrics import percentile
from memguard.service import MemGuardService


LONGMEMEVAL_V2_REPOSITORY = "https://github.com/xiaowu0162/LongMemEval-V2"
LONGMEMEVAL_V2_DATASET = "xiaowu0162/longmemeval-v2"
LONGMEMEVAL_V2_CODE_REVISION = "be15ea6e995462f3391c1a610892df3f67dfa7bd"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> List[Dict[str, object]]:
    rows = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError("%s:%d must contain a JSON object" % (path, line_number))
        rows.append(item)
    return rows


def _compact_text(value: object, max_chars: int) -> str:
    text = re.sub(r"[ \t]+", " ", str(value or ""))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[: max(1, int(max_chars))]


def flatten_trajectory(trajectory: Dict[str, object], max_chars: int = 11000) -> List[Dict[str, object]]:
    trajectory_id = str(trajectory.get("id") or "").strip()
    if not trajectory_id:
        raise ValueError("LongMemEval-V2 trajectory is missing id")
    domain = str(trajectory.get("domain") or "unknown")
    environment = str(trajectory.get("environment") or "unknown")
    goal = _compact_text(trajectory.get("goal"), max_chars)
    outcome = _compact_text(trajectory.get("outcome"), 200)
    chunks = [
        {
            "trajectory_id": trajectory_id,
            "state_index": None,
            "source_uri": "longmemeval-v2://%s/summary" % trajectory_id,
            "text": _compact_text(
                "LongMemEval-V2 trajectory=%s domain=%s environment=%s outcome=%s\nGoal: %s"
                % (trajectory_id, domain, environment, outcome, goal),
                max_chars,
            ),
        }
    ]
    for ordinal, raw_state in enumerate(trajectory.get("states") or []):
        state = dict(raw_state) if isinstance(raw_state, dict) else {"state": raw_state}
        state_index = state.get("state_index", ordinal)
        fields = [
            "LongMemEval-V2 trajectory=%s state=%s domain=%s environment=%s" % (trajectory_id, state_index, domain, environment),
            "URL: %s" % _compact_text(state.get("url"), 1000),
            "Thought: %s" % _compact_text(state.get("thought"), 3000),
            "Action: %s" % _compact_text(state.get("action"), 3000),
            "Visible state:\n%s" % _compact_text(state.get("accessibility_tree"), max_chars),
        ]
        chunks.append(
            {
                "trajectory_id": trajectory_id,
                "state_index": state_index,
                "source_uri": "longmemeval-v2://%s/state/%s" % (trajectory_id, state_index),
                "text": _compact_text("\n".join(fields), max_chars),
            }
        )
    return chunks


class LongMemEvalV2Backend:
    """MemGuardOS backend implementing LongMemEval-V2's insert/query contract."""

    memory_type = "memguardos"

    def __init__(self, memory_params: Dict[str, object]):
        self.memory_params = dict(memory_params)
        workspace = Path(str(self.memory_params.get("workspace_dir") or ".")).resolve()
        namespace = str(self.memory_params.get("namespace") or "longmemeval-v2")
        root = workspace / "memguardos_longmemeval_v2" / namespace
        root.mkdir(parents=True, exist_ok=True)
        self.context_limit = max(1, int(self.memory_params.get("context_limit") or 8))
        self.max_chunk_chars = max(1000, int(self.memory_params.get("max_chunk_chars") or 11000))
        self.service = MemGuardService(store_path=root / "memory.segment", db_path=root / "memory.sqlite3", namespace=namespace)
        self._query_context_local = threading.local()
        self.inserted_trajectory_ids: List[str] = []

    @property
    def memory_config(self) -> Dict[str, object]:
        return {"memory_type": self.memory_type, "memory_params": dict(self.memory_params)}

    def insert(self, trajectory: Dict[str, object]) -> Dict[str, object]:
        chunks = flatten_trajectory(trajectory, max_chars=self.max_chunk_chars)
        stored = []
        rejected = []
        for chunk in chunks:
            result = self.service.ingest_text(
                str(chunk["text"]),
                source_kind="longmemeval_v2",
                source_uri=str(chunk["source_uri"]),
                tier="long_term",
            )
            if result.get("stored"):
                stored.append(result["record"].memory_id)
            else:
                rejected.append({"source_uri": chunk["source_uri"], "reason": result.get("reason")})
        trajectory_id = str(trajectory["id"])
        if trajectory_id not in self.inserted_trajectory_ids:
            self.inserted_trajectory_ids.append(trajectory_id)
        return {
            "trajectory_id": trajectory_id,
            "chunk_count": len(chunks),
            "stored_count": len(stored),
            "rejected_count": len(rejected),
            "memory_ids": stored,
            "rejected": rejected,
        }

    def set_query_context(self, context: Optional[Dict[str, object]] = None, **kwargs: object) -> None:
        merged = dict(context or {})
        merged.update(kwargs)
        self._query_context_local.context = merged

    def get_query_context(self) -> Dict[str, object]:
        return dict(getattr(self._query_context_local, "context", {}) or {})

    def clear_query_context(self) -> None:
        if hasattr(self._query_context_local, "context"):
            delattr(self._query_context_local, "context")

    def query(self, query: str, query_image: Optional[str] = None) -> List[Dict[str, str]]:
        del query_image
        hits = self.service.search(str(query), limit=self.context_limit)
        contexts = []
        for hit in hits:
            content = str(hit.get("content") or hit.get("summary") or "").strip()
            if not content:
                continue
            source = dict(hit.get("source") or {})
            contexts.append(
                {
                    "type": "text",
                    "value": "[MemGuardOS evidence | %s]\n%s" % (source.get("uri") or hit.get("memory_id"), content),
                }
            )
        return contexts

    def post_query_hook(self, *, query: str, query_image: Optional[str], memory_context: List[Dict[str, str]]) -> Dict[str, object]:
        del query, query_image
        return {
            "backend": self.memory_type,
            "context_count": len(memory_context),
            "query_context": self.get_query_context(),
        }

    def close(self) -> None:
        self.service.close()


def validate_context_items(items: object) -> List[str]:
    issues = []
    if not isinstance(items, list):
        return ["memory.query did not return a list"]
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            issues.append("context item %d is not an object" % index)
            continue
        if item.get("type") not in {"text", "image"}:
            issues.append("context item %d has invalid type" % index)
        value = item.get("value")
        if not isinstance(value, str) or not value.strip():
            issues.append("context item %d has an empty value" % index)
    return issues


def _answer_lexically_covered(answer: object, contexts: List[Dict[str, str]]) -> bool:
    normalized_answer = re.sub(r"\s+", " ", str(answer or "").casefold()).strip(" .;,")
    if not normalized_answer:
        return False
    context_text = re.sub(r"\s+", " ", "\n".join(item["value"] for item in contexts).casefold())
    phrases = [part.strip(" .;,") for part in re.split(r"[;,]", normalized_answer) if part.strip(" .;,")]
    return bool(phrases) and all(phrase in context_text for phrase in phrases)


def evaluate_questions(
    trajectories_path: Path,
    questions_path: Path,
    work_dir: Path,
    limit: Optional[int] = None,
    context_limit: int = 8,
) -> Dict[str, object]:
    trajectories = load_jsonl(trajectories_path)
    questions = load_jsonl(questions_path)
    if limit is not None:
        questions = questions[: max(0, int(limit))]
    backend = LongMemEvalV2Backend(
        {"workspace_dir": str(work_dir), "namespace": "external-benchmark", "context_limit": context_limit}
    )
    insert_results = []
    try:
        for trajectory in trajectories:
            insert_results.append(backend.insert(trajectory))
        cases = []
        latencies = []
        schema_valid = 0
        lexical_covered = 0
        for question in questions:
            backend.set_query_context(
                question_id=question.get("id"),
                question_type=question.get("question_type"),
                raw_question_item=question,
            )
            started = perf_counter()
            contexts = backend.query(str(question.get("question") or ""), query_image=question.get("image"))
            latency = (perf_counter() - started) * 1000.0
            latencies.append(latency)
            issues = validate_context_items(contexts)
            covered = _answer_lexically_covered(question.get("answer"), contexts)
            schema_valid += int(not issues)
            lexical_covered += int(covered)
            cases.append(
                {
                    "question_id": question.get("id"),
                    "domain": question.get("domain"),
                    "question_type": question.get("question_type"),
                    "context_count": len(contexts),
                    "context_schema_issues": issues,
                    "answer_lexically_covered": covered,
                    "query_latency_ms": round(latency, 3),
                }
            )
            backend.clear_query_context()
    finally:
        backend.close()
    count = len(questions)
    return {
        "schema_version": "1.0",
        "provenance": {
            "repository": LONGMEMEVAL_V2_REPOSITORY,
            "dataset": LONGMEMEVAL_V2_DATASET,
            "code_revision": LONGMEMEVAL_V2_CODE_REVISION,
            "trajectories_sha256": file_sha256(trajectories_path),
            "questions_sha256": file_sha256(questions_path),
        },
        "dataset": {
            "trajectory_count": len(trajectories),
            "question_count": count,
            "inserted_chunk_count": sum(int(item["stored_count"]) for item in insert_results),
        },
        "retrieval_layer": {
            "status": "completed",
            "question_count": count,
            "context_schema_valid_rate": round(schema_valid / float(count or 1), 6),
            "answer_lexical_coverage_rate": round(lexical_covered / float(count or 1), 6),
            "p50_latency_ms": round(percentile(latencies, 0.50), 3),
            "p95_latency_ms": round(percentile(latencies, 0.95), 3),
            "p99_latency_ms": round(percentile(latencies, 0.99), 3),
            "metric_warning": "lexical coverage is a retrieval proxy and is not answer accuracy",
        },
        "answer_layer": {
            "status": "not_run",
            "accuracy": None,
            "reason": "reader and official answer evaluator were not configured",
        },
        "cases": cases,
    }


def to_markdown(result: Dict[str, object]) -> str:
    provenance = result["provenance"]
    data = result["dataset"]
    retrieval = result["retrieval_layer"]
    return "\n".join(
        [
            "# LongMemEval-V2 外部交叉验证报告",
            "",
            "本报告使用官方 LongMemEval-V2 数据和 MemGuardOS 后端。检索代理指标与端到端答案准确率严格分开。",
            "",
            "| 项目 | 结果 |",
            "|---|---:|",
            "| 官方代码 revision | `%s` |" % provenance["code_revision"],
            "| 轨迹数 | %d |" % data["trajectory_count"],
            "| 问题数 | %d |" % data["question_count"],
            "| 写入记忆块 | %d |" % data["inserted_chunk_count"],
            "| context schema 有效率 | %.4f |" % retrieval["context_schema_valid_rate"],
            "| 答案短语词面覆盖率（代理指标） | %.4f |" % retrieval["answer_lexical_coverage_rate"],
            "| P50 查询延迟 | %.3f ms |" % retrieval["p50_latency_ms"],
            "| P95 查询延迟 | %.3f ms |" % retrieval["p95_latency_ms"],
            "| P99 查询延迟 | %.3f ms |" % retrieval["p99_latency_ms"],
            "| 官方 reader/judge 答案准确率 | 未运行 |",
            "",
            "> 词面覆盖只说明检索上下文是否包含参考答案短语，不能替代官方 reader 与答案评估器给出的准确率。",
        ]
    ) + "\n"
