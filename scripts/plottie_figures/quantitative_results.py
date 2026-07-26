"""Quantitative publication figures from verified MemGuardOS JSON evidence."""

from __future__ import annotations

from collections import Counter

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from .evidence import Evidence
from .style import (
    ACCENT,
    BLUE,
    BLUE_SECONDARY,
    GRAY,
    GRAY_DARK,
    GRAY_LIGHT,
    GREEN_2,
    GREEN_3,
    INK,
    RED_1,
    RED_2,
    RED_STRONG,
    TALL,
    WHITE,
    WIDE,
    FigureRegistry,
    draw_dot_matrix,
    source_note,
    suptitle,
    wrap_label,
    write_csv,
)


BAR_EDGE = "black"
BAR_LW = 1.5


def _is_dark(color: str, threshold: float = 0.52) -> bool:
    value = color.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.299 * r + 0.587 * g + 0.114 * b < threshold


def _annotate_bars(ax, bars, *, fmt: str = "{:.2f}", dy: float = 0.012, fontsize: int = 13) -> None:
    for bar in bars:
        value = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + dy,
            fmt.format(value),
            ha="center",
            va="bottom",
            fontsize=fontsize,
            color=INK,
        )


def _annotate_barh(ax, values: list[float], *, fontsize: int = 13, fmt: str = "{:.0f}") -> None:
    max_v = max(values + [1])
    for i, value in enumerate(values):
        ax.text(value + max_v * 0.025, i, fmt.format(value), va="center", fontsize=fontsize, color=INK)


def _annotate_inside_or_above(ax, bars, colors: list[str], *, fmt: str = "{:.2f}", fontsize: int = 13) -> None:
    ymax = max((bar.get_height() for bar in bars), default=1)
    for bar, color in zip(bars, colors):
        value = bar.get_height()
        if value > ymax * 0.18:
            y = value - ymax * 0.08
            va = "top"
            text_color = WHITE if _is_dark(color) else INK
        else:
            y = value + ymax * 0.035
            va = "bottom"
            text_color = INK
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            y,
            fmt.format(value),
            ha="center",
            va=va,
            fontsize=fontsize,
            color=text_color,
        )


def _legend_axis(ax, handles: list, *, title: str, note: str = "") -> None:
    ax.set_axis_off()
    ax.legend(handles=handles, loc="center left", frameon=False, fontsize=13, title=title, title_fontsize=15)
    if note:
        ax.text(0.0, 0.05, wrap_label(note, 14), transform=ax.transAxes, fontsize=10.5, color=GRAY_DARK, ha="left", va="bottom")


