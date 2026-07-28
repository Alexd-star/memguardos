from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import sys
import unicodedata


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
    "桌面主题切换记录只描述配色和壁纸设置。",
    "API 文档使用保留域名作为占位邮箱。",
    "X 项目保存了部署拓扑说明，没有记录回答风格。",
    "systemd 单元 token.service 当前处于 active 状态。",
    "mmap 教程介绍 MAP_PRIVATE 的写时复制语义。",
    "今天的修复记录包含新的回归测试结果。",
    "Peony 文件管理器崩溃日志只包含调用栈。",
    "演示凭据字段使用固定占位符用于文档截图。",
    "另一个 Agent 偏好在回答前先列出假设。",
    "BPF 教程解释 verifier 的基本检查流程。",
]

HOLDOUT_TOPICS = [
    ("service-socket", "用户级 memguard-agent socket 激活失败时，检查 listen path 与 linger 状态。"),
    ("audit-rule-reload", "更新 audit 规则后先运行语法检查，再确认 augenrules 已加载新规则。"),
    ("peony-quarantine", "Peony 扫描隔离文件后，只在确认页展示来源与命中规则。"),
    ("embedding-fallback", "向量后端超时时记录降级原因，并用本地词法索引完成本次查询。"),
    ("segment-verification", "压缩段文件后重新扫描旧明文片段，并核对 tombstone 对应的审计事件。"),
    ("namespace-export", "导出命名空间前核对目标目录权限，避免把其他用户的记忆一并打包。"),
    ("answer-boundary", "生成结论时明确区分已验证事实、推断结果和未覆盖的外部日志。"),
    ("test-runner", "Python 回归测试统一通过 python -m pytest 调用，避免解释器环境漂移。"),
    ("destructive-confirm", "批量删除候选必须展示数量和作用域，并在确认后才执行 compact。"),
    ("incident-minimize", "复现服务故障时先保留最小日志窗口，再逐步加入环境变量与插件。"),
    ("journal-rate-limit", "journald 触发速率限制时，分别检查 unit 配置和全局限流参数。"),
    ("bpf-map-pin", "BPF map 固定路径变更后，验证旧 pin 已清理且新程序引用正确对象。"),
    ("ukui-autostart", "UKUI 托盘未启动时，核对 autostart 文件、桌面会话变量和进程退出码。"),
    ("store-migration", "元数据迁移前创建只读备份，迁移后比较记录数和审计链头。"),
    ("scope-precedence", "会话偏好覆盖项目偏好时，解释选择顺序并保留被排除候选的原因。"),
    ("redaction-preview", "敏感文本入库前先展示脱敏预览，阻断项不得进入长期记忆。"),
    ("agent-replay", "重放 Agent 事件时按 sequence 排序，并拒绝重复 event_id。"),
    ("resource-budget", "压力测试同时记录 p95 延迟、RSS 增量和存储增长，不只看平均值。"),
    ("rollback-window", "回滚记忆版本后校验有效时间窗，旧版本仍应保持 superseded 状态。"),
    ("offline-evidence", "实机增强不可用时输出缺失证据，不用模拟结果替代 openKylin 状态。"),
]

HOLDOUT_HARD_NEGATIVES = [
    "壁纸轮换计划只影响桌面外观。",
    "软件仓库镜像地址用于包下载配置。",
    "文件缩略图缓存记录不包含隔离判定。",
    "离线词典索引用于拼写提示。",
    "归档压缩率统计不代表明文残留检查。",
    "共享目录清单属于文件同步配置。",
    "会议纪要模板包含结论与行动项标题。",
    "JavaScript 测试由 npm test 负责执行。",
    "相册批量移除只删除本地缩略图。",
    "完整系统日志用于长期容量趋势分析。",
    "终端配色方案定义警告文本颜色。",
    "网络命名空间示例展示 veth 配置。",
    "登录提示音设置属于桌面会话偏好。",
    "数据库连接池迁移不改变记忆记录。",
    "全局快捷键优先级由窗口管理器处理。",
    "图像水印预览不涉及敏感文本分类。",
    "录像回放按帧时间戳排序。",
    "磁盘基准测试只报告吞吐和队列深度。",
    "软件包降级会恢复上一版本二进制。",
    "离线演示页面展示静态状态截图。",
]

_TOPIC_HINTS = {
    "openkylin-systemd": "journalctl",
    "auditd-bpf": "BPF LSM",
    "ukui-peony": "UKUI",
    "kylin-sdk": "hash fallback",
    "mmap-compact": "compact",
    "namespace-cgroup": "cgroup",
    "agent-style": "证据与边界",
    "tool-preference": "systemctl status",
    "security-policy": "二次确认",
    "procedural-fix": "最小失败用例",
    "service-socket": "linger",
    "audit-rule-reload": "augenrules",
    "peony-quarantine": "确认页",
    "embedding-fallback": "词法索引",
    "segment-verification": "tombstone",
    "namespace-export": "目标目录权限",
    "answer-boundary": "未覆盖的外部日志",
    "test-runner": "python -m pytest",
    "destructive-confirm": "compact",
    "incident-minimize": "最小日志窗口",
    "journal-rate-limit": "限流参数",
    "bpf-map-pin": "pin",
    "ukui-autostart": "autostart",
    "store-migration": "审计链头",
    "scope-precedence": "选择顺序",
    "redaction-preview": "脱敏预览",
    "agent-replay": "event_id",
    "resource-budget": "RSS",
    "rollback-window": "superseded",
    "offline-evidence": "缺失证据",
}

