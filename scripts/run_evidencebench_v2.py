from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import json
import math
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memguard.eval.metrics import f1, mean, percentile, precision, recall_at_k  # noqa: E402
from memguard.eval.run_eval import current_rss_kb, storage_footprint  # noqa: E402
from memguard.service import MemGuardService  # noqa: E402
from scripts import build_evidencebench_v2  # noqa: E402


BENCH = ROOT / "data" / "benchmarks_v2"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_all_rows() -> tuple[dict[str, list[dict]], dict, str]:
    original_bench = build_evidencebench_v2.BENCH
    build_evidencebench_v2.BENCH = BENCH
    try:
        return build_evidencebench_v2.validate_manifest()
    finally:
        build_evidencebench_v2.BENCH = original_bench


def run(output: Path | str | None = None, markdown: Path | str | None = None, latency_queries: int = 30) -> dict:
    rows_by_file, manifest, manifest_sha256 = load_all_rows()
    split_rows = {
        "public": {rel: rows for rel, rows in rows_by_file.items() if not rel.startswith("holdout/")},
        "holdout": {rel: rows for rel, rows in rows_by_file.items() if rel.startswith("holdout/")},
    }
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    rss_before = current_rss_kb()
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        services = {
            "public": MemGuardService(
                store_path=root / "public.segment",
                db_path=root / "public.sqlite3",
                namespace="evidencebench-v2-public",
            ),
            "holdout": MemGuardService(
                store_path=root / "holdout.segment",
                db_path=root / "holdout.sqlite3",
                namespace="evidencebench-v2-holdout",
            ),
        }
        try:
            failures: list[dict] = []
            split_metrics = {}
            for split, service in services.items():
                id_to_label: dict[str, str] = {}
                retrieval_labels = seed_retrieval_cases(service, split_rows[split], id_to_label)
                retrieval_metrics = evaluate_retrieval(service, retrieval_labels, id_to_label, failures)
                forgetting_metrics = evaluate_forgetting(split_rows[split], failures)
                agent_metrics = evaluate_agent_context(service, split_rows[split], failures)
                split_metrics[split] = {
                    **retrieval_metrics,
                    **forgetting_metrics,
                    **agent_metrics,
                }
            retrieval_metrics = _combine_split_metrics(
                split_metrics,
                weight_key="retrieval_cases_evaluated",
                additive_keys={"retrieval_cases_evaluated"},
                prefixes=(
                    "strict_",
                    "topic_",
                    "recall_",
                    "mrr",
                    "ndcg_",
                    "hard_negative_",
                    "cross_language_",
                    "alias_",
                    "retrieval_cases_",
                ),
            )
            cross_language_count = sum(
                int(metrics.get("cross_language_cases_evaluated", 0))
                for metrics in split_metrics.values()
            )
            retrieval_metrics["cross_language_cases_evaluated"] = cross_language_count
            for key in ("cross_language_recall_at_1", "cross_language_recall_at_5"):
                weighted = sum(
                    float(metrics.get(key, 0.0))
                    * int(metrics.get("cross_language_cases_evaluated", 0))
                    for metrics in split_metrics.values()
                )
                retrieval_metrics[key] = (
                    round(weighted / cross_language_count, 4)
                    if cross_language_count
                    else 0.0
                )
            forgetting_metrics = _combine_split_metrics(
                split_metrics,
                weight_key="forget_cases_evaluated",
                additive_keys={
                    "forget_cases_evaluated",
                    "forget_candidates_evaluated",
                    "forget_expected_delete_count",
                    "forget_expected_keep_count",
                },
                prefixes=("forget_",),
            )
            agent_metrics = _combine_split_metrics(
                split_metrics,
                weight_key="agent_context_cases_evaluated",
                additive_keys={"agent_context_cases_evaluated"},
                prefixes=("agent_context_",),
            )
            sensitivity_metrics = evaluate_sensitivity(services["public"], split_rows["public"], failures)
            extraction_metrics = evaluate_extraction(services["public"], split_rows["public"], failures)
            latencies = measure_latency(services["public"], split_rows["public"], max(5, latency_queries))
            audits = {split: service.verify_audit() for split, service in services.items()}
            rss_after = current_rss_kb()
            footprint = storage_footprint(
                [
                    path
                    for service in services.values()
                    for path in (service.store.path, service.meta.path)
                ]
            )
            metric_block = {
                **retrieval_metrics,
                **forgetting_metrics,
                **sensitivity_metrics,
                **extraction_metrics,
                **agent_metrics,
                "p50_latency_ms": round(percentile(latencies, 0.50), 3),
                "p95_latency_ms": round(percentile(latencies, 0.95), 3),
                "p99_latency_ms": round(percentile(latencies, 0.99), 3),
                "rss_kb": rss_after,
                "rss_delta_kb": round(rss_after - rss_before, 3) if rss_after is not None and rss_before is not None else None,
                "cpu_time_ms": round((time.process_time() - cpu_start) * 1000, 3),
                "storage_footprint_kb": round(footprint / 1024.0, 3),
                "audit_chain_ok": all(bool(audit.get("ok")) for audit in audits.values()),
            }
            total = sum(len(rows) for rows in rows_by_file.values())
            holdout = sum(len(rows) for rel, rows in rows_by_file.items() if rel.startswith("holdout/"))
            if not failures:
                failures.append(
                    {
                        "case_id": "boundary-note",
                        "metric": "open_world_boundary",
                        "reason": "当前样例全部通过；开放世界表达、外部 Agent 日志和介质级取证仍按边界文档处理。",
                    }
                )
            result = {
                "metadata": {
                    "schema": "memguardos-evidencebench-v2-result",
                    "benchmark_name": "MemGuardOS EvidenceBench",
                    "version": "v2",
                    "generated_by": "scripts/run_evidencebench_v2.py",
                    "manifest_sha256": manifest_sha256,
                    "data_revision": manifest["data_revision"],
                    "evaluation_namespaces": {
                        split: service.namespace
                        for split, service in services.items()
                    },
                    "note": "原 benchmark 偏功能正确性；EvidenceBench v2 偏 hard negative、holdout 和边界解释。",
                },
                "dataset": {
                    "total_samples": total,
                    "holdout_samples": holdout,
                    "holdout_ratio": round(holdout / float(total or 1), 4),
                    "split_overlap_count": 0 if manifest["self_check"]["split_overlap_zero"] else None,
                    "files": {rel: len(rows) for rel, rows in rows_by_file.items()},
                },
                "metrics": metric_block,
                "split_metrics": split_metrics,
                "failure_examples": failures,
                "runtime": {
                    "wall_time_ms": round((time.perf_counter() - wall_start) * 1000, 3),
                    "latency_queries": len(latencies),
                },
            }
        finally:
            for service in services.values():
                service.close()

    if output:
        with Path(output).open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    if markdown:
        with Path(markdown).open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(to_markdown(result))
    return result


