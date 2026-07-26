"""Shared style, export, and QA helpers for publication figures.

The visual language is adapted from the local reference repositories
figures4papers, Show2Know, and nature-skills: wide publication canvases,
Helvetica-like typography, thick axes, semantic blue/green/red/neutral colors,
direct labels, editable vector text, real source data, and per-figure contracts.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib as mpl
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch

from .evidence import ROOT


DPI = 300
MM_PER_INCH = 25.4
F4P_WIDE = (14.0, 4.8)
F4P_WIDE_SHORT = (14.0, 3.45)
F4P_TALL = (14.0, 6.2)
F4P_IMAGE_PLATE = (14.0, 7.7)
F4P_SINGLE = (7.0, 4.7)
F4P_SINGLE_TALL = (7.0, 5.6)
WIDE = F4P_WIDE
WIDE_SHORT = F4P_WIDE_SHORT
TALL = F4P_TALL
IMAGE_PLATE = F4P_IMAGE_PLATE
SINGLE = F4P_SINGLE
SINGLE_TALL = F4P_SINGLE_TALL

OUT_ROOT = ROOT / "outputs" / "可视化证据包_figures4papers_style"
SOURCE_ROOT = OUT_ROOT / "source_data"

INK = "#111111"
GRAY_DARK = "#4D4D4D"
GRAY = "#767676"
GRAY_LIGHT = "#CFCECE"
GRAY_FILL = "#F5F5F5"
BLUE = "#0F4D92"
BLUE_SECONDARY = "#3775BA"
GREEN_1 = "#DDF3DE"
GREEN_2 = "#AADCA9"
GREEN_3 = "#8BCF8B"
RED_1 = "#F6CFCB"
RED_2 = "#E9A6A1"
RED_STRONG = "#B64342"
TEAL = "#42949E"
VIOLET = "#9A4D8E"
HIGHLIGHT = "#FFD700"
ACCENT = RED_STRONG
WHITE = "#FFFFFF"
DEFAULT_COLORS = [BLUE, GREEN_3, RED_STRONG, TEAL, VIOLET, GRAY_LIGHT]


def _font_stack() -> list[str]:
    preferred = [
        "DengXian",
        "SimHei",
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "WenQuanYi Micro Hei",
        "Microsoft YaHei",
        "SimSun",
        "Arial Unicode MS",
        "Helvetica",
        "Arial",
        "DejaVu Sans",
        "sans-serif",
    ]
    available = {f.name for f in fm.fontManager.ttflist}
    return [name for name in preferred if name == "sans-serif" or name in available] or ["DejaVu Sans", "sans-serif"]


def apply_publication_style() -> None:
    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": _font_stack(),
            "axes.unicode_minus": False,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "font.size": 16,
            "axes.titlesize": 18,
            "axes.labelsize": 18,
            "xtick.labelsize": 14,
            "ytick.labelsize": 14,
            "axes.linewidth": 2.2,
            "axes.spines.right": False,
            "axes.spines.top": False,
            "legend.frameon": False,
            "legend.fontsize": 13,
            "lines.linewidth": 2.5,
            "lines.markersize": 7.0,
            "xtick.major.width": 1.8,
            "ytick.major.width": 1.8,
            "xtick.major.size": 7,
            "ytick.major.size": 7,
            "figure.facecolor": WHITE,
            "savefig.facecolor": WHITE,
            "savefig.dpi": DPI,
        }
    )


def wrap_label(text: str, width: int = 12) -> str:
    cjk = re.compile(r"[\u4e00-\u9fff]")
    lines: list[str] = []
    for para in str(text).split("\n"):
        if not para:
            lines.append("")
            continue
        if " " in para:
            current = ""
            for token in para.split(" "):
                candidate = token if not current else f"{current} {token}"
                if len(candidate) <= width or not current:
                    current = candidate
                else:
                    lines.append(current)
                    current = token
            if current:
                lines.append(current)
            continue
        if not cjk.search(para):
            lines.append(para)
            continue
        tokens = re.findall(r"[A-Za-z0-9_./:+-]+|[\u4e00-\u9fff]|.", para)
        current = ""
        for token in tokens:
            candidate = current + token
            if len(candidate) <= width or not current:
                current = candidate
            else:
                lines.append(current)
                current = token
        if current:
            lines.append(current)
    return "\n".join(lines)


def new_canvas(size: tuple[float, float] = WIDE):
    fig, ax = plt.subplots(figsize=size)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    return fig, ax


def suptitle(fig, text: str) -> None:
    fig.suptitle(text, x=0.025, y=0.965, ha="left", fontsize=20, fontweight="bold")


def source_note(fig, text: str) -> None:
    fig.text(0.018, 0.020, text, fontsize=9.2, color=GRAY_DARK, ha="left", va="bottom")


def panel_heading(ax, label: str, title: str, *, y: float = 1.02, x: float = 0.0) -> None:
    ax.text(x, y, label, transform=ax.transAxes, fontsize=18, fontweight="bold", ha="left", va="bottom", color=INK)
    ax.text(x + 0.060, y, title, transform=ax.transAxes, fontsize=16, fontweight="bold", ha="left", va="bottom", color=INK)


def arrow(ax, start: tuple[float, float], end: tuple[float, float], *, color: str = GRAY_DARK, lw: float = 2.0) -> None:
    ax.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="-|>",
            mutation_scale=16,
            linewidth=lw,
            color=color,
            shrinkA=1.5,
            shrinkB=1.5,
        )
    )


def text_node(ax, x: float, y: float, head: str, body: str = "", *, color: str = INK) -> None:
    ax.plot(x, y, "o", ms=10, mfc=WHITE, mec=color, mew=2.2, zorder=3)
    ax.text(x, y + 0.072, head, fontsize=15, fontweight="bold", ha="center", va="bottom", color=INK)
    if body:
        ax.text(x, y - 0.072, wrap_label(body, 13), fontsize=12, ha="center", va="top", color=GRAY_DARK, linespacing=1.12)


def stage_line(ax, xs: Iterable[float], y: float, labels: list[str], *, active: int | None = None) -> None:
    xs = list(xs)
    for i, x in enumerate(xs):
        color = BLUE if active is None or i <= active else GRAY
        ax.plot(x, y, "o", ms=10, mfc=WHITE, mec=color, mew=2.2)
        ax.text(x, y - 0.075, wrap_label(labels[i], 8), ha="center", va="top", fontsize=12.5, color=INK)
        if i < len(xs) - 1:
            ax.plot([x + 0.018, xs[i + 1] - 0.018], [y, y], color=color, lw=2.2)


def draw_lollipop(ax, labels: list[str], values: list[float], *, xlim: tuple[float, float], xlabel: str, title: str) -> None:
    y = np.arange(len(labels))
    ax.hlines(y, xlim[0], values, color=GRAY, lw=2.2)
    ax.plot(values, y, "o", ms=8.0, color=BLUE)
    ax.set_yticks(y, labels)
    ax.set_xlim(*xlim)
    ax.set_xlabel(xlabel)
    ax.set_title(title, loc="left", fontsize=17, fontweight="bold", pad=12)
    ax.grid(axis="x", color=GRAY_LIGHT, linewidth=1.0, alpha=0.65)


def draw_dot_matrix(ax, rows: list[str], cols: list[str], values: np.ndarray, *, title: str, dot_size: float = 36) -> None:
    ax.set_title(title, loc="left", fontsize=17, fontweight="bold", pad=12)
    ax.set_xlim(-0.5, len(cols) - 0.5)
    ax.set_ylim(len(rows) - 0.5, -0.5)
    ax.set_xticks(range(len(cols)), cols, rotation=30, ha="right")
    ax.set_yticks(range(len(rows)), rows)
    ax.tick_params(length=0)
    for x in range(len(cols)):
        ax.axvline(x, color=GRAY_LIGHT, lw=1.0, zorder=0)
    for y in range(len(rows)):
        ax.axhline(y, color=GRAY_LIGHT, lw=1.0, zorder=0)
    for y in range(len(rows)):
        for x in range(len(cols)):
            v = float(values[y, x])
            if v <= 0:
                ax.plot(x, y, "o", ms=5.0, mfc=WHITE, mec=GRAY, mew=1.3, zorder=2)
            else:
                ax.scatter([x], [y], s=dot_size * v, color=BLUE if v >= 1 else GREEN_3, edgecolors=INK, linewidths=1.0, zorder=3)
    for spine in ax.spines.values():
        spine.set_visible(False)


def legend_handles() -> list[Line2D]:
    return [
        Line2D([0], [0], marker="o", color=BLUE, markerfacecolor=BLUE, markeredgecolor=BLUE, markersize=9, label="P50"),
        Line2D([0], [0], marker="s", color=GREEN_3, markerfacecolor=GREEN_3, markeredgecolor=INK, markersize=9, label="P95"),
        Line2D([0], [0], marker="^", color=RED_STRONG, markerfacecolor=RED_STRONG, markeredgecolor=INK, markersize=9, label="P99"),
    ]


def write_csv(stem: str, header: list[str], rows: list[Iterable[Any]]) -> str:
    SOURCE_ROOT.mkdir(parents=True, exist_ok=True)
    path = SOURCE_ROOT / f"{stem}.csv"
    lines = [",".join(header)]
    for row in rows:
        fields = []
        for value in row:
            text = str(value)
            if any(ch in text for ch in [",", "\n", '"']):
                text = '"' + text.replace('"', '""') + '"'
            fields.append(text)
        lines.append(",".join(fields))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path.relative_to(OUT_ROOT).as_posix()


class FigureRegistry:
    def __init__(self) -> None:
        self.rows: list[dict[str, str]] = []
        self.contracts: list[dict[str, str]] = []

    def contract(self, fig_id: str, conclusion: str, archetype: str, evidence_chain: str, review_risk: str) -> None:
        self.contracts.append(
            {
                "图号": fig_id,
                "核心结论": conclusion,
                "图件类型": archetype,
                "证据链": evidence_chain,
                "审稿风险控制": review_risk,
            }
        )

    def save(self, fig, rel_dir: str, stem: str, meta: dict[str, str]) -> None:
        out_dir = OUT_ROOT / rel_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = {
            "svg": out_dir / f"{stem}.svg",
            "pdf": out_dir / f"{stem}.pdf",
            "png": out_dir / f"{stem}.png",
            "tiff": out_dir / f"{stem}.tiff",
        }
        fig.savefig(paths["svg"], format="svg")
        fig.savefig(paths["pdf"], format="pdf")
        fig.savefig(paths["png"], format="png", dpi=DPI)
        fig.savefig(paths["tiff"], format="tiff", dpi=DPI)
        plt.close(fig)
        row = dict(meta)
        row.update({key: value.relative_to(OUT_ROOT).as_posix() for key, value in paths.items()})
        row.update(
            {
                "width_mm": f"{fig.get_figwidth() * MM_PER_INCH:.1f}",
                "height_mm": f"{fig.get_figheight() * MM_PER_INCH:.1f}",
            }
        )
        self.rows.append(row)

    def copy_curated_sets(self) -> None:
        ppt_dir = OUT_ROOT / "04_ppt_selection"
        report_dir = OUT_ROOT / "05_report_selection"
        ppt_dir.mkdir(parents=True, exist_ok=True)
        report_dir.mkdir(parents=True, exist_ok=True)
        by_id = {r["图号"]: r for r in self.rows}
        for idx, fig_id in enumerate(["F01", "F03", "F06", "F10", "F13", "F15", "F17", "F20"], 1):
            rec = by_id.get(fig_id)
            if rec:
                for ext in ["svg", "pdf", "png"]:
                    src = OUT_ROOT / rec[ext]
                    shutil.copy2(src, ppt_dir / f"PPT{idx:02d}_{src.name}")
        for idx, fig_id in enumerate([f"F{i:02d}" for i in range(1, 21)], 1):
            rec = by_id.get(fig_id)
            if rec:
                for ext in ["svg", "pdf", "png"]:
                    src = OUT_ROOT / rec[ext]
                    shutil.copy2(src, report_dir / f"REPORT{idx:02d}_{src.name}")

    def write_index(self) -> None:
        manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "style": "figures4papers_show2know_nature_skills_publication_figures",
            "backend": "python_matplotlib",
            "figure_count": len(self.rows),
            "formats": ["svg_editable_text", "pdf", f"png_{DPI}dpi", f"tiff_{DPI}dpi"],
            "canvas_contract": {
                "primary_canvas_inches": "14 x 4.8 for wide comparison figures",
                "short_canvas_inches": "14 x 3.45 for method timelines",
                "image_plate_inches": "14 x 7.7 for screenshot plates",
                "bbox_inches": "not used; fixed final-size canvases are preserved",
            },
            "palette": {
                "ink": INK,
                "main_blue": BLUE,
                "secondary_blue": BLUE_SECONDARY,
                "green": GREEN_3,
                "red": RED_STRONG,
                "neutral": GRAY_LIGHT,
            },
            "references": [
                "https://github.com/ChenLiu-1996/figures4papers",
                "https://github.com/Winn1y/Show2Know",
                "https://github.com/Yuan1z0825/nature-skills",
                "https://matplotlib.org/stable/users/explain/customizing.html",
            ],
            "figure_contracts": self.contracts,
            "figures": self.rows,
        }
        (OUT_ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = [
            "# MemGuardOS figures4papers 科研图风格图包",
            "",
            "本目录由 `scripts/generate_figures4papers_style_figures.py` 生成，并复用 `scripts/generate_plottie_publication_figures.py` 的图件注册逻辑。代码按图件类别拆分为 `scripts/plottie_figures/method_schematics.py`、`scripts/plottie_figures/quantitative_results.py` 和 `scripts/plottie_figures/image_plates.py`，共享宽画布、Helvetica-like 字体、粗轴线、语义配色、导出和证据登记逻辑在 `scripts/plottie_figures/style.py`。",
            "",
            "图件设计参考 `figures4papers` 的顶会/顶刊论文图 house style：大字号、粗坐标轴、宽画布、多面板横向叙事、直接数值标注、蓝/绿/红/灰语义配色和独立图例空间；同时参考 `Show2Know` 的基础图型组织和 `nature-skills` 的 Figure Contract、证据链与导出审计流程。所有定量图均来自项目真实 source data，不绘制项目不存在的训练曲线、PR/ROC 或外部排行榜。",
            "",
            "参考示例已经整理在 `outputs/科研图参考示例学习包/`，其中 `reference_contact_sheet.png` 用于对比 figures4papers、Show2Know 和 nature-skills 的代表图面。`figures4papers` 本地仓库未提供 LICENSE 文件，因此本项目只学习其风格模式并重新实现绘图逻辑；`Show2Know` 与 `nature-skills` 分别按 Apache-2.0 和 MIT 许可证作为参考。",
            "",
            "## 图表索引",
            "",
            "| 图号 | 图名 | 类型 | SVG | PDF | PNG | TIFF |",
            "|---|---|---|---|---|---|---|",
        ]
        for rec in self.rows:
            lines.append(
                f"| {rec['图号']} | {rec['图名']} | {rec['类型']} | [{Path(rec['svg']).name}]({rec['svg']}) | [{Path(rec['pdf']).name}]({rec['pdf']}) | [{Path(rec['png']).name}]({rec['png']}) | [{Path(rec['tiff']).name}]({rec['tiff']}) |"
            )
        lines.extend(
            [
                "",
                "## 使用边界",
                "",
                "1. F01-F09、F16、F18 是方法示意或证据点阵，不是性能曲线。",
                "2. F10-F15 是定量图，数据来自项目 JSON、openKylin 证据和 manifest，并在 `source_data/` 下提供 CSV。",
                "3. F17、F19、F20 是实际截图 image plate，裁剪仅为可读性，不改写证据。",
                "4. 本项目没有训练曲线、PR/ROC、外部数据库专属性能证据或公开 leaderboard，因此不绘制这些图。",
            ]
        )
        (OUT_ROOT / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

        contract_lines = [
            "# MemGuardOS figures4papers 风格科研图件逐图设计契约",
            "",
            "| 图号 | 核心结论 | 图件类型 | 证据链 | 审稿风险控制 |",
            "|---|---|---|---|---|",
        ]
        for row in self.contracts:
            contract_lines.append(
                f"| {row['图号']} | {row['核心结论']} | {row['图件类型']} | {row['证据链']} | {row['审稿风险控制']} |"
            )
        (OUT_ROOT / "逐图设计契约.md").write_text("\n".join(contract_lines) + "\n", encoding="utf-8")