_CROSS_LANGUAGE_QUERIES = {
    "openkylin-systemd": "Which logs and resource limits should be checked before restarting an openKylin service?",
    "auditd-bpf": "How are auditd observation and BPF LSM enforcement separated?",
    "ukui-peony": "Where can a user review memory candidates after a Peony file scan?",
    "kylin-sdk": "What fallback remains available when the local embedding backend fails?",
    "mmap-compact": "How is managed plaintext absence verified after segment compaction?",
    "namespace-cgroup": "How are workspace isolation and daemon resource limits combined?",
    "agent-style": "What answer structure distinguishes conclusions, evidence, and boundaries?",
    "tool-preference": "Which tools are preferred for code search and service diagnostics?",
    "security-policy": "What confirmation is required before a high-risk command?",
    "procedural-fix": "What is the first step after a pytest failure?",
}

_KNOWLEDGE_EXTRACTION_CASES = {
    "agent-style": ("Agent 输出结构规范包含结论、依据和适用范围三个字段。", "fact"),
    "security-policy": ("高风险操作确认机制由治理模块统一记录。", "fact"),
    "procedural-fix": ("pytest 回归流程包括最小失败用例定位与全量运行验证。", "workflow"),
}

_AGENT_HINTS = {
    **_TOPIC_HINTS,
    "agent-style": "依据字段",
    "security-policy": "高风险确认",
    "redaction-preview": "预览结果",
}

_SYNTHETIC_CREDENTIALS = [
    "API_KEY=sk-testmemguardos0000000000000000 是合成检测值。",
    "API_TOKEN=sk-evalmemguardos1111111111111111 是合成检测值。",
    "PASSWORD=synthetic-pass-12345 是合成检测值。",
    "-----BEGIN PRIVATE KEY----- synthetic evaluation only",
    "AWS_ACCESS_KEY_ID=AKIA1234567890ABCDEF 是合成检测值。",
]

_SYNTHETIC_PII = [
    "联系人手机号是 13800138000，仅用于合成检测。",
    "身份证 110101199001011234 是合成号码。",
    "邮箱 user@example.invalid 是文档占位地址。",
    "银行卡号 6222 0202 1234 5678 901 是合成号码。",
    "联系人：张三，仅用于合成检测。",
    "地址：北京市海淀区中关村路1号，仅用于合成检测。",
    "备用手机号 13912345678 是合成号码。",
    "身份证 310101198812123456 是合成号码。",
    "邮箱 demo@example.invalid 是文档占位地址。",
    "联系人：李四，仅用于合成检测。",
]


class EvidenceBenchValidationError(ValueError):
    """Raised when checked-in benchmark assets violate their trust contract."""


_CREDENTIAL_PATTERNS = {
    "api_key": re.compile(r"\b(?:(?:sk|ak|pk|hf|ghp|github_pat)[_-][A-Za-z0-9_\-]{16,}|sk-[A-Za-z0-9_\-]{16,})\b"),
    "aws_access_key_id": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "token_assignment": re.compile(
        r"(?i)\b(?:[A-Z0-9_]*api[_-]?key|[A-Z0-9_]*token|[A-Z0-9_]*secret|[A-Z0-9_]*password)"
        r"\s*[:=]\s*['\"]?[^'\"\s]{8,}"
    ),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}

_PII_PATTERNS = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "phone_cn": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "id_card_cn": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "bank_card": re.compile(r"(?<!\d)(?:\d{4}[- ]?){3,4}\d{3}(?!\d)"),
    "person_name_cn": re.compile(r"(?:姓名|名字|收件人|联系人)\s*(?:是|为|:|：)\s*[\u4e00-\u9fff]{2,4}"),
    "address_cn": re.compile(r"(?:地址|住址|收货地址)\s*(?:是|为|:|：)\s*[\u4e00-\u9fffA-Za-z0-9#_\-号栋单元室路街区县市省]{8,80}"),
}

_MANIFEST_REQUIRED_KEYS = {
    "benchmark_name",
    "data_revision",
    "external_reference_policy",
    "file_hashes_sha256",
    "generated_at",
    "holdout_policy",
    "purpose",
    "safety_policy",
    "sample_counts",
    "seed",
    "self_check",
    "version",
}
_SPLIT_SCAFFOLDING = (
    "holdout",
    "public",
    "留出集",
    "留出",
    "公开集",
    "公开",
    "复核",
    "样例",
    "a组",
    "b组",
)


