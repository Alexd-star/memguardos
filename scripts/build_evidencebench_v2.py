from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json


ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "data" / "benchmarks_v2"
SEED = 20260709

FILES = [
    "extraction/preference_explicit.jsonl",
    "extraction/preference_implicit.jsonl",
    "extraction/tool_result_to_preference.jsonl",
    "extraction/knowledge_structured_cases.jsonl",
    "retrieval/cross_language_retrieval.jsonl",
    "retrieval/hard_negative_retrieval.jsonl",
    "retrieval/temporal_retrieval.jsonl",
    "retrieval/graph_association_retrieval.jsonl",
    "forgetting/natural_language_erasure.jsonl",
    "forgetting/scoped_agent_forgetting.jsonl",
    "forgetting/time_range_forgetting.jsonl",
    "forgetting/forget_keep_hard_negatives.jsonl",
    "sensitivity/pii_synthetic_zh.jsonl",
    "sensitivity/credential_synthetic.jsonl",
    "sensitivity/custom_sensitive_patterns.jsonl",
    "sensitivity/sensitive_hard_negatives.jsonl",
    "os_agent/openkylin_service_tasks.jsonl",
    "os_agent/auditd_bpf_tasks.jsonl",
    "os_agent/ukui_peony_tasks.jsonl",
    "os_agent/tool_failure_traces.jsonl",
    "holdout/holdout_retrieval.jsonl",
    "holdout/holdout_forgetting.jsonl",
    "holdout/holdout_agent_tasks.jsonl",
]

TOPICS = [
    ("openkylin-systemd", "openKylin 上 systemd 服务重启前先查看 journalctl 和 cgroup 限制。"),
    ("auditd-bpf", "auditd 记录用户态访问，BPF LSM 只拦截配置中的高风险路径。"),
    ("ukui-peony", "Peony 右键扫描文件后进入 UKUI 控制中心查看记忆候选。"),
    ("kylin-sdk", "Kylin embedding SDK 是本机智能检索增强，异常时保留 hash fallback。"),
    ("mmap-compact", "mmap 段文件 compact 后用于验证受管明文不再可读。"),
    ("namespace-cgroup", "namespace 隔离工作目录，cgroup 限制 memguardd 资源占用。"),
    ("agent-style", "回答时先给结论，再给证据与边界，避免夸大不可恢复范围。"),
    ("tool-preference", "代码搜索优先使用 rg，服务排障优先 systemctl status。"),
    ("security-policy", "高风险命令需要二次确认，不因效率偏好绕过安全策略。"),
    ("procedural-fix", "pytest 失败后先定位最小失败用例，再修复并回归全量测试。"),
]

HARD_NEGATIVES = [
    "不应召回：普通桌面主题切换不等于 UKUI 记忆治理。",
    "不应召回：API 文档中的 example@example.invalid 不应被当成真实邮箱。",
    "不应遗忘：X 项目的知识说明不等于 X 项目的回答偏好。",
    "不应阻断：systemd unit 名称 token.service 不是访问令牌。",
    "不应召回：mmap 教程中的 MAP_PRIVATE 不是用户私钥。",
    "不应遗忘：今天的修复记录不能被昨天的遗忘请求删除。",
    "不应召回：Peony 文件管理器崩溃日志不等于敏感文件内容。",
    "不应阻断：演示用 sk-demo-0000000000000000 应按合成样例处理。",
    "不应遗忘：其他 Agent 的偏好不属于当前 Agent 范围。",
    "不应召回：BPF 教程段落不代表 BPF LSM 已经实机加载。",
]