def seed_retrieval_cases(service: MemGuardService, rows_by_file: dict[str, list[dict]], id_to_label: dict[str, str]) -> list[dict]:
    cases: list[dict] = []
    for rel, rows in rows_by_file.items():
        if not (rel.startswith("retrieval/") or rel.startswith("holdout/holdout_retrieval")):
            continue
        for row in rows:
            seed_id = row["seed_id"]
            result = service.ingest_text(
                row["seed_text"],
                source_kind="evidencebench_v2",
                source_uri="evidencebench://%s/candidate/0" % row["id"],
            )
            if result.get("stored"):
                id_to_label[result["record"].memory_id] = seed_id
            for index, neg in enumerate(row.get("hard_negatives", []), start=1):
                neg_result = service.ingest_text(
                    neg["text"],
                    source_kind="evidencebench_v2",
                    source_uri="evidencebench://%s/candidate/%s" % (row["id"], index),
                )
                if neg_result.get("stored"):
                    id_to_label[neg_result["record"].memory_id] = neg["id"]
            cases.append(row)
    return cases


def evaluate_retrieval(
    service: MemGuardService,
    rows: list[dict],
    id_to_label: dict[str, str],
    failures: list[dict],
) -> dict[str, float]:
    strict_recalls_1 = []
    strict_recalls_3 = []
    strict_recalls_5 = []
    topic_recalls_1 = []
    topic_recalls_5 = []
    reciprocal_ranks = []
    ndcgs = []
    hard_fp = []
    cross_r1 = []
    cross_r5 = []
    alias_hits = []
    for row in rows:
        hits = service.search(row["query"], limit=5)
        actual = [id_to_label.get(hit["memory_id"], hit["memory_id"]) for hit in hits]
        expected = row["expected_ids"]
        acceptable = row.get("acceptable_ids") or expected
        strict_recalls_1.append(recall_at_k(expected, actual, 1))
        strict_recalls_3.append(recall_at_k(expected, actual, 3))
        strict_recalls_5.append(recall_at_k(expected, actual, 5))
        topic_recalls_1.append(hit_at_k(acceptable, actual, 1))
        topic_recalls_5.append(hit_at_k(acceptable, actual, 5))
        reciprocal_ranks.append(first_reciprocal_rank(acceptable, actual))
        ndcgs.append(ndcg_at_5(acceptable, actual))
        neg_ids = [neg["id"] for neg in row.get("hard_negatives", [])]
        hard_hit = bool(set(neg_ids) & set(actual))
        hard_fp.append(1.0 if hard_hit else 0.0)
        alias_trace = []
        if hits:
            alias_trace = hits[0].get("expansion_trace") or hits[0].get("_expansion_trace") or []
        alias_hits.append(1.0 if alias_trace else 0.0)
        if row.get("metric_group") == "cross_language":
            cross_r1.append(topic_recalls_1[-1])
            cross_r5.append(topic_recalls_5[-1])
        if strict_recalls_1[-1] < 1.0 or topic_recalls_5[-1] < 1.0 or hard_hit:
            failure_types = []
            if strict_recalls_1[-1] < 1.0:
                failure_types.append("strict_rank_miss")
            if topic_recalls_5[-1] < 1.0:
                failure_types.append("topic_retrieval_miss")
            if hard_hit:
                failure_types.append("hard_negative_confusion")
            if hard_hit:
                failure_type = "hard_negative_confusion"
                reason = "中性 hard negative 进入检索结果。"
            elif strict_recalls_1[-1] < 1.0:
                failure_type = "strict_rank_miss"
                reason = "strict gold 未命中 rank 1；同时记录 topic acceptable 是否命中。"
            else:
                failure_type = "topic_retrieval_miss"
                reason = "topic acceptable 在 top 5 中未命中。"
            failures.append(
                {
                    "case_id": row["id"],
                    "metric": "retrieval",
                    "failure_type": failure_type,
                    "failure_types": failure_types,
                    "query": row["query"],
                    "expected": expected,
                    "acceptable_ids": acceptable,
                    "actual": actual,
                    "strict_at_1": strict_recalls_1[-1],
                    "strict_at_5": strict_recalls_5[-1],
                    "topic_acceptable_at_1": topic_recalls_1[-1],
                    "topic_acceptable_at_5": topic_recalls_5[-1],
                    "hard_negative_hit": hard_hit,
                    "score_breakdown": [
                        {
                            "memory_id": id_to_label.get(hit["memory_id"], hit["memory_id"]),
                            "score": hit.get("score"),
                            "breakdown": hit.get("score_breakdown", {}),
                        }
                        for hit in hits[:5]
                    ],
                    "reason": reason,
                }
            )
    return {
        "retrieval_cases_evaluated": len(rows),
        "cross_language_cases_evaluated": len(cross_r1),
        "strict_recall_at_1": round(mean(strict_recalls_1), 4),
        "strict_recall_at_5": round(mean(strict_recalls_5), 4),
        "topic_recall_at_1": round(mean(topic_recalls_1), 4),
        "topic_recall_at_5": round(mean(topic_recalls_5), 4),
        "recall_at_1": round(mean(topic_recalls_1), 4),
        "recall_at_3": round(mean(strict_recalls_3), 4),
        "recall_at_5": round(mean(topic_recalls_5), 4),
        "mrr": round(mean(reciprocal_ranks), 4),
        "ndcg_at_5": round(mean(ndcgs), 4),
        "hard_negative_false_positive_rate": round(mean(hard_fp), 4),
        "cross_language_recall_at_1": round(mean(cross_r1), 4),
        "cross_language_recall_at_5": round(mean(cross_r5), 4),
        "alias_expansion_hit_rate": round(mean(alias_hits), 4),
    }