def row_for(rel: str, idx: int, holdout: bool = False) -> dict:
    topics = HOLDOUT_TOPICS if holdout else TOPICS
    topic_id, topic_text = topics[idx % len(topics)]
    negatives = HOLDOUT_HARD_NEGATIVES if holdout else HARD_NEGATIVES
    hard = negatives[idx % len(negatives)]
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
        if "tool_result" in rel:
            text = "以后处理%s相关任务时，代码搜索请优先使用 rg。" % topic_text.rstrip("。")
            memory_type = "preference"
            subtype = "tool_preference"
            source_kind = "tool_result"
        elif "preference_explicit" in rel:
            subject = _TOPIC_HINTS[topic_id]
            text = "以后回答%s相关问题时，请先给结论，再说明依据和适用范围。" % subject
            memory_type = "preference"
            subtype = "output_style"
            source_kind = "evidencebench_v2"
        elif "preference_implicit" in rel:
            subject = _TOPIC_HINTS[topic_id]
            text = "我偏好用简洁中文回答%s相关问题，并保留风险提醒。" % subject
            memory_type = "preference"
            subtype = "output_style"
            source_kind = "evidencebench_v2"
        else:
            text, subtype = _KNOWLEDGE_EXTRACTION_CASES.get(
                topic_id,
                (
                    "工作流程：处理%s相关问题时，先采集证据，再按步骤验证结果。"
                    % _TOPIC_HINTS[topic_id],
                    "workflow",
                ),
            )
            memory_type = "knowledge"
            source_kind = "evidencebench_v2"
        row = common | {
            "input": text,
            "text": text,
            "expected_type": memory_type,
            "expected_subtype": subtype,
            "expected_topic": topic_id,
            "expected_preference_topic": "code_search_tool" if "tool" in rel else ("response_order" if memory_type == "preference" else ""),
            "evaluation_source_kind": source_kind,
        }
        if "preference_explicit" in rel and idx < 5:
            row["conflict_case"] = {
                "candidates": [
                    {
                        "source_kind": "benchmark",
                        "text": "以后代码搜索优先使用 grep，并记录匹配行号。",
                    },
                    {
                        "source_kind": "manual",
                        "text": "以后代码搜索优先使用 rg，并记录匹配行号。",
                    },
                ],
                "expected_winner_index": 1,
                "expected_topic": "code_search_tool",
                "update_text": "以后代码搜索优先使用 rg，并在结果中显示文件路径。",
            }
        return row
    if rel.startswith("retrieval/") or rel.startswith("holdout/holdout_retrieval"):
        seed_id = "seed_%s" % case_id
        if holdout:
            seed_text = topic_text
            query = "请检索与%s直接相关的操作证据。" % _TOPIC_HINTS[topic_id]
        elif "cross_language" in rel:
            seed_text = "跨语言术语记录：%s" % topic_text
            query = _CROSS_LANGUAGE_QUERIES[topic_id]
        elif "hard_negative" in rel:
            seed_text = "困难负例任务：%s" % topic_text
            query = "%s 的直接操作要求" % _TOPIC_HINTS[topic_id]
        elif "temporal" in rel:
            seed_text = "本周有效记录：%s" % topic_text
            query = "本周 %s" % _TOPIC_HINTS[topic_id]
        else:
            seed_text = "关联图记录：%s" % topic_text
            query = "%s 的关联组件与步骤" % _TOPIC_HINTS[topic_id]
        return common | {
            "seed_id": seed_id,
            "seed_text": seed_text,
            "text": seed_text,
            "query": query,
            "expected_ids": [seed_id],
            "acceptable_ids": [seed_id],
            "evaluation_mode": "strict",
            "hard_negatives": [{"id": "neg_%s" % case_id, "text": hard, "reason": "相似词存在但语义或作用域不匹配"}],
            "metric_group": "cross_language" if "cross_language" in rel else "retrieval",
        }
    if rel.startswith("forgetting/") or rel.startswith("holdout/holdout_forgetting"):
        seed_id = "seed_%s_primary" % case_id
        delete_peer_id = "seed_%s_peer" % case_id
        keep_id = "keep_%s_primary" % case_id
        keep_peer_id = "keep_%s_peer" % case_id
        request_target = _TOPIC_HINTS[topic_id]
        prefix = {
            "forgetting/natural_language_erasure.jsonl": "自然语言请求",
            "forgetting/scoped_agent_forgetting.jsonl": "当前 Agent 会话",
            "forgetting/time_range_forgetting.jsonl": "上周记录",
            "forgetting/forget_keep_hard_negatives.jsonl": "保留约束",
            "holdout/holdout_forgetting.jsonl": "独立留出任务",
        }.get(rel, "遗忘任务")
        return common | {
            "request": "忘记%s中关于%s的两条记忆，但保留其他主题。" % (prefix, request_target),
            "expected_delete": [seed_id, delete_peer_id],
            "expected_keep": [keep_id, keep_peer_id],
            "candidates": [
                {"id": seed_id, "text": "%s：%s" % (prefix, topic_text)},
                {
                    "id": delete_peer_id,
                    "text": "%s补充：%s 是本任务必须复核的关键点。" % (prefix, request_target),
                },
                {"id": keep_id, "text": hard},
                {
                    "id": keep_peer_id,
                    "text": "独立主题记录：%s" % negatives[(idx + 3) % len(negatives)],
                },
            ],
            "evaluation_mode": "strict",
            "seed_text": "%s：%s" % (prefix, topic_text),
            "keep_text": hard,
            "natural_language_scope": "session" if idx % 3 == 0 else "project",
        }
    if rel.startswith("sensitivity/"):
        if "credential_synthetic" in rel:
            text = _SYNTHETIC_CREDENTIALS[idx % len(_SYNTHETIC_CREDENTIALS)]
            expected_block = True
            expected_redaction = True
        elif "sensitive_hard_negatives" in rel:
            text = hard
            expected_block = False
            expected_redaction = False
        else:
            text = _SYNTHETIC_PII[idx % len(_SYNTHETIC_PII)]
            expected_block = False
            expected_redaction = "example.invalid" not in text
        return common | {
            "input": text,
            "expected_block": expected_block,
            "expected_redaction": expected_redaction,
            "expected_labels": expected_sensitive_labels(text),
            "synthetic_values": synthetic_values(text),
        }
    agent_prefix = {
        "os_agent/openkylin_service_tasks.jsonl": "服务生命周期任务",
        "os_agent/auditd_bpf_tasks.jsonl": "审计与拦截任务",
        "os_agent/ukui_peony_tasks.jsonl": "桌面集成任务",
        "os_agent/tool_failure_traces.jsonl": "失败恢复任务",
        "holdout/holdout_agent_tasks.jsonl": "独立留出 Agent 任务",
    }.get(rel, "Agent 任务")
    agent_hint = _AGENT_HINTS[topic_id]
    agent_text = "工作流程：%s处理 %s 时，先收集状态，再验证结果。" % (agent_prefix, agent_hint)
    return common | {
        "text": agent_text,
        "agent_event": {
            "agent_name": "OpenHands",
            "session_id": "journey-%02d" % idx,
            "stage": ["planning", "tool_selection", "response_generation"][idx % 3],
            "tool_result": {
                "command": ["rg build_agent_context", "systemctl status memguardd", "journalctl -u memguardd"][idx % 3],
                "exit_code": 0 if idx % 4 else 1,
                "lesson": agent_text,
            },
        },
        "expected_context_hint": agent_hint,
        "expected_behavior": "selected",
        "expected_scope": {
            "agent_name": "OpenHands",
            "session_id": "journey-%02d" % idx,
            "stage": ["planning", "tool_selection", "response_generation"][idx % 3],
        },
        "fallback_if_missing": "记录为 openKylin 增强项缺失，不伪造实机状态。",
    }