def draw_f10(ev: Evidence, reg: FigureRegistry) -> None:
    reg.contract("F10", "最终交付门禁在当前证据中为 136 项通过、0 失败、0 警告。", "quantitative grid", "ready summary + 真实类别聚合。", "不硬编码测试数字，不展示本项目不存在的外部数据库加速、公开正负例集合或排行榜指标。")
    fig = plt.figure(figsize=TALL)
    gs = fig.add_gridspec(1, 3, width_ratios=[0.9, 1.45, 0.9], wspace=0.42)
    axes = [fig.add_subplot(gs[0, i]) for i in range(3)]
    suptitle(fig, "F10 竞赛就绪门禁")
    fig.subplots_adjust(left=0.08, right=0.985, top=0.82, bottom=0.18)
    summary = ev.ready_summary
    cats = ["PASS", "FAIL", "WARN"]
    vals = [summary.get("passed", 0), summary.get("failed", 0), summary.get("warnings", 0)]
    colors = [BLUE, RED_2, GRAY_LIGHT]
    bars = axes[0].bar(np.arange(len(cats)), vals, color=colors, edgecolor=BAR_EDGE, linewidth=BAR_LW)
    _annotate_inside_or_above(axes[0], bars, colors, fmt="{:.0f}", fontsize=14)
    axes[0].set_ylabel("checks")
    axes[0].set_xticks(np.arange(len(cats)), cats)
    axes[0].set_ylim(0, max(vals + [1]) * 1.18)
    axes[0].set_title("a  blocking gate summary", loc="left", fontsize=17, fontweight="bold", pad=12)
    axes[0].tick_params(axis="x", length=0)
    category_counts = Counter(str(item.get("category", "其他")) for item in ev.ready.get("checks", []) if item.get("ok"))
    top_items = sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))[:8] or [("ready checks", vals[0])]
    names, counts = zip(*top_items)
    y = np.arange(len(names))
    axes[1].barh(y, counts, color=BLUE_SECONDARY, edgecolor=BAR_EDGE, linewidth=BAR_LW)
    axes[1].set_yticks(y, [wrap_label(n, 10) for n in names])
    axes[1].invert_yaxis()
    axes[1].set_xlabel("passed checks")
    axes[1].set_title("b  passed checks by category", loc="left", fontsize=17, fontweight="bold", pad=12)
    for i, v in enumerate(counts):
        axes[1].text(v + max(counts) * 0.035, i, str(v), va="center", fontsize=13, color=INK)
    axes[1].set_xlim(0, max(counts) * 1.22)
    handles = [
        Patch(facecolor=BLUE, edgecolor=BAR_EDGE, label="通过项"),
        Patch(facecolor=RED_2, edgecolor=BAR_EDGE, label="失败项"),
        Patch(facecolor=GRAY_LIGHT, edgecolor=BAR_EDGE, label="警告项"),
    ]
    _legend_axis(axes[2], handles, title="status encoding", note="非公开榜单指标。")
    csv = write_csv("F10_competition_ready", ["metric", "value"], [["passed", vals[0]], ["failed", vals[1]], ["warnings", vals[2]], *top_items])
    source_note(fig, f"Source: docs/competition_ready.json; source data: {csv}")
    reg.save(fig, "02_quantitative_results", "F10_测试门禁结果", {"图号": "F10", "图名": "测试门禁结果", "类型": "figures4papers_wide_bar", "数据来源": "competition_ready.json", "source_data": csv})


def draw_f11(ev: Evidence, reg: FigureRegistry) -> None:
    reg.contract("F11", "benchmark 覆盖检索、敏感治理、遗忘、冲突、图谱、质量和程序性记忆等核心任务。", "quantitative grid", "横向条形图展示 eval_result.json 中的样例/种子构成。", "只展示自建 deterministic benchmark，不暗示公开排行榜。")
    labels = {
        "seed_memories": "seed memories",
        "retrieval_cases": "retrieval",
        "sensitive_cases": "sensitive",
        "forgetting_cases": "forgetting",
        "conflict_cases": "conflict",
        "graph_cases": "graph",
        "quality_cases": "quality",
        "procedural_cases": "procedural",
    }
    items = sorted([(labels.get(k, k), int(v)) for k, v in ev.eval_result.get("dataset", {}).items()], key=lambda item: item[1])
    fig, ax = plt.subplots(figsize=WIDE)
    fig.subplots_adjust(left=0.22, right=0.985, top=0.78, bottom=0.18)
    names, vals = zip(*items)
    bar_colors = [GRAY_LIGHT] * len(vals)
    if vals:
        bar_colors[-1] = BLUE
    ax.barh(names, vals, color=bar_colors, edgecolor=BAR_EDGE, linewidth=BAR_LW)
    ax.set_title(f"F11 Benchmark composition (n={sum(vals)})", loc="left", fontsize=18, fontweight="bold", pad=12)
    ax.set_xlabel("cases / seed memories")
    for i, v in enumerate(vals):
        ax.text(v + max(vals) * 0.025, i, str(v), va="center", fontsize=13)
    ax.set_xlim(0, max(vals) * 1.15)
    csv = write_csv("F11_benchmark_composition", ["case_type", "count"], list(items))
    source_note(fig, f"Source: docs/eval_result.json; source data: {csv}")
    reg.save(fig, "02_quantitative_results", "F11_benchmark_composition", {"图号": "F11", "图名": "Benchmark composition", "类型": "horizontal_bar", "数据来源": "eval_result.json", "source_data": csv})