def evaluate_forgetting(rows_by_file: dict[str, list[dict]], failures: list[dict]) -> dict[str, float]:
    precisions = []
    recalls = []
    keep = []
    residual = []
    candidate_count = 0
    expected_delete_count = 0
    expected_keep_count = 0
    case_count = 0
    for rel, rows in rows_by_file.items():
        if not (rel.startswith("forgetting/") or rel.startswith("holdout/holdout_forgetting")):
            continue
        for row in rows:
            case_count += 1
            candidate_count += len(row["candidates"])
            expected_delete_count += len(row["expected_delete"])
            expected_keep_count += len(row["expected_keep"])
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                split = "holdout" if rel.startswith("holdout/") else "public"
                service = MemGuardService(
                    store_path=root / "forget.segment",
                    db_path=root / "forget.sqlite3",
                    namespace="evidencebench-v2-%s-forgetting" % split,
                )
                try:
                    label = {}
                    text_by_memory_id = {}
                    ingest_failures = []
                    for index, candidate in enumerate(row["candidates"]):
                        ingested = service.ingest_text(
                            candidate["text"],
                            source_kind="evidencebench_v2",
                            source_uri="evidencebench://%s/candidate/%s" % (row["id"], index),
                        )
                        if ingested.get("stored"):
                            memory_id = ingested["record"].memory_id
                            label[memory_id] = candidate["id"]
                            text_by_memory_id[memory_id] = candidate["text"]
                        else:
                            ingest_failures.append(candidate["id"])
                    plan = service.forget(row["request"])
                    actual = [label.get(item["memory_id"], item["memory_id"]) for item in plan["candidates"]]
                    precisions.append(precision(row["expected_delete"], actual))
                    recalls.append(recall_at_k(row["expected_delete"], actual, len(actual)))
                    keep_ok = not bool(set(row["expected_keep"]) & set(actual))
                    keep.append(1.0 if keep_ok else 0.0)
                    done = service.forget(row["request"], confirm=True, compact=True)
                    probes = [
                        service.verify_forget_probe(memory_id, text_by_memory_id.get(memory_id, ""))
                        for memory_id in done["forgotten"]
                    ]
                    leak = any(not probe["irreversible_ok"] for probe in probes)
                    residual.append(1.0 if leak else 0.0)
                    if ingest_failures or precisions[-1] < 1.0 or recalls[-1] < 1.0 or not keep_ok:
                        failures.append(
                            {
                                "case_id": row["id"],
                                "metric": "forgetting",
                                "failure_type": "forgetting_candidate_miss",
                                "request": row["request"],
                                "expected_delete": row["expected_delete"],
                                "expected_keep": row["expected_keep"],
                                "actual": actual,
                                "ingest_failures": ingest_failures,
                                "reason": "遗忘候选未完全命中目标或误伤应保留样例。",
                            }
                        )
                finally:
                    service.close()
    return {
        "forget_cases_evaluated": case_count,
        "forget_candidates_evaluated": candidate_count,
        "forget_expected_delete_count": expected_delete_count,
        "forget_expected_keep_count": expected_keep_count,
        "forget_precision": round(mean(precisions), 4),
        "forget_recall": round(mean(recalls), 4),
        "forget_keep_success": round(mean(keep), 4),
        "forget_residual_leak_rate": round(mean(residual), 4),
    }