def expected_sensitive_labels(text: str) -> list[str]:
    labels = []
    for label, pattern in (*_CREDENTIAL_PATTERNS.items(), *_PII_PATTERNS.items()):
        if pattern.search(text):
            labels.append(label)
    if "example.invalid" in text:
        labels = ["email_documentation_example" if label == "email" else label for label in labels]
    return sorted(set(labels))


def synthetic_values(text: str) -> list[str]:
    values = []
    for pattern in (*_CREDENTIAL_PATTERNS.values(), *_PII_PATTERNS.values()):
        values.extend(match.group(0) for match in pattern.finditer(text))
    return sorted(set(values))


def write_jsonl(rel: str, rows: list[dict]) -> None:
    path = BENCH / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _load_rows() -> dict[str, list[dict]]:
    actual_files = {
        path.relative_to(BENCH).as_posix()
        for path in BENCH.rglob("*.jsonl")
        if path.is_file()
    }
    expected_files = set(FILES)
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        extra = sorted(actual_files - expected_files)
        raise EvidenceBenchValidationError(
            "benchmark JSONL file set mismatch; missing=%s extra=%s"
            % (missing, extra)
        )
    rows_by_file: dict[str, list[dict]] = {}
    for rel in FILES:
        path = BENCH / rel
        if not path.is_file():
            raise EvidenceBenchValidationError("missing benchmark file: %s" % rel)
        rows: list[dict] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EvidenceBenchValidationError("%s:%s: invalid JSON: %s" % (rel, line_number, exc.msg)) from exc
            if not isinstance(row, dict):
                raise EvidenceBenchValidationError("%s:%s: each JSONL row must be an object" % (rel, line_number))
            rows.append(row)
        rows_by_file[rel] = rows
    return rows_by_file


def _declared_ids(row: dict, key: str, rel: str) -> list[str]:
    value = row.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise EvidenceBenchValidationError("%s:%s: %s must be a list of non-empty strings" % (rel, row.get("id", "?"), key))
    return value


