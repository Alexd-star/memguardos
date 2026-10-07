#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import argparse
import json
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memguard.eval.agent_interactions import evaluate_agent_interaction_cases  # noqa: E402


def load_cases(path):
    cases = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError("line %s must be an object" % line_number)
        cases.append(item)
    return cases


def render_markdown(result):
    extraction = result["metrics"]["extraction"]
    lines = [
        "# 真实 Agent 交互 Benchmark 报告",
        "",
        "本报告由固定 JSONL 数据集和实际 MemGuardOS 代码自动生成。指标同时给出失败样例，不能替代真实 openKylin 演示证据。",
        "",
        "## 数据组成",
        "",
        "- 总样例数：%s" % result["case_count"],
        "- 来源分布：`%s`" % json.dumps(result["source_distribution"], ensure_ascii=False),
        "- 类别分布：`%s`" % json.dumps(result["category_distribution"], ensure_ascii=False),
        "",
        "## 核心指标",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        "| 提取 Precision | %.4f |" % extraction["precision"],
        "| 提取 Recall | %.4f |" % extraction["recall"],
        "| 提取 F1 | %.4f |" % extraction["f1"],
        "| 角色污染率 | %.4f |" % result["metrics"]["role_pollution_rate"],
        "| 敏感泄漏率 | %.4f |" % result["metrics"]["sensitive_leak_rate"],
        "| 精准遗忘成功率 | %.4f |" % result["metrics"]["forget_success_rate"],
        "| 跨会话遵守率 | %.4f |" % result["metrics"]["cross_session_adherence"],
        "| 单样例端到端 P95 | %.3f ms |" % result["latency_ms"]["p95"],
        "",
        "## 失败样例与误差归因",
        "",
    ]
    if not result["failures"]:
        lines.append("本次固定数据集未出现失败样例。该结论仅适用于当前数据集，仍需结合真实 Agent 会话和扩展难例持续验证。")
    else:
        for failure in result["failures"]:
            lines.append(
                "- `%s`（%s）：%s"
                % (
                    failure["case_id"],
                    failure["category"],
                    "；".join(failure["reasons"]),
                )
            )
    lines.extend(
        [
            "",
            "## 口径边界",
            "",
            "“real_anonymized”表示来自项目实际开发与演示模式的脱敏重构样例，不代表公开第三方基准。"
            "“synthetic”表示为覆盖边界条件而人工构造的可复现样例。所有样例均含双标注字段；"
            "当前版本的双标注一致性为数据文件中的记录值，正式提交前仍建议由第二名真实标注者独立复核。",
            "",
        ]
    )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        default=str(ROOT / "data" / "benchmarks" / "agent_interactions.jsonl"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "outputs" / "agent_interaction_benchmark"),
    )
    args = parser.parse_args()
    dataset = Path(args.dataset).resolve()
    output_dir = Path(args.output_dir).resolve()
    work_dir = output_dir / "work"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result = evaluate_agent_interaction_cases(load_cases(dataset), work_dir)
    (output_dir / "metrics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(render_markdown(result), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