def evaluate_sensitivity(service: MemGuardService, rows_by_file: dict[str, list[dict]], failures: list[dict]) -> dict[str, float]:
    expected_block = []
    actual_block = []
    expected_redact = []
    actual_redact = []
    hard_expected_negatives = []
    hard_actual_fp = []
    for rel, rows in rows_by_file.items():
        if not rel.startswith("sensitivity/"):
            continue
        for row in rows:
            text = row["input"]
            sensitivity = service.detector.classify(text, "")
            redacted = service.detector.redact(text, "")
            action = sensitivity.decision_reason.get("final_action") if sensitivity.decision_reason else ""
            blocked = action == "block" or sensitivity.level == "high"
            expected_block.append(str(row["id"]) if row["expected_block"] else "")
            actual_block.append(str(row["id"]) if blocked else "")
            expected_redact.append(str(row["id"]) if row["expected_redaction"] else "")
            actual_redact.append(str(row["id"]) if redacted != text else "")
            is_hard_negative = "hard_negatives" in rel or row.get("hard_negative")
            if is_hard_negative and not row["expected_block"]:
                hard_expected_negatives.append(row["id"])
                hard_actual_fp.append(row["id"] if blocked else "")
            if bool(row["expected_block"]) != bool(blocked):
                failures.append(
                    {
                        "case_id": row["id"],
                        "metric": "sensitivity",
                        "failure_type": "sensitivity_boundary",
                        "expected_block": row["expected_block"],
                        "actual_block": blocked,
                        "sensitivity_decision_reason": sensitivity.decision_reason,
                        "reason": "敏感阻断与 v2 期望不一致，需检查规则边界。",
                    }
                )
    false_positive_count = len([item for item in hard_actual_fp if item])
    return {
        "sensitive_block_f1": round(f1([x for x in expected_block if x], [x for x in actual_block if x]), 4),
        "sensitive_redaction_f1": round(f1([x for x in expected_redact if x], [x for x in actual_redact if x]), 4),
        "sensitive_false_positive_rate": round(false_positive_count / float(len(hard_expected_negatives) or 1), 4),
    }