def _required_string(row: dict, key: str, rel: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EvidenceBenchValidationError("%s:%s: %s must be a non-empty string" % (rel, row.get("id", "?"), key))
    return value


def _required_ids(row: dict, key: str, rel: str) -> list[str]:
    values = _declared_ids(row, key, rel)
    if not values:
        raise EvidenceBenchValidationError("%s:%s: %s must not be empty" % (rel, row.get("id", "?"), key))
    return values


def _structured_records(
    row: dict,
    key: str,
    rel: str,
    *,
    required: bool,
    nonempty: bool,
) -> list[dict]:
    if key not in row:
        if required:
            raise EvidenceBenchValidationError("%s:%s: %s is required" % (rel, row.get("id", "?"), key))
        return []
    value = row[key]
    if not isinstance(value, list):
        raise EvidenceBenchValidationError("%s:%s: %s must be a structured list" % (rel, row.get("id", "?"), key))
    if nonempty and not value:
        raise EvidenceBenchValidationError("%s:%s: %s must not be empty" % (rel, row.get("id", "?"), key))
    output: list[dict] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise EvidenceBenchValidationError(
                "%s:%s: %s[%s] must be an object" % (rel, row.get("id", "?"), key, index)
            )
        for field in ("id", "text"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise EvidenceBenchValidationError(
                    "%s:%s: %s[%s].%s must be a non-empty string"
                    % (rel, row.get("id", "?"), key, index, field)
                )
        output.append(item)
    return output


def _task_kind(rel: str) -> str:
    if rel.startswith("retrieval/") or rel == "holdout/holdout_retrieval.jsonl":
        return "retrieval"
    if rel.startswith("forgetting/") or rel == "holdout/holdout_forgetting.jsonl":
        return "forgetting"
    return "other"


def _iter_string_values(value: object, *, skip_keys: set[str] | None = None):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if skip_keys and key in skip_keys:
                continue
            yield from _iter_string_values(item, skip_keys=skip_keys)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_string_values(item, skip_keys=skip_keys)


def _match_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _scan_safety(rows_by_file: dict[str, list[dict]]) -> tuple[dict[str, bool], list[str]]:
    credential_findings: list[str] = []
    pii_findings: list[str] = []
    for rel, rows in rows_by_file.items():
        for row in rows:
            case_id = str(row.get("id") or "?")
            raw_allowed = row.get("synthetic_values", [])
            if not isinstance(raw_allowed, list) or any(not isinstance(item, str) for item in raw_allowed):
                credential_findings.append("%s:%s:invalid_synthetic_values" % (rel, case_id))
                raw_allowed = []
            allowed = {_match_key(item) for item in raw_allowed}
            for text in _iter_string_values(row, skip_keys={"synthetic_values", "hard_negative_note"}):
                for label, pattern in _CREDENTIAL_PATTERNS.items():
                    for match in pattern.finditer(text):
                        if _match_key(match.group(0)) not in allowed:
                            credential_findings.append("%s:%s:%s" % (rel, case_id, label))
                for label, pattern in _PII_PATTERNS.items():
                    for match in pattern.finditer(text):
                        if _match_key(match.group(0)) not in allowed:
                            pii_findings.append("%s:%s:%s" % (rel, case_id, label))
    policy = {
        "no_real_api_keys": not credential_findings,
        "no_real_pii": not pii_findings,
        "synthetic_sensitive_data_only": not credential_findings and not pii_findings,
    }
    failures = []
    if credential_findings:
        failures.append("possible real credentials found at %s" % ", ".join(credential_findings[:5]))
    if pii_findings:
        failures.append("possible real PII found at %s" % ", ".join(pii_findings[:5]))
    return policy, failures


def _normalize_split_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    compact = "".join(char for char in normalized if char.isalnum())
    for marker in _SPLIT_SCAFFOLDING:
        compact = compact.replace(
            "".join(char for char in marker.casefold() if char.isalnum()),
            "",
        )
    return compact


def _split_text_values(rel: str, row: dict) -> list[tuple[str, str]]:
    task_kind = _task_kind(rel)
    values = []
    if task_kind == "retrieval":
        values.extend((key, row[key]) for key in ("seed_text", "query") if isinstance(row.get(key), str))
    elif task_kind == "forgetting":
        if isinstance(row.get("request"), str):
            values.append(("request", row["request"]))
        for index, candidate in enumerate(row.get("candidates", [])):
            if isinstance(candidate, dict) and isinstance(candidate.get("text"), str):
                values.append(("candidates[%s].text" % index, candidate["text"]))
    elif rel.startswith("os_agent/") or rel == "holdout/holdout_agent_tasks.jsonl":
        if isinstance(row.get("text"), str):
            values.append(("text", row["text"]))
        event = row.get("agent_event")
        if isinstance(event, dict):
            tool_result = event.get("tool_result")
            if isinstance(tool_result, dict) and isinstance(tool_result.get("lesson"), str):
                values.append(("agent_event.tool_result.lesson", tool_result["lesson"]))
    return values


def _validate_dataset(rows_by_file: dict[str, list[dict]]) -> tuple[dict[str, bool], dict[str, bool]]:
    retrieval_seed_ids = {"public": set(), "holdout": set()}
    retrieval_gold: list[tuple[str, str, str, str]] = []
    strict_expected_failures: list[tuple[str, str, str, str]] = []
    forgetting_gold: list[tuple[str, str, str]] = []
    hard_negative_cases = 0
    total = 0
    holdout = 0
    offline_rows: list[dict] = []
    split_texts = {"public": {}, "holdout": {}}
    answer_leakage: list[str] = []

    for rel, rows in rows_by_file.items():
        expected_holdout = rel.startswith("holdout/")
        task_kind = _task_kind(rel)
        for row in rows:
            total += 1
            case_id = str(row.get("id") or "")
            if row.get("holdout") is not expected_holdout:
                raise EvidenceBenchValidationError(
                    "%s:%s: holdout must be %s to match its directory"
                    % (rel, case_id or "?", str(expected_holdout).lower())
                )
            if expected_holdout:
                holdout += 1
            split = "holdout" if expected_holdout else "public"
            for field, value in _split_text_values(rel, row):
                normalized = _normalize_split_text(value)
                if normalized:
                    split_texts[split].setdefault(normalized, []).append(
                        "%s:%s:%s" % (rel, case_id or "?", field)
                    )
            if rel.startswith("os_agent/") or rel == "holdout/holdout_agent_tasks.jsonl":
                offline_rows.append(row)

            negatives = _structured_records(
                row,
                "hard_negatives",
                rel,
                required=task_kind == "retrieval",
                nonempty=False,
            )
            if row.get("hard_negative") is True or negatives:
                hard_negative_cases += 1
            for negative in negatives:
                if negative["text"].startswith(("不应召回", "不应遗忘", "不应阻断")):
                    answer_leakage.append("%s:%s:hard_negatives" % (rel, case_id or "?"))

            if task_kind == "retrieval":
                seed_id = _required_string(row, "seed_id", rel)
                _required_string(row, "seed_text", rel)
                _required_string(row, "query", rel)
                expected_ids = _required_ids(row, "expected_ids", rel)
                acceptable_ids = _required_ids(row, "acceptable_ids", rel)
                retrieval_seed_ids[split].add(seed_id)
                strict_expected_failures.extend(
                    (rel, case_id or "?", item, seed_id)
                    for item in expected_ids
                    if item != seed_id
                )
                retrieval_gold.extend((split, rel, case_id or "?", item) for item in expected_ids)
                retrieval_gold.extend((split, rel, case_id or "?", item) for item in acceptable_ids)
            elif task_kind == "forgetting":
                _required_string(row, "request", rel)
                candidates = _structured_records(row, "candidates", rel, required=True, nonempty=True)
                expected_delete = _required_ids(row, "expected_delete", rel)
                expected_keep = _required_ids(row, "expected_keep", rel)
                candidate_ids = {item["id"] for item in candidates}
                candidate_text = {item["id"]: item["text"] for item in candidates}
                for keep_id in expected_keep:
                    if candidate_text.get(keep_id, "").startswith(("不应召回", "不应遗忘", "不应阻断")):
                        answer_leakage.append("%s:%s:expected_keep" % (rel, case_id or "?"))
                forgetting_gold.extend(
                    (rel, case_id or "?", item)
                    for item in (*expected_delete, *expected_keep)
                    if item not in candidate_ids
                )
            elif rel.startswith("extraction/"):
                _required_string(row, "input", rel)
                expected_type = _required_string(row, "expected_type", rel)
                expected_subtype = _required_string(row, "expected_subtype", rel)
                _required_string(row, "expected_topic", rel)
                _required_string(row, "evaluation_source_kind", rel)
                if expected_type not in {"knowledge", "preference", "sensitive_policy"}:
                    raise EvidenceBenchValidationError(
                        "%s:%s: unsupported expected_type %s"
                        % (rel, case_id or "?", expected_type)
                    )
                if expected_subtype not in {
                    "behavior_risk",
                    "behavior_trace",
                    "case",
                    "fact",
                    "general",
                    "os_event",
                    "output_style",
                    "procedural_trace",
                    "security_policy",
                    "template",
                    "tool_failure_case",
                    "tool_preference",
                    "tool_trace",
                    "user_habit",
                    "workflow",
                }:
                    raise EvidenceBenchValidationError(
                        "%s:%s: unsupported expected_subtype %s"
                        % (rel, case_id or "?", expected_subtype)
                    )
            elif rel.startswith("os_agent/") or rel == "holdout/holdout_agent_tasks.jsonl":
                _required_string(row, "text", rel)
                _required_string(row, "expected_context_hint", rel)
                behavior = _required_string(row, "expected_behavior", rel)
                if behavior not in {"selected", "filtered"}:
                    raise EvidenceBenchValidationError(
                        "%s:%s: expected_behavior must be selected or filtered"
                        % (rel, case_id or "?")
                    )
                scope = row.get("expected_scope")
                event = row.get("agent_event")
                if not isinstance(scope, dict) or not isinstance(event, dict):
                    raise EvidenceBenchValidationError(
                        "%s:%s: expected_scope and agent_event must be objects"
                        % (rel, case_id or "?")
                    )
                for key in ("agent_name", "session_id", "stage"):
                    if not isinstance(scope.get(key), str) or not scope[key]:
                        raise EvidenceBenchValidationError(
                            "%s:%s: expected_scope.%s must be a non-empty string"
                            % (rel, case_id or "?", key)
                        )
                    if scope[key] != event.get(key):
                        raise EvidenceBenchValidationError(
                            "%s:%s: expected_scope.%s must match agent_event"
                            % (rel, case_id or "?", key)
                        )

    unreachable_retrieval = [
        "%s:%s -> %s" % (item[1], item[2], item[3])
        for item in retrieval_gold
        if item[3] not in retrieval_seed_ids[item[0]]
    ]
    split_overlap = sorted(set(split_texts["public"]) & set(split_texts["holdout"]))
    unreachable_forgetting = ["%s:%s -> %s" % item for item in forgetting_gold]
    holdout_ratio = holdout / float(total or 1)
    openkylin_offline_runnable = bool(offline_rows) and all(
        isinstance(row.get("fallback_if_missing"), str) and bool(row["fallback_if_missing"].strip())
        for row in offline_rows
    )
    safety_policy, safety_failures = _scan_safety(rows_by_file)
    checks = {
        "all_gold_ids_exist": not unreachable_retrieval and not unreachable_forgetting,
        "agent_schema_valid": True,
        "answer_leakage_absent": not answer_leakage,
        "extraction_schema_valid": True,
        "forgetting_schema_valid": True,
        "hard_negative_cases_present": hard_negative_cases > 0,
        "hard_negatives_structured": True,
        "holdout_flags_consistent": True,
        "holdout_split_present": holdout_ratio >= 0.20,
        "openkylin_offline_runnable": openkylin_offline_runnable,
        "retrieval_schema_valid": True,
        "safety_policy_verified": all(safety_policy.values()),
        "split_gold_isolated": not unreachable_retrieval,
        "split_overlap_zero": not split_overlap,
        "topic_acceptable_ids_present": True,
    }

    failures = []
    if unreachable_retrieval:
        failures.append("retrieval gold IDs are outside their split: %s" % ", ".join(unreachable_retrieval[:5]))
    if strict_expected_failures:
        failures.append(
            "strict retrieval expected IDs must reference their own row seed: %s"
            % ", ".join(
                "%s:%s -> %s (seed %s)" % item
                for item in strict_expected_failures[:5]
            )
        )
    if unreachable_forgetting:
        failures.append("forgetting gold IDs are outside row candidates: %s" % ", ".join(unreachable_forgetting[:5]))
    if not checks["hard_negative_cases_present"]:
        failures.append("no hard-negative cases are present")
    if not checks["holdout_split_present"]:
        failures.append("holdout ratio %.2f%% is below 20%%" % (holdout_ratio * 100.0))
    if not checks["openkylin_offline_runnable"]:
        failures.append("openKylin offline cases must provide fallback_if_missing")
    if answer_leakage:
        failures.append("hard-negative answer leakage found at %s" % ", ".join(answer_leakage[:5]))
    if split_overlap:
        examples = []
        for key in split_overlap[:5]:
            examples.append(
                "%s <> %s"
                % (
                    split_texts["public"][key][0],
                    split_texts["holdout"][key][0],
                )
            )
        failures.append("public/holdout normalized text overlap: %s" % ", ".join(examples))
    failures.extend(safety_failures)
    if failures:
        raise EvidenceBenchValidationError("; ".join(failures))
    return checks, safety_policy


def _dataset_self_check(rows_by_file: dict[str, list[dict]]) -> dict[str, bool]:
    self_check, _ = _validate_dataset(rows_by_file)
    return self_check


def _valid_iso_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _source_generated_at(
    existing: dict | None,
    *,
    data_unchanged: bool,
    data_revision: str,
) -> str:
    source_date_epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if source_date_epoch is not None:
        try:
            timestamp = int(source_date_epoch)
            return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError) as exc:
            raise EvidenceBenchValidationError("SOURCE_DATE_EPOCH must be a valid integer timestamp") from exc
    if data_unchanged and _valid_iso_timestamp((existing or {}).get("generated_at")):
        return str(existing["generated_at"])
    seconds = int(data_revision[:12], 16) % (200 * 365 * 24 * 60 * 60)
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def _data_revision(sample_counts: dict[str, int], file_hashes: dict[str, str]) -> str:
    payload = {
        "files": [
            {
                "path": rel,
                "samples": sample_counts[rel],
                "sha256": file_hashes[rel],
            }
            for rel in FILES
        ],
        "seed": SEED,
        "version": "v2",
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest_payload(
    sample_counts: dict[str, int],
    existing: dict | None = None,
    *,
    self_check: dict[str, bool] | None = None,
    safety_policy: dict[str, bool] | None = None,
    data_unchanged: bool = False,
) -> dict:
    manifest = dict(existing or {})
    if self_check is None or safety_policy is None:
        computed_self_check, computed_safety_policy = _validate_dataset(_load_rows())
    else:
        computed_self_check, computed_safety_policy = self_check, safety_policy
    file_hashes = {rel: hashlib.sha256((BENCH / rel).read_bytes()).hexdigest() for rel in FILES}
    data_revision = _data_revision(sample_counts, file_hashes)
    manifest.update(
        {
            "benchmark_name": "MemGuardOS EvidenceBench",
            "version": "v2",
            "purpose": "OS Agent memory, security, precise forgetting and openKylin-native evaluation",
            "generated_at": _source_generated_at(
                existing,
                data_unchanged=data_unchanged,
                data_revision=data_revision,
            ),
            "data_revision": data_revision,
            "seed": SEED,
            "sample_counts": sample_counts,
            "holdout_policy": (
                "Holdout rows are separately authored, normalized-overlap checked, forbidden from cross-split gold "
                "references, and evaluated in an isolated service namespace; they must not be used for rule tuning."
            ),
            "external_reference_policy": "Only schema ideas and public benchmark styles are referenced; no private competitor data is copied.",
            "safety_policy": dict(computed_safety_policy),
            "file_hashes_sha256": file_hashes,
            "self_check": dict(computed_self_check),
        }
    )
    return manifest


def validate_manifest() -> tuple[dict[str, list[dict]], dict, str]:
    manifest_path = BENCH / "manifest.json"
    if not manifest_path.is_file():
        raise EvidenceBenchValidationError("missing manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvidenceBenchValidationError("manifest.json contains invalid JSON: %s" % exc.msg) from exc
    if not isinstance(manifest, dict):
        raise EvidenceBenchValidationError("manifest.json must contain an object")
    missing_keys = sorted(_MANIFEST_REQUIRED_KEYS - set(manifest))
    if missing_keys:
        raise EvidenceBenchValidationError("manifest.json missing required keys: %s" % missing_keys)
    rows_by_file = _load_rows()
    self_check, safety_policy = _validate_dataset(rows_by_file)
    sample_counts = {rel: len(rows_by_file[rel]) for rel in FILES}
    file_hashes = {rel: hashlib.sha256((BENCH / rel).read_bytes()).hexdigest() for rel in FILES}
    expected_revision = _data_revision(sample_counts, file_hashes)
    comparisons = {
        "benchmark_name": "MemGuardOS EvidenceBench",
        "version": "v2",
        "seed": SEED,
        "sample_counts": sample_counts,
        "file_hashes_sha256": file_hashes,
        "self_check": self_check,
        "safety_policy": safety_policy,
        "data_revision": expected_revision,
    }
    for key, expected in comparisons.items():
        if manifest.get(key) != expected:
            raise EvidenceBenchValidationError("manifest %s does not match recomputed dataset state" % key)
    if set(manifest["sample_counts"]) != set(FILES) or set(manifest["file_hashes_sha256"]) != set(FILES):
        raise EvidenceBenchValidationError("manifest file collection must equal canonical FILES")
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    return rows_by_file, manifest, manifest_sha256


def _write_manifest(
    sample_counts: dict[str, int],
    existing: dict | None = None,
    *,
    self_check: dict[str, bool] | None = None,
    safety_policy: dict[str, bool] | None = None,
    data_unchanged: bool = False,
) -> None:
    manifest = _manifest_payload(
        sample_counts,
        existing,
        self_check=self_check,
        safety_policy=safety_policy,
        data_unchanged=data_unchanged,
    )
    with (BENCH / "manifest.json").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def refresh_existing_dataset() -> int:
    """Canonicalize checked-in samples without discarding optimized gold metadata."""
    manifest_path = BENCH / "manifest.json"
    try:
        existing = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    except json.JSONDecodeError as exc:
        raise EvidenceBenchValidationError("manifest.json contains invalid JSON: %s" % exc.msg) from exc

    rows_by_file = _load_rows()
    self_check, safety_policy = _validate_dataset(rows_by_file)
    sample_counts = {rel: len(rows) for rel, rows in rows_by_file.items()}
    current_hashes = {rel: hashlib.sha256((BENCH / rel).read_bytes()).hexdigest() for rel in FILES}
    data_unchanged = bool(
        existing
        and existing.get("sample_counts") == sample_counts
        and existing.get("file_hashes_sha256") == current_hashes
    )
    for rel, rows in rows_by_file.items():
        write_jsonl(rel, rows)

    _write_manifest(
        sample_counts,
        existing,
        self_check=self_check,
        safety_policy=safety_policy,
        data_unchanged=data_unchanged,
    )
    print("refreshed %s with %s samples" % (BENCH, sum(sample_counts.values())))
    return 0


def rebuild_baseline_dataset() -> int:
    rows_by_file: dict[str, list[dict]] = {}
    for rel in FILES:
        count = 20 if rel.startswith("holdout/") else 10
        rows = [row_for(rel, idx, holdout=rel.startswith("holdout/")) for idx in range(count)]
        rows_by_file[rel] = rows
    self_check, safety_policy = _validate_dataset(rows_by_file)
    sample_counts = {rel: len(rows) for rel, rows in rows_by_file.items()}
    for rel, rows in rows_by_file.items():
        write_jsonl(rel, rows)

    readme = [
        "# MemGuardOS EvidenceBench v2",
        "",
        "EvidenceBench v2 是 MemGuardOS 的抗质疑评测集，用于补充原有功能正确性 benchmark。",
        "它覆盖 hard negative、holdout、跨语言、冲突、自然语言精准遗忘、敏感治理和 openKylin OS Agent 场景。",
        "",
        "所有敏感信息均为合成样例，不包含真实 API Key、真实个人信息或私有竞品数据。",
        "holdout 目录至少占全量样例 20%，用于说明规则调优与最终复验之间的隔离口径。",
        "",
        "运行方式：`python scripts/run_evidencebench_v2.py --output output/eval_v2_result.json --markdown output/eval_v2_report.md`。",
        "",
    ]
    with (BENCH / "README.md").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(readme))

    _write_manifest(sample_counts, self_check=self_check, safety_policy=safety_policy)
    print("wrote %s with %s samples" % (BENCH, sum(sample_counts.values())))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh or explicitly rebuild MemGuardOS EvidenceBench v2")
    parser.add_argument(
        "--rebuild-baseline",
        action="store_true",
        help="Replace optimized checked-in cases with the deterministic baseline generator output.",
    )
    args = parser.parse_args(argv)
    try:
        if args.rebuild_baseline:
            return rebuild_baseline_dataset()
        return refresh_existing_dataset()
    except EvidenceBenchValidationError as exc:
        print("EvidenceBench validation failed: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
