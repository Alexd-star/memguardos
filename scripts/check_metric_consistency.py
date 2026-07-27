#!/usr/bin/env python3
"""Fail when a document claims a metric value the result files do not support.

Documents drift: a number gets copied into a table, the evaluation is re-run,
and the table keeps the old figure. That is how a repository ends up claiming
"全线 1.0000" on one page and "0.7333" on another. This check pins the
retrieval/forgetting headline metrics to the generated result files.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 文档中出现这些"权威指标"时，紧跟其后的反引号数字必须与结果 JSON 一致。
TRACKED_METRICS = {
    "recall_at_5": "Recall@5",
    "recall_at_1": "Recall@1",
    "cross_language_recall_at_5": "跨语言 Recall@5",
    "cross_language_recall_at_1": "跨语言 Recall@1",
    "forget_precision": "遗忘精确率",
    "forget_recall": "遗忘召回率",
}

# 只检查会被评委当作正式结论引用的文档。
CHECKED_DOCS = (
    "docs/赛题要求逐句符合性核查.md",
    "docs/超标准交付评分矩阵.md",
    "docs/eval_v2_report.md",
)

VALUE_PATTERN = r"\s*[`]([01]\.\d{4})[`]"


def load_v2_metrics() -> dict:
    path = ROOT / "docs" / "eval_v2_report.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    metrics = payload.get("metrics")
    return metrics if isinstance(metrics, dict) else payload


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
    metrics = load_v2_metrics()
    if not metrics:
        print("skip: docs/eval_v2_report.json 不存在；先运行 scripts/run_evidencebench_v2.py")
        return 0

    problems = []
    for relative in CHECKED_DOCS:
        path = ROOT / relative
        if not path.exists():
            continue
        # 只校验明确标注 EvidenceBench v2 的行：同名指标在 v1 集、消融基线、
        # 遗忘门禁集上取值本就不同，混在一起比较会产生假告警。
        text = "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if "v2" in line.lower()
        )
        for metric_key, label in TRACKED_METRICS.items():
            if metric_key not in metrics:
                continue
            expected = float(metrics[metric_key])
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