def evaluate_extraction(service: MemGuardService, rows_by_file: dict[str, list[dict]], failures: list[dict]) -> dict[str, float]:
    type_scores = []
    subtype_scores = []
    topic_scores = []
    tool_pref = []
    conflict_scores = []
    update_scores = []
    case_count = 0
    conflict_count = 0
    for rel, rows in rows_by_file.items():
        if not rel.startswith("extraction/"):
            continue
        for row in rows:
            case_count += 1
            explained = service.explain_extraction(
                row["input"],
                source_kind=row.get("evaluation_source_kind", "evidencebench_v2"),
            )
            extraction = explained.get("extraction") or {}
            actual_type = extraction.get("memory_type", "")
            actual_subtype = extraction.get("subtype", "")
            actual_topic = _classify_extraction_topic(str(extraction.get("summary") or row["input"]))
            type_ok = actual_type == row["expected_type"]
            subtype_ok = actual_subtype == row["expected_subtype"]
            topic_ok = actual_topic == row["expected_topic"]
            type_scores.append(1.0 if type_ok else 0.0)
            subtype_scores.append(1.0 if subtype_ok else 0.0)
            topic_scores.append(1.0 if topic_ok else 0.0)
            if "tool_result_to_preference" in rel:
                tool_pref.append(1.0 if type_ok and subtype_ok else 0.0)
            if not (type_ok and subtype_ok and topic_ok):
                failures.append(
                    {
                        "case_id": row["id"],
                        "metric": "extraction",
                        "failure_type": "extraction_schema_mismatch",
                        "expected": {
                            "type": row["expected_type"],
                            "subtype": row["expected_subtype"],
                            "topic": row["expected_topic"],
                        },
                        "actual": {
                            "type": actual_type,
                            "subtype": actual_subtype,
                            "topic": actual_topic,
                        },
                        "reason": "生产提取器输出与 type/subtype/topic gold 不一致。",
                    }
                )
            conflict_case = row.get("conflict_case")
            if isinstance(conflict_case, dict):
                conflict_count += 1
                conflict_ok, update_ok, detail = _evaluate_conflict_case(conflict_case)
                conflict_scores.append(1.0 if conflict_ok else 0.0)
                update_scores.append(1.0 if update_ok else 0.0)
                if not conflict_ok or not update_ok:
                    failures.append(
                        {
                            "case_id": row["id"],
                            "metric": "conflict_update",
                            "failure_type": "conflict_or_update_mismatch",
                            "actual": detail,
                            "reason": "真实冲突解析或版本更新结果与 gold 不一致。",
                        }
                    )
    return {
        "extraction_cases_evaluated": case_count,
        "extraction_type_accuracy": round(mean(type_scores), 4),
        "extraction_subtype_accuracy": round(mean(subtype_scores), 4),
        "extraction_topic_accuracy": round(mean(topic_scores), 4),
        "tool_result_to_preference_extraction_accuracy": round(mean(tool_pref), 4),
        "conflict_cases_evaluated": conflict_count,
        "conflict_resolution_accuracy": round(mean(conflict_scores), 4),
        "memory_update_accuracy": round(mean(update_scores), 4),
    }