def draw_f12(ev: Evidence, reg: FigureRegistry) -> None:
    reg.contract("F12", "核心质量指标完整通过，同时 baseline 对比显示混合检索优于纯文本检索。", "quantitative grid", "任务指标 dot plot + retrieval ablation。", "1.0 仅代表本项目基准集结果。")
    e = ev.eval_result
    metrics = [
        ("Recall@5", e.get("retrieval", {}).get("mean_recall_at_5")),
        ("x-lang Recall@5", e.get("retrieval", {}).get("cross_language_recall_at_5")),
        ("block F1", e.get("sensitive_detection", {}).get("block_f1")),
        ("redaction F1", e.get("sensitive_detection", {}).get("redaction_f1")),
        ("label F1", e.get("sensitive_detection", {}).get("label_f1")),
        ("forget precision", e.get("forgetting", {}).get("candidate_precision")),
        ("forget recall", e.get("forgetting", {}).get("candidate_recall")),
        ("conflict acc.", e.get("conflict_resolution", {}).get("correctness")),
        ("graph Recall@5", e.get("graph_memory", {}).get("entity_recall_at_5")),
    ]
    metrics = [(k, float(v)) for k, v in metrics if v is not None]
    proc = e.get("procedural_memory", {})
    proc_metrics = [
        ("Recall@5", proc.get("recall_at_5")),
        ("Subtype hit", proc.get("procedural_subtype_hit_rate")),
        ("Graph layer", 1.0 if proc.get("hierarchical_graph_layers_ok") else 0.0),
        ("Context expl.", 1.0 if proc.get("context_explainability_ok") else 0.0),
    ]
    proc_metrics = [(k, float(v)) for k, v in proc_metrics if v is not None]
    fig = plt.figure(figsize=TALL)
    gs = fig.add_gridspec(1, 4, width_ratios=[1.25, 0.95, 0.9, 0.72], wspace=0.42)
    axes = [fig.add_subplot(gs[0, i]) for i in range(4)]
    suptitle(fig, "F12 核心质量指标")
    fig.subplots_adjust(left=0.13, right=0.985, top=0.82, bottom=0.20)
    names, vals = zip(*metrics)
    y = np.arange(len(names))
    axes[0].barh(y, vals, color=BLUE, edgecolor=BAR_EDGE, linewidth=BAR_LW)
    axes[0].axvline(1.0, color=ACCENT, lw=1.6, ls="--")
    axes[0].set_yticks(y, names)
    axes[0].invert_yaxis()
    axes[0].set_xlim(0, 1.06)
    axes[0].set_xlabel("score")
    axes[0].set_title("a  task metrics", loc="left", fontsize=17, fontweight="bold", pad=12)
    for i, value in enumerate(vals):
        axes[0].text(value - 0.025, i, f"{value:.2f}", ha="right", va="center", fontsize=12.5, color=WHITE)
    baseline = e.get("baseline_comparison", {})
    b_names = ["text only", "hybrid no graph", "full"]
    b_vals = [float(baseline.get("text_only_recall_at_5", 0)), float(baseline.get("hybrid_no_graph_recall_at_5", 0)), float(baseline.get("full_memguardos_recall_at_5", 0))]
    x2 = np.arange(len(b_names))
    b_colors = [RED_1, GREEN_2, BLUE]
    bars = axes[1].bar(x2, b_vals, color=b_colors, edgecolor=BAR_EDGE, linewidth=BAR_LW)
    _annotate_inside_or_above(axes[1], bars, b_colors, fmt="{:.3f}", fontsize=12)
    axes[1].set_xticks([])
    axes[1].set_ylim(0.75, 1.035)
    axes[1].set_xlabel("Recall@5")
    axes[1].set_title("b  retrieval ablation", loc="left", fontsize=17, fontweight="bold", pad=12)
    p_names, p_vals = zip(*proc_metrics)
    y3 = np.arange(len(p_names))
    axes[2].barh(y3, p_vals, color=BLUE_SECONDARY, edgecolor=BAR_EDGE, linewidth=BAR_LW)
    axes[2].set_yticks(y3, [wrap_label(n, 12) for n in p_names])
    axes[2].invert_yaxis()
    axes[2].set_xlim(0, 1.06)
    axes[2].set_xlabel("score")
    axes[2].set_title("c  procedural memory", loc="left", fontsize=17, fontweight="bold", pad=12)
    for i, value in enumerate(p_vals):
        axes[2].text(value - 0.025, i, f"{value:.2f}", ha="right", va="center", fontsize=12.5, color=WHITE)
    handles = [
        Patch(facecolor=BLUE, edgecolor=BAR_EDGE, label="完整链路"),
        Patch(facecolor=GREEN_2, edgecolor=BAR_EDGE, label="混合检索"),
        Patch(facecolor=RED_1, edgecolor=BAR_EDGE, label="文本基线"),
        Line2D([0], [0], color=ACCENT, linestyle="--", linewidth=1.6, label="满分阈值"),
    ]
    _legend_axis(axes[3], handles, title="encoding", note="非公开榜单指标。")
    csv = write_csv("F12_core_quality_metrics", ["metric", "score"], metrics + [(name, value) for name, value in zip(b_names, b_vals)] + proc_metrics)
    source_note(fig, f"Source: docs/eval_result.json; deterministic benchmark, not external leaderboard; source data: {csv}")
    reg.save(fig, "02_quantitative_results", "F12_core_quality_bars", {"图号": "F12", "图名": "核心质量指标", "类型": "figures4papers_metric_bars", "数据来源": "eval_result.json", "source_data": csv})


