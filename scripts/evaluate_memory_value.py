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

from memguard.eval.memory_value import evaluate_memory_value_cases  # noqa: E402


def load_cases(path: Path):
    cases = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError("line %s must be an object" % line_number)
        cases.append(item)
    return cases


def render_report(result):
    aggregate = result["aggregate"]
    groups = aggregate["groups"]
    deltas = aggregate["mean_deltas"]
    lines = [
        "# MemGuardOS 智能体记忆价值确定性评测报告",
        "",
        "本报告由固定 JSONL 数据集和实际 MemGuardOS 服务代码自动生成。"
        "确定性层用于验证记忆抽取、作用域、选择、遗忘和指标计算，"
        "不能替代 openKylin 上的真实 OpenHands 行为证据。",
        "",
        "## 数据与系统验证",
        "",
        "- 配对案例数：%s" % result["case_count"],
        "- 系统验证通过：%s" % result["system_validation"]["passed"],
        "- 系统验证失败：%s" % result["system_validation"]["failed"],
        "",
        "## 三组结果",
        "",
        "| 指标 | 无记忆组 | 完整历史组 | MemGuardOS 组 |",
        "|---|---:|---:|---:|",
        "| 任务完成率 | %.4f | %.4f | %.4f |"
        % tuple(groups[name]["task_completion_rate"] for name in groups),
        "| 首选工具准确率 | %.4f | %.4f | %.4f |"
        % tuple(groups[name]["first_tool_accuracy"] for name in groups),
        "| 平均交互轮数 | %.3f | %.3f | %.3f |"
        % tuple(groups[name]["mean_interaction_turns"] for name in groups),
        "| 平均 Prompt token | %.3f | %.3f | %.3f |"
        % tuple(groups[name]["mean_prompt_tokens"] for name in groups),
        "| 平均错误命令数 | %.3f | %.3f | %.3f |"
        % tuple(groups[name]["mean_failed_commands"] for name in groups),
        "| 平均重复命令数 | %.3f | %.3f | %.3f |"
        % tuple(groups[name]["mean_repeated_commands"] for name in groups),
        "",
        "## 记忆价值变化",
        "",
        "| 指标 | 平均变化 | 对照基线 |",
        "|---|---:|---|",
        "| 任务完成率变化 | %.4f | 无记忆组 |"
        % (deltas["completion_vs_no_memory"] or 0.0),
        "| 工具选择准确率变化 | %.4f | 无记忆组 |"
        % (deltas["tool_accuracy_vs_no_memory"] or 0.0),
        "| 交互轮数变化 | %.4f | 无记忆组 |"
        % (deltas["interaction_turns_vs_no_memory"] or 0.0),
        "| Prompt token 减少率 | %.4f | 完整历史组 |"
        % (deltas["prompt_token_reduction_ratio"] or 0.0),
        "| 错误命令变化 | %.4f | 无记忆组 |"
        % (deltas["failed_commands_vs_no_memory"] or 0.0),
        "| 重复命令变化 | %.4f | 无记忆组 |"
        % (deltas["repeated_commands_vs_no_memory"] or 0.0),
        "",
        "## 失败与边界",
        "",
    ]
    if result["failures"]:
        for failure in result["failures"]:
            lines.append(
                "- `%s`：%s" % (failure["case_id"], failure["reason"])
            )
    else:
        lines.append("- 当前固定数据集未出现系统验证失败。")
    lines.extend(
        [
            "",
            "Prompt token 使用 MemGuardOS 的统一估算器口径。token 减少率以完整历史回放为基线，"
            "不以天然更短但缺少历史信息的无记忆组为基线。所有聚合结果均可由 `cases.json` "
            "中的逐案例结果重新计算。",
            "",
        ]
    )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="运行 MemGuardOS 智能体记忆价值确定性配对评测"
    )
    parser.add_argument(
        "--dataset",
        default=str(ROOT / "data" / "benchmarks" / "agent_memory_value.jsonl"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "outputs" / "memory_value_deterministic"),
    )
    args = parser.parse_args()
    dataset = Path(args.dataset).resolve()
    output_dir = Path(args.output_dir).resolve()
    work_dir = output_dir / "work"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result = evaluate_memory_value_cases(load_cases(dataset), work_dir)
    cases = result["cases"]
    metrics = {key: value for key, value in result.items() if key != "cases"}
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "cases.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        render_report(result),
        encoding="utf-8",
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0 if not result["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