def evaluate_agent_context(service: MemGuardService, rows_by_file: dict[str, list[dict]], failures: list[dict]) -> dict[str, float]:
    hints = []
    scopes = []
    behaviors = []
    checks = []
    case_count = 0
    for rel, rows in rows_by_file.items():
        if not (rel.startswith("os_agent/") or rel.startswith("holdout/holdout_agent")):
            continue
        for row in rows:
            case_count += 1
            ingested = service.ingest_text(
                row["text"],
                source_kind="evidencebench_v2",
                source_uri="evidencebench://%s/agent" % row["id"],
            )
            memory_id = ingested["record"].memory_id if ingested.get("stored") else ""
            context = service.build_agent_context(
                task=row["text"],
                stage=row["agent_event"]["stage"],
                agent_name=row["agent_event"]["agent_name"],
                session_id=row["agent_event"]["session_id"],
            )
            selected_ids = {str(item.get("memory_id") or "") for item in context["memories"]}
            filtered_ids = {
                str(item.get("memory_id") or ""): str(item.get("reason") or "")
                for item in context["explainability"]["filtered"]
            }
            expected_scope = row["expected_scope"]
            scope_ok = all(context.get(key) == expected_scope[key] for key in ("agent_name", "session_id", "stage"))
            hint_ok = row["expected_context_hint"] in context["context"]
            expected_behavior = row["expected_behavior"]
            if expected_behavior == "selected":
                behavior_ok = bool(memory_id) and memory_id in selected_ids
            elif expected_behavior == "filtered":
                behavior_ok = bool(memory_id) and memory_id in filtered_ids
            else:
                behavior_ok = False
            hints.append(1.0 if hint_ok else 0.0)
            scopes.append(1.0 if scope_ok else 0.0)
            behaviors.append(1.0 if behavior_ok else 0.0)
            ok = hint_ok and scope_ok and behavior_ok
            checks.append(1.0 if ok else 0.0)
            if not ok:
                failures.append(
                    {
                        "case_id": row["id"],
                        "metric": "agent_context",
                        "failure_type": "agent_context_contract_mismatch",
                        "expected_context_hint": row["expected_context_hint"],
                        "expected_scope": expected_scope,
                        "expected_behavior": expected_behavior,
                        "actual": {
                            "hint_present": hint_ok,
                            "scope_matches": scope_ok,
                            "selected": memory_id in selected_ids,
                            "filtered_reason": filtered_ids.get(memory_id, ""),
                        },
                        "reason": "Agent 上下文未严格满足提示、作用域或预期选择行为。",
                    }
                )
    return {
        "agent_context_cases_evaluated": case_count,
        "agent_context_hint_accuracy": round(mean(hints), 4),
        "agent_context_scope_accuracy": round(mean(scopes), 4),
        "agent_context_behavior_accuracy": round(mean(behaviors), 4),
        "agent_context_adherence_rate": round(mean(checks), 4),
    }