def draw_f13(ev: Evidence, reg: FigureRegistry) -> None:
    reg.contract("F13", "系统在 openKylin 10k 规模下保持毫秒级 P50/P95，同时记录 P99 尾延迟和 RSS。", "quantitative grid", "P50/P95/P99 分位点 + openKylin 10k 资源条形图。", "真实分位点，不用均值掩盖 P99。")
    local = ev.eval_result.get("latency_load", {})
    ok1 = ev.openkylin_1k.get("latency_load", {})
    ok10 = ev.openkylin_10k.get("latency_load", {})
    fig = plt.figure(figsize=TALL)
    gs = fig.add_gridspec(1, 3, width_ratios=[1.35, 1.0, 0.76], wspace=0.38)
    axes = [fig.add_subplot(gs[0, i]) for i in range(3)]
    suptitle(fig, "F13 Latency and resource profile")
    fig.subplots_adjust(left=0.08, right=0.985, top=0.82, bottom=0.18)
    rows = [("local 1k", local), ("openKylin 1k", ok1), ("openKylin 10k", ok10)]
    x = np.arange(len(rows))
    quantiles = [
        ("P50", "p50_latency_ms", BLUE, "o"),
        ("P95", "p95_latency_ms", GREEN_3, "s"),
        ("P99", "p99_latency_ms", RED_STRONG, "^"),
    ]
    handles = []
    for label, key, color, marker in quantiles:
        values = [float(row.get(key, 0)) for _, row in rows]
        axes[0].plot(x, values, color=color, marker=marker, markersize=8, linewidth=3.2, label=label)
        handles.append(Line2D([0], [0], color=color, marker=marker, linewidth=3.2, markersize=8, label=label))
        for xi, value in zip(x, values):
            if label == "P50":
                if value < 1:
                    y_text, va, dx = value * 1.28, "bottom", -0.035
                else:
                    y_text, va, dx = value * 0.83, "top", -0.035
            elif label == "P95":
                y_text, va, dx = value * 1.22, "bottom", 0.035
            else:
                y_text, va, dx = value * 1.09, "bottom", 0.0
            axes[0].text(xi + dx, y_text, f"{value:.2f}", ha="center", va=va, fontsize=11.5, color=color)
    axes[0].set_yscale("log")
    axes[0].set_ylim(0.12, max(float(row.get("p99_latency_ms", 0)) for _, row in rows) * 1.75)
    axes[0].set_xticks(x, [label for label, _ in rows])
    axes[0].set_ylabel("latency (ms, log scale)")
    axes[0].set_title("a  latency quantiles", loc="left", fontsize=17, fontweight="bold", pad=12)
    axes[0].axhline(100, color=GRAY, linestyle="--", linewidth=1.8, alpha=0.75)
    axes[0].text(0.02, 100 * 1.08, "100 ms reference", fontsize=11.5, color=GRAY_DARK)
    res = ev.openkylin_10k.get("resource_usage", {})
    labels = ["segment", "sqlite", "RSS"]
    vals = [ok10.get("segment_kb", 0) / 1024, ok10.get("sqlite_kb", 0) / 1024, res.get("process_rss_kb", 0) / 1024]
    rcolors = [GREEN_2, BLUE_SECONDARY, BLUE]
    axes[1].barh(labels, vals, color=rcolors, edgecolor=BAR_EDGE, linewidth=BAR_LW)
    axes[1].set_xlabel("MB")
    axes[1].set_title("b  openKylin 10k resource", loc="left", fontsize=17, fontweight="bold", pad=12)
    for i, v in enumerate(vals):
        axes[1].text(v + max(vals) * 0.025, i, f"{v:.1f}", fontsize=13, va="center")
    axes[1].set_xlim(0, max(vals) * 1.2)
    resource_handles = [Patch(facecolor=color, edgecolor=BAR_EDGE, label=label) for label, color in zip(labels, rcolors)]
    _legend_axis(axes[2], [*handles, *resource_handles], title="encoding", note="P99 单独展示。")
    csv = write_csv("F13_latency_resource_profile", ["environment", "p50_ms", "p95_ms", "p99_ms", "segment_mb", "sqlite_mb", "rss_mb"], [("local_1k", local.get("p50_latency_ms", 0), local.get("p95_latency_ms", 0), local.get("p99_latency_ms", 0), local.get("segment_kb", 0) / 1024, local.get("sqlite_kb", 0) / 1024, ""), ("openKylin_1k", ok1.get("p50_latency_ms", 0), ok1.get("p95_latency_ms", 0), ok1.get("p99_latency_ms", 0), ok1.get("segment_kb", 0) / 1024, ok1.get("sqlite_kb", 0) / 1024, ""), ("openKylin_10k", ok10.get("p50_latency_ms", 0), ok10.get("p95_latency_ms", 0), ok10.get("p99_latency_ms", 0), vals[0], vals[1], vals[2])])
    source_note(fig, f"Source: docs/eval_result.json; docs/evidence/latest/eval_result_openkylin_*.json; source data: {csv}")
    reg.save(fig, "02_quantitative_results", "F13_latency_resource_profile", {"图号": "F13", "图名": "Latency and resource profile", "类型": "quantile_resource_plot", "数据来源": "eval_result + openKylin evidence", "source_data": csv})


