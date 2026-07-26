"""Actual screenshot evidence plates."""

from __future__ import annotations

import numpy as np
from PIL import Image

from .evidence import Evidence, screenshot_path
from .style import GRAY_DARK, IMAGE_PLATE, TALL, FigureRegistry, source_note, suptitle


CROP_PROFILES: dict[str, tuple[float, float, float, float]] = {
    "E008_": (0.00, 0.06, 1.00, 0.50),
    "E022_": (0.00, 0.04, 1.00, 0.86),
    "E024_": (0.00, 0.07, 1.00, 0.36),
    "E040_": (0.00, 0.05, 1.00, 0.28),
    "E041_": (0.00, 0.06, 1.00, 0.31),
    "E042_": (0.00, 0.05, 1.00, 0.30),
    "E043_": (0.00, 0.07, 1.00, 0.40),
    "E044_": (0.00, 0.04, 1.00, 0.28),
    "E045_": (0.00, 0.05, 1.00, 0.46),
    "E048_": (0.00, 0.06, 1.00, 0.30),
}


def _crop(path, prefix: str, max_size: tuple[int, int] = (1700, 1300)) -> Image.Image:
    img = Image.open(path).convert("RGB")
    profile = next((box for key, box in CROP_PROFILES.items() if prefix.startswith(key)), None)
    if profile:
        w, h = img.size
        x0, y0, x1, y1 = profile
        img = img.crop((int(w * x0), int(h * y0), int(w * x1), int(h * y1)))
    img.thumbnail(max_size)
    return img


def _plate(reg: FigureRegistry, fig_id: str, title: str, shots: list[tuple[str, str]], stem: str) -> None:
    import matplotlib.pyplot as plt

    if len(shots) == 3:
        fig = plt.figure(figsize=IMAGE_PLATE)
        axes = [
            fig.add_axes([0.045, 0.22, 0.44, 0.58]),
            fig.add_axes([0.565, 0.50, 0.39, 0.30]),
            fig.add_axes([0.565, 0.14, 0.39, 0.30]),
        ]
    else:
        ncols = 2 if len(shots) > 2 else len(shots)
        nrows = int(np.ceil(len(shots) / ncols))
        fig, axes_grid = plt.subplots(nrows, ncols, figsize=IMAGE_PLATE if len(shots) > 2 else TALL)
        axes = list(np.array(axes_grid, dtype=object).reshape(-1))
        fig.subplots_adjust(left=0.045, right=0.985, top=0.83, bottom=0.105, wspace=0.16, hspace=0.30)
    suptitle(fig, f"{fig_id} {title}")
    used: list[str] = []
    for ax, (prefix, label) in zip(axes, shots):
        path = screenshot_path(prefix)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title(label, loc="left", fontsize=6.4, pad=3)
        if path and path.exists():
            ax.imshow(_crop(path, prefix))
            used.append(path.name)
            ax.set_frame_on(True)
            for spine in ax.spines.values():
                spine.set_visible(True)
                spine.set_color(GRAY_DARK)
                spine.set_linewidth(0.45)
        else:
            ax.set_axis_off()
            ax.text(0.5, 0.5, "not available", ha="center", va="center", fontsize=7, color=GRAY_DARK)
    for ax in axes[len(shots) :]:
        ax.set_axis_off()
    source_note(fig, "Image plate from actual screenshots; panels are cropped for readability: " + ("; ".join(used) if used else "none available"))
    reg.save(fig, "03_image_plates", stem, {"图号": fig_id, "图名": title, "类型": "image_plate", "数据来源": "; ".join(used)})


def draw_f17(ev: Evidence, reg: FigureRegistry) -> None:
    reg.contract("F17", "Web 控制台、评测结果和技术报告指标页具备实际截图证据。", "image plate + quant", "三面板 image plate 使用真实截图。", "裁剪只为可读性，不改写截图内容。")
    _plate(reg, "F17", "Product and evaluation evidence plate", [("E022_", "web smoke"), ("E024_", "local metrics"), ("E040_", "report metrics")], "F17_product_evaluation_image_plate")


def draw_f19(ev: Evidence, reg: FigureRegistry) -> None:
    reg.contract("F19", "证据目录、SHA256 清单和 READY 检查具备可截图审计证据。", "image plate + quant", "目录、校验和 ready 三类截图。", "只使用实际截图，不生成伪造上传成功图。")
    _plate(reg, "F19", "Evidence package plate", [("E008_", "evidence dir"), ("E045_", "SHA256"), ("E048_", "READY")], "F19_evidence_package_image_plate")


def draw_f20(ev: Evidence, reg: FigureRegistry) -> None:
    reg.contract("F20", "openKylin 10k 性能、Kylin SDK、OS 机制和 deb 安装均有实际截图证据。", "image plate + quant", "四面板 image plate 展示 openKylin 集成证据。", "截图 plate 是证据索引，不替代完整日志。")
    _plate(reg, "F20", "openKylin integration plate", [("E041_", "10k perf"), ("E042_", "SDK inference"), ("E043_", "OS mechanisms"), ("E044_", ".deb install")], "F20_openKylin_integration_image_plate")


DRAWERS = [draw_f17, draw_f19, draw_f20]