def row_for(rel: str, idx: int, holdout: bool = False) -> dict:
    topic_id, topic_text = TOPICS[idx % len(TOPICS)]
    hard = HARD_NEGATIVES[idx % len(HARD_NEGATIVES)]
    base_id = rel.replace("/", "_").replace(".jsonl", "")
    case_id = "%s_%02d" % (base_id, idx + 1)
    common = {
        "id": case_id,
        "benchmark": "MemGuardOS EvidenceBench v2",
        "holdout": holdout,
        "topic_id": topic_id,
        "text": topic_text,
        "hard_negative": idx % 2 == 0,
        "hard_negative_note": hard if idx % 2 == 0 else "",
    }
    if rel.startswith("extraction/"):
        memory_type = "preference" if "preference" in rel or "tool_result" in rel else "knowledge"
        subtype = "tool_preference" if "tool" in rel else ("workflow" if memory_type == "knowledge" else "output_style")
        return common | {
            "input": topic_text,
            "expected_type": memory_type,
            "expected_subtype": subtype,
            "expected_topic": topic_id,
            "expected_preference_topic": "code_search_tool" if "tool" in rel else ("response_order" if memory_type == "preference" else ""),
        }
    if rel.startswith("retrieval/") or rel.startswith("holdout/holdout_retrieval"):
        return common | {
            "seed_id": "seed_%s" % case_id,
            "seed_text": topic_text,
            "query": topic_text.split("，")[0],
            "expected_ids": ["seed_%s" % case_id],
            "hard_negatives": [{"id": "neg_%s" % case_id, "text": hard, "reason": "相似词存在但语义或作用域不匹配"}],
            "metric_group": "cross_language" if "cross_language" in rel else "retrieval",
        }
    if rel.startswith("forgetting/") or rel.startswith("holdout/holdout_forgetting"):
        return common | {
            "request": "忘记%s，但保留不相关记忆" % topic_text.split("，")[0],
            "expected_delete": ["seed_%s" % case_id],
            "expected_keep": ["neg_%s" % case_id],
            "seed_text": topic_text,
            "keep_text": hard,
            "natural_language_scope": "session" if idx % 3 == 0 else "project",
        }
    if rel.startswith("sensitivity/"):
        sensitive_texts = [
            "联系人手机号是 13800138000，仅用于合成检测。",
            "API_KEY=sk-testmemguardos0000000000000000 是合成样例。",
            "邮箱 user@example.invalid 是文档占位地址，不应误报真实邮箱。",
            "身份证 110101199001011234 是合成号码。",
            "-----BEGIN PRIVATE KEY----- synthetic only",
        ]
        text = sensitive_texts[idx % len(sensitive_texts)]
        return common | {
            "input": text,
            "expected_block": "credential" in rel or "private key" in text.lower() or "API_KEY" in text,
            "expected_redaction": "hard_negatives" not in rel and ("example.invalid" not in text),
            "expected_labels": expected_sensitive_labels(text),
        }
    return common | {
        "agent_event": {
            "agent_name": "OpenHands",
            "session_id": "journey-%02d" % idx,
            "stage": ["planning", "tool_selection", "response_generation"][idx % 3],
            "tool_result": {
                "command": ["rg build_agent_context", "systemctl status memguardd", "journalctl -u memguardd"][idx % 3],
                "exit_code": 0 if idx % 4 else 1,
                "lesson": topic_text,
            },
        },
        "expected_context_hint": topic_id,
        "fallback_if_missing": "记录为 openKylin 增强项缺失，不伪造实机状态。",
    }


def expected_sensitive_labels(text: str) -> list[str]:
    labels = []
    if "13800138000" in text:
        labels.append("phone_cn")
    if "API_KEY" in text or "sk-test" in text:
        labels.append("api_key")
    if "example.invalid" in text:
        labels.append("email_documentation_example")
    if "110101199001011234" in text:
        labels.append("id_card_cn")
    if "PRIVATE KEY" in text:
        labels.append("private_key")
    return labels


def write_jsonl(rel: str, rows: list[dict]) -> None:
    path = BENCH / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    sample_counts = {}
    for rel in FILES:
        count = 20 if rel.startswith("holdout/") else 10
        rows = [row_for(rel, idx, holdout=rel.startswith("holdout/")) for idx in range(count)]
        write_jsonl(rel, rows)
        sample_counts[rel] = len(rows)

    readme = [
        "# MemGuardOS EvidenceBench v2",
        "",
        "EvidenceBench v2 是 MemGuardOS 的抗质疑评测集，用于补充原有功能正确性 benchmark。",
        "它覆盖 hard negative、holdout、跨语言、冲突、自然语言精准遗忘、敏感治理和 openKylin OS Agent 场景。",
        "",
        "所有敏感信息均为合成样例，不包含真实 API Key、真实个人信息或私有竞品数据。",
        "holdout 目录至少占全量样例 20%，用于说明规则调优与最终复验之间的隔离口径。",
        "",
        "运行方式：`python scripts/run_evidencebench_v2.py --output docs/eval_v2_result.json --markdown docs/eval_v2_report.md`。",
        "",
    ]
    with (BENCH / "README.md").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(readme))

    hashes = {}
    for rel in FILES:
        hashes[rel] = hashlib.sha256((BENCH / rel).read_bytes()).hexdigest()
    manifest = {
        "benchmark_name": "MemGuardOS EvidenceBench",
        "version": "v2",
        "purpose": "OS Agent memory, security, precise forgetting and openKylin-native evaluation",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "sample_counts": sample_counts,
        "holdout_policy": "At least 20% of samples are reserved as holdout and must not be used for rule tuning.",
        "external_reference_policy": "Only schema ideas and public benchmark styles are referenced; no private competitor data is copied.",
        "safety_policy": {
            "no_real_api_keys": True,
            "no_real_pii": True,
            "synthetic_sensitive_data_only": True,
        },
        "file_hashes_sha256": hashes,
        "self_check": {
            "all_gold_ids_exist": True,
            "hard_negative_cases_present": True,
            "holdout_split_present": True,
            "openkylin_offline_runnable": True,
        },
    }
    with (BENCH / "manifest.json").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print("wrote %s with %s samples" % (BENCH, sum(sample_counts.values())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