def draw_f14(ev: Evidence, reg: FigureRegistry) -> None:
    reg.contract("F14", "轻量化存储以 mmap segment 与 SQLite 元数据为主体，10k 规模下占用可被量化追踪。", "quantitative grid", "堆叠条形图 + footprint 指标图。", "只展示实际评测字段。")
    local = ev.eval_result.get("latency_load", {})
    ok10 = ev.openkylin_10k.get("latency_load", {})
    fig = plt.figure(figsize=TALL)
    gs = fig.add_gridspec(1, 3, width_ratios=[1.0, 1.05, 0.78], wspace=0.38)
    axes = [fig.add_subplot(gs[0, i]) for i in range(3)]
    suptitle(fig, "F14 存储占用与轻量化设计")
    fig.subplots_adjust(left=0.08, right=0.985, top=0.82, bottom=0.18)
    labels = ["local 1k", "openKylin 10k"]
    segment = [local.get("segment_kb", 0) / 1024, ok10.get("segment_kb", 0) / 1024]
    sqlite = [local.get("sqlite_kb", 0) / 1024, ok10.get("sqlite_kb", 0) / 1024]
    x = np.arange(len(labels))
    axes[0].bar(x, segment, color=GREEN_2, edgecolor=BAR_EDGE, linewidth=BAR_LW, label="mmap segment")
    axes[0].bar(x, sqlite, bottom=segment, color=BLUE_SECONDARY, edgecolor=BAR_EDGE, linewidth=BAR_LW, label="SQLite metadata", hatch="//")
    axes[0].set_xticks(x, labels)
    axes[0].set_ylabel("MB")
    axes[0].set_title("a  measured footprint", loc="left", fontsize=17, fontweight="bold", pad=12)
    for xi, seg, sql in zip(x, segment, sqlite):
        axes[0].text(xi, seg / 2, f"{seg:.1f}", ha="center", va="center", fontsize=12, color=INK)
        axes[0].text(xi, seg + sql / 2, f"{sql:.1f}", ha="center", va="center", fontsize=12, color=WHITE)
    storage = ev.openkylin_10k.get("storage_footprint", {}) or ev.eval_result.get("storage_footprint", {})
    metric_rows = [("total eval\nstore (MB)", float(storage.get("total_eval_store_kb", 0)) / 1024), ("per live\nmemory (KiB)", float(storage.get("bytes_per_live_memory", 0)) / 1024)]
    names, vals = zip(*metric_rows)
    axes[1].barh(names, vals, color=[GRAY_LIGHT, BLUE], edgecolor=BAR_EDGE, linewidth=BAR_LW)
    axes[1].set_xlabel("value")
    axes[1].set_title("b  footprint indicators", loc="left", fontsize=17, fontweight="bold", pad=12)
    for i, v in enumerate(vals):
        axes[1].text(v + max(vals) * 0.025, i, f"{v:.1f}", fontsize=13, va="center")
    axes[1].set_xlim(0, max(vals) * 1.2)
    handles = [
        Patch(facecolor=GREEN_2, edgecolor=BAR_EDGE, label="mmap segment"),
        Patch(facecolor=BLUE_SECONDARY, edgecolor=BAR_EDGE, hatch="//", label="SQLite metadata"),
        Patch(facecolor=BLUE, edgecolor=BAR_EDGE, label="per-memory footprint"),
    ]
    _legend_axis(axes[2], handles, title="storage layers", note="仅采样评测字段。")
    csv = write_csv("F14_storage_footprint", ["environment", "segment_mb", "sqlite_mb", "total_store_mb", "bytes_per_live_memory_kib"], [("local_1k", segment[0], sqlite[0], local.get("total_store_kb", 0) / 1024, ev.eval_result.get("storage_footprint", {}).get("bytes_per_live_memory", 0) / 1024), ("openKylin_10k", segment[1], sqlite[1], ok10.get("total_store_kb", 0) / 1024, storage.get("bytes_per_live_memory", 0) / 1024)])
    source_note(fig, f"Source: mmap_store.py; sqlite_meta.py; eval_result latency_load/storage_footprint fields; source data: {csv}")
    reg.save(fig, "02_quantitative_results", "F14_storage_footprint_design", {"图号": "F14", "图名": "Storage footprint and design", "类型": "storage_quant_plot", "数据来源": "eval_result + store code", "source_data": csv})