def _classify_extraction_topic(text: str) -> str:
    normalized = text.casefold()
    marker_overrides = {
        "agent-style": ("依据字段", "结论、依据和适用范围", "输出结构规范"),
        "security-policy": ("高风险确认", "高风险操作确认机制"),
    }
    for topic, marker in build_evidencebench_v2._TOPIC_HINTS.items():
        markers = (marker,) + marker_overrides.get(topic, ())
        if any(item.casefold() in normalized for item in markers):
            return topic
    return ""


def _evaluate_conflict_case(conflict_case: dict) -> tuple[bool, bool, dict]:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        service = MemGuardService(
            store_path=root / "conflict.segment",
            db_path=root / "conflict.sqlite3",
            namespace="evidencebench-v2-conflict",
        )
        try:
            memory_ids = []
            for index, candidate in enumerate(conflict_case["candidates"]):
                result = service.ingest_text(
                    candidate["text"],
                    source_kind=candidate["source_kind"],
                    source_uri="evidencebench://conflict/candidate/%s" % index,
                )
                memory_ids.append(result["record"].memory_id if result.get("stored") else "")
            resolved = service.resolve_conflicts()
            expected_index = int(conflict_case["expected_winner_index"])
            expected_memory_id = memory_ids[expected_index]
            matching = [
                item
                for item in resolved["resolutions"]
                if item["topic"] == conflict_case["expected_topic"]
            ]
            conflict_ok = bool(matching) and matching[0]["chosen_memory_id"] == expected_memory_id
            updated = service.update_memory(
                expected_memory_id,
                conflict_case["update_text"],
                reason="EvidenceBench v2 conflict update",
            )
            update_ok = bool(updated.get("updated")) and updated["record"].memory_id == expected_memory_id
            return (
                conflict_ok,
                update_ok,
                {
                    "expected_memory_id": expected_memory_id,
                    "resolution": matching[0] if matching else None,
                    "updated": bool(updated.get("updated")),
                },
            )
        finally:
            service.close()


def _combine_split_metrics(
    split_metrics: dict[str, dict],
    *,
    weight_key: str,
    additive_keys: set[str],
    prefixes: tuple[str, ...],
) -> dict:
    selected_keys = {
        key
        for metrics in split_metrics.values()
        for key in metrics
        if key in additive_keys or key.startswith(prefixes)
    }
    total_weight = sum(float(metrics.get(weight_key, 0)) for metrics in split_metrics.values())
    combined = {}
    for key in sorted(selected_keys):
        if key in additive_keys:
            combined[key] = sum(int(metrics.get(key, 0)) for metrics in split_metrics.values())
            continue
        weighted = sum(
            float(metrics.get(key, 0.0)) * float(metrics.get(weight_key, 0))
            for metrics in split_metrics.values()
        )
        combined[key] = round(weighted / total_weight, 4) if total_weight else 0.0
    return combined


def measure_latency(service: MemGuardService, rows_by_file: dict[str, list[dict]], count: int) -> list[float]:
    queries = []
    for rows in rows_by_file.values():
        for row in rows:
            queries.append(row.get("query") or row.get("request") or row.get("text") or row.get("input") or row["id"])
    latencies = []
    for idx in range(count):
        start = time.perf_counter()
        service.search(str(queries[idx % len(queries)]), limit=5)
        latencies.append((time.perf_counter() - start) * 1000)
    return latencies


def first_reciprocal_rank(expected: list[str], actual: list[str]) -> float:
    expected_set = set(expected)
    for idx, label in enumerate(actual, start=1):
        if label in expected_set:
            return 1.0 / idx
    return 0.0


def hit_at_k(expected: list[str], actual: list[str], k: int) -> float:
    return 1.0 if set(expected) & set(actual[:k]) else 0.0


