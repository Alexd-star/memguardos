#!/usr/bin/env python3
"""Check public headline metrics against the curated openKylin result JSON."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 文档中出现这些"权威指标"时，紧跟其后的反引号数字必须与结果 JSON 一致。
TRACKED_METRICS = {
    ("retrieval", "mean_recall_at_5"): "Recall@5",
    ("retrieval", "cross_language_recall_at_5"): "跨语言 Recall@5",
    ("forgetting", "candidate_precision"): "遗忘候选精确率",
    ("forgetting", "candidate_recall"): "遗忘候选召回率",
}

# 只检查会被评委当作正式结论引用的文档。
CHECKED_DOCS = (
    "README.md",
    "docs/testing.md",
    "docs/competition.md",
)

VALUE_PATTERN = r"\s*[`]([01]\.\d{4})[`]"


def load_metrics() -> dict:
    path = ROOT / "docs/evidence/performance_latest/eval_result_openkylin_10k_optimized.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload


def claimed_values(text: str, label: str) -> list:
    """Values written immediately after the label.

    Binding each label to the number that directly follows it keeps neighbouring
    metrics on the same table row from being attributed to the wrong one.
    """

    # 前缀否定用于区分 "Recall@5" 与 "跨语言 Recall@5"。
    prefix = "" if label.startswith("跨语言") else "(?<!跨语言 )"
    pattern = prefix + re.escape(label) + VALUE_PATTERN
    return re.findall(pattern, text)


def main() -> int:
    metrics = load_metrics()
    if not metrics:
        print("缺少精选 openKylin 评测结果")
        return 1

    problems = []
    for relative in CHECKED_DOCS:
        path = ROOT / relative
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for metric_path, label in TRACKED_METRICS.items():
            section, metric_key = metric_path
            section_payload = metrics.get(section, {})
            if metric_key not in section_payload:
                continue
            expected = float(section_payload[metric_key])
            for raw in claimed_values(text, label):
                if abs(float(raw) - expected) > 1e-6:
                    problems.append(
                        "%s: %s 声称 %s，结果文件为 %.4f" % (relative, label, raw, expected)
                    )

    unique = sorted(set(problems))
    if unique:
        print("发现 %d 处指标不一致：" % len(unique))
        for item in unique:
            print("  - " + item)
        return 1
    print("0 处不一致：文档中的权威指标与结果文件一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