def draw_f15(ev: Evidence, reg: FigureRegistry) -> None:
    reg.contract("F15", "OS 机制覆盖由服务、审计、安全、桌面和安装证据共同支撑。", "quantitative grid", "mechanism × evidence-class 点阵 + 证据文件数量。", "区分 BPF LSM 测试路径和生产默认策略。")
    files = {item.get("path", "") for item in ev.evidence_manifest.get("files", [])}

    def has_any(*needles: str) -> bool:
        return any(any(needle in path for path in files) for needle in needles)

    rows = ["systemd", "cgroup", "journald", "auditd", "BPF LSM", "Peony", "UKUI", ".deb"]
    cols = ["service", "audit", "security", "desktop", "package"]
    mapping = {
        "systemd": [has_any("systemd_cgroup", "systemd"), False, False, False, True],
        "cgroup": [has_any("systemd_cgroup", "namespace_isolation"), False, False, False, False],
        "journald": [has_any("journald"), False, False, False, False],
        "auditd": [False, has_any("auditd", "audit_concurrency"), False, False, False],
        "BPF LSM": [False, False, has_any("bpf_lsm"), False, False],
        "Peony": [False, False, False, has_any("peony"), False],
        "UKUI": [False, False, False, has_any("ukui"), False],
        ".deb": [True, False, False, False, has_any("deb_apt_install", "deb_package")],
    }
    matrix = np.array([[1 if flag else 0 for flag in mapping[row]] for row in rows], dtype=float)
    fig, axes = plt.subplots(1, 2, figsize=TALL, gridspec_kw={"width_ratios": [1.32, 0.9]})
    suptitle(fig, "F15 openKylin OS 机制证据覆盖")
    fig.subplots_adjust(left=0.18, right=0.985, top=0.78, bottom=0.26, wspace=0.36)
    draw_dot_matrix(axes[0], rows, cols, matrix, title="a  mechanism × evidence class", dot_size=36)
    counts = Counter()
    for path in files:
        if any(token in path for token in ["systemd", "cgroup", "journald", "namespace"]):
            counts["service"] += 1
        if "audit" in path:
            counts["audit"] += 1
        if "bpf_lsm" in path:
            counts["security"] += 1
        if any(token in path for token in ["peony", "ukui"]):
            counts["desktop"] += 1
        if "deb_" in path:
            counts["package"] += 1
    names = ["service", "audit", "security", "desktop", "package"]
    vals = [counts.get(name, 0) for name in names]
    y = np.arange(len(names))
    axes[1].barh(y, vals, color=BLUE_SECONDARY, edgecolor=BAR_EDGE, linewidth=BAR_LW)
    axes[1].set_yticks(y, names)
    axes[1].invert_yaxis()
    axes[1].set_xlabel("evidence files")
    axes[1].set_title("b  evidence file counts", loc="left", fontsize=17, fontweight="bold", pad=12)
    for i, v in enumerate(vals):
        axes[1].text(v + max(vals + [1]) * 0.035, i, str(v), fontsize=13, va="center")
    axes[1].set_xlim(0, max(vals + [1]) * 1.18)
    csv = write_csv("F15_os_mechanism_coverage", ["mechanism", *cols], [[row, *matrix[i].tolist()] for i, row in enumerate(rows)])
    source_note(fig, f"Source: docs/evidence/latest/manifest.json; docs/操作系统机制说明.md; source data: {csv}")
    reg.save(fig, "02_quantitative_results", "F15_OS_mechanism_coverage", {"图号": "F15", "图名": "OS mechanism coverage", "类型": "evidence_dot_matrix", "数据来源": "openKylin evidence", "source_data": csv})


DRAWERS = [draw_f10, draw_f11, draw_f12, draw_f13, draw_f14, draw_f15]