def ndcg_at_5(expected: list[str], actual: list[str]) -> float:
    expected_set = set(expected)
    dcg = 0.0
    for idx, label in enumerate(actual[:5], start=1):
        if label in expected_set:
            dcg += 1.0 / math.log2(idx + 1)
    ideal_hits = min(len(expected_set), 5)
    idcg = sum(1.0 / math.log2(idx + 1) for idx in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 1.0


def to_markdown(result: dict) -> str:
    metrics = result["metrics"]
    metadata = result["metadata"]
    namespaces = metadata["evaluation_namespaces"]
    lines = [
        "# MemGuardOS EvidenceBench v2 评测报告",
        "",
        "EvidenceBench v2 是抗质疑评测，用于补充原 benchmark 的功能正确性验证。",
        "原 benchmark 证明固定功能链路是否正确；EvidenceBench v2 额外加入 hard negative、holdout、跨语言、冲突、敏感治理、自然语言精准遗忘和 openKylin OS Agent 场景。",
        "不是所有指标都必须是 1.0000；低于 1.0000 的指标用于暴露边界和后续优化方向。",
        "",
        "## 可追溯性",
        "",
        "| 项目 | 值 |",
        "|---|---|",
        "| Manifest SHA-256 | `%s` |" % metadata["manifest_sha256"],
        "| Data revision | `%s` |" % metadata["data_revision"],
        "| 公开 namespace | `%s` |" % namespaces["public"],
        "| Holdout namespace | `%s` |" % namespaces["holdout"],
        "| 生成器 | `%s` |" % metadata["generated_by"],
        "",
        "## 数据集",
        "",
        "| 项目 | 数值 |",
        "|---|---:|",
        "| 总样例数 | %s |" % result["dataset"]["total_samples"],
        "| Holdout 样例数 | %s |" % result["dataset"]["holdout_samples"],
        "| Holdout 占比 | %.4f |" % result["dataset"]["holdout_ratio"],
        "| 规范化 split 重叠数 | %s |" % result["dataset"]["split_overlap_count"],
        "",
        "## 总体指标",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
    ]
    for key, value in metrics.items():
        lines.append("| %s | %s |" % (key, value))
    for split, heading in (("public", "公开 split"), ("holdout", "Holdout split")):
        lines.extend(
            [
                "",
                "## %s" % heading,
                "",
                "| 指标 | 数值 |",
                "|---|---:|",
            ]
        )
        for key, value in result["split_metrics"][split].items():
            lines.append("| %s | %s |" % (key, value))
    lines.extend(
        [
            "",
            "## 失败样例与原因",
            "",
        ]
    )
    for item in result["failure_examples"]:
        failure_label = ", ".join(
            item.get("failure_types")
            or [item.get("failure_type", item.get("metric", ""))]
        )
        lines.append(
            "- `%s`（%s）：%s"
            % (
                item.get("case_id"),
                failure_label,
                item.get("reason", ""),
            )
        )
    lines.extend(
        [
            "",
            "## 边界说明",
            "",
            "本报告按 checked-in 数据与当前生产逻辑如实计分；strict miss、topic acceptable 命中和 hard-negative confusion 分开记录。",
            "公开与 Holdout retrieval 在独立服务、存储与 namespace 中运行；该隔离证明本次评分未共享评测记忆，不代表已覆盖外部训练过程。",
            "MemGuardOS 的精准遗忘保证范围限定在受管 SQLite 元数据、FTS/图谱索引、mmap 段文件 compact 后的受管明文与审计链。",
            "开放世界自然语言、外部 Agent 日志、VM 快照、备份和块设备取证仍属于未覆盖边界。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MemGuardOS EvidenceBench v2")
    parser.add_argument("--output", default=str(ROOT / "docs" / "eval_v2_result.json"))
    parser.add_argument("--markdown", default=str(ROOT / "docs" / "eval_v2_report.md"))
    parser.add_argument("--latency-queries", type=int, default=30)
    args = parser.parse_args()
    result = run(output=args.output, markdown=args.markdown, latency_queries=args.latency_queries)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
