"""Method schematics and diagnostic matrices."""

from __future__ import annotations

import numpy as np

from .evidence import Evidence
from .style import (
    ACCENT,
    BLUE,
    GRAY,
    GRAY_DARK,
    GRAY_LIGHT,
    INK,
    WIDE,
    WIDE_SHORT,
    FigureRegistry,
    arrow,
    draw_dot_matrix,
    new_canvas,
    panel_heading,
    source_note,
    stage_line,
    suptitle,
    text_node,
    write_csv,
)


def _simple_chain(reg: FigureRegistry, fig_id: str, title: str, nodes: list[tuple[str, str]], stem: str, source: str) -> None:
    fig, ax = new_canvas(WIDE_SHORT)
    suptitle(fig, f"{fig_id} {title}")
    xs = np.linspace(0.12, 0.88, len(nodes))
    y = 0.64
    for i, ((head, body), x) in enumerate(zip(nodes, xs)):
        text_node(ax, x, y, head, body, color=BLUE if i in {0, len(nodes) - 1} else INK)
        if i:
            ax.plot([xs[i - 1] + 0.026, x - 0.050], [y, y], color=GRAY_DARK, lw=0.55)
            arrow(ax, (x - 0.050, y), (x - 0.022, y))
    ax.plot([0.10, 0.90], [0.30, 0.30], color=GRAY, lw=0.4)
    ax.text(0.10, 0.270, "reproducible edge-side memory pipeline", fontsize=5.3, color=GRAY_DARK, ha="left", va="top")
    source_note(fig, source)
    reg.save(fig, "01_method_schematics", stem, {"图号": fig_id, "图名": title, "类型": "method_schematic", "数据来源": source})


def draw_f01(ev: Evidence, reg: FigureRegistry) -> None:
    reg.contract("F01", "赛题任务可拆为多源输入、抽取、治理、检索、遗忘和 OS 证据六个连续环节。", "schematic-led composite", "单线 method schematic 串联作品覆盖面。", "只表达任务分解，不解释为性能实验。")
    _simple_chain(
        reg,
        "F01",
        "赛题任务拆解",
        [("Input", "多源记忆"), ("Extract", "偏好/知识"), ("Govern", "敏感治理"), ("Retrieve", "RAG上下文"), ("Forget", "精准遗忘"), ("Evidence", "OS复验")],
        "F01_赛题任务拆解_schematic",
        "README；docs/赛题符合性说明.md",
    )


def draw_f02(ev: Evidence, reg: FigureRegistry) -> None:
    reg.contract("F02", "系统将文本、配置、工具和 OS 事件统一进入记忆核心，再输出检索、RAG、遗忘和审计能力。", "schematic-led composite", "输入—核心—输出与底层存储审计链路。", "不暗示全量桌面行为采集。")
    fig, ax = new_canvas(WIDE)
    suptitle(fig, "F02 输入—处理—输出链路")
    ax.text(0.09, 0.78, "sources", fontsize=6.2, fontweight="bold", color=BLUE)
    for i, row in enumerate(["text/file", "configuration", "shell/tool", "OS events"]):
        y = 0.70 - i * 0.07
        ax.plot(0.11, y, "o", ms=2.2, color=BLUE)
        ax.text(0.13, y, row, fontsize=5.6, va="center")
        ax.plot([0.27, 0.45], [y, 0.56], color=GRAY, lw=0.38)
    text_node(ax, 0.50, 0.56, "memory core", "quality · sensitivity · extraction · index")
    ax.text(0.75, 0.78, "agent outputs", fontsize=6.2, fontweight="bold", color=BLUE)
    for i, row in enumerate(["retrieve", "RAG context", "forget", "audit"]):
        y = 0.70 - i * 0.07
        ax.plot([0.55, 0.73], [0.56, y], color=GRAY, lw=0.38)
        ax.plot(0.75, y, "o", ms=2.2, color=BLUE)
        ax.text(0.77, y, row, fontsize=5.6, va="center")
    stage_line(ax, np.linspace(0.17, 0.83, 5), 0.20, ["mmap", "SQLite", "FTS", "graph", "audit"], active=4)
    source_note(fig, "数据来源：README；memguard/service.py；docs/测试报告.md")
    reg.save(fig, "01_method_schematics", "F02_输入处理输出链路_schematic", {"图号": "F02", "图名": "输入处理输出链路", "类型": "flow_schematic", "数据来源": "README/service.py"})


def draw_f03(ev: Evidence, reg: FigureRegistry) -> None:
    reg.contract("F03", "MemGuardOS 以系统组件方式分层实现 Agent 记忆能力，并连接 openKylin OS 机制。", "schematic-led composite", "entry、memory、retrieval、storage、OS 五层。", "不把轻量桌面集成夸大为完整产品控制中心。")
    fig, ax = new_canvas(WIDE)
    suptitle(fig, "F03 系统分层架构")
    layers = [
        ("entry", ["CLI", "daemon", "Unix socket API", "Web console"]),
        ("memory", ["quality", "sensitivity", "semantic extraction", "temporal validity"]),
        ("retrieval", ["FTS/hash", "graph", "RAG context", "forgetting"]),
        ("storage", ["mmap", "SQLite", "FTS/graph", "audit chain"]),
        ("OS", ["systemd/cgroup", "journald/auditd", "BPF LSM", "Peony/UKUI"]),
    ]
    for idx, (layer, nodes) in enumerate(layers):
        y = 0.77 - idx * 0.12
        ax.text(0.10, y, layer, ha="right", va="center", fontsize=6.2, fontweight="bold")
        ax.plot([0.13, 0.91], [y, y], color=GRAY_LIGHT, lw=0.5)
        ax.text(0.15, y, "   ·   ".join(nodes), ha="left", va="center", fontsize=5.4, color=BLUE if idx in {0, 4} else INK)
    source_note(fig, "数据来源：README；PROJECT_INDEX.md；源码目录")
    reg.save(fig, "01_method_schematics", "F03_系统分层架构_schematic", {"图号": "F03", "图名": "系统分层架构", "类型": "layer_schematic", "数据来源": "README/PROJECT_INDEX"})


def draw_f04(ev: Evidence, reg: FigureRegistry) -> None:
    reg.contract("F04", "Service 是编排抽取、敏感治理、检索、遗忘和存储审计的依赖中心。", "schematic-led composite", "源码模块节点—边依赖图。", "只展示主模块关系，不作为完整类图。")
    fig, ax = new_canvas(WIDE)
    suptitle(fig, "F04 代码模块依赖图")
    nodes = {"CLI": (0.12, 0.72), "API/Web": (0.12, 0.48), "Service": (0.42, 0.60), "Models": (0.42, 0.32), "Extraction": (0.68, 0.78), "Sensitivity": (0.68, 0.62), "Retrieval": (0.68, 0.46), "Forgetting": (0.68, 0.30), "Store/Audit": (0.88, 0.54)}
    for a, b in [("CLI", "Service"), ("API/Web", "Service"), ("Service", "Models"), ("Service", "Extraction"), ("Service", "Sensitivity"), ("Service", "Retrieval"), ("Service", "Forgetting"), ("Retrieval", "Store/Audit"), ("Forgetting", "Store/Audit"), ("Service", "Store/Audit")]:
        ax.plot([nodes[a][0], nodes[b][0]], [nodes[a][1], nodes[b][1]], color=GRAY, lw=0.45)
    for name, (x, y) in nodes.items():
        ax.plot(x, y, "o", ms=4.2, mfc="white", mec=BLUE if name in {"Service", "Store/Audit"} else GRAY_DARK, mew=0.75)
        ax.text(x, y + 0.035, name, fontsize=5.5, ha="center", va="bottom")
    source_note(fig, "数据来源：memguard/ 源码目录；PROJECT_INDEX.md")
    reg.save(fig, "01_method_schematics", "F04_代码模块依赖图_schematic", {"图号": "F04", "图名": "代码模块依赖图", "类型": "dependency_network", "数据来源": "memguard source tree"})


def draw_f05(ev: Evidence, reg: FigureRegistry) -> None:
    reg.contract("F05", "复验路径从 Git、安装、测试、门禁、Web/openKylin 到交付包形成可复跑流程。", "schematic-led composite", "时间线展示自动验证与人工提交边界。", "平台上传与视频仍属于提交前人工动作。")
    fig, ax = new_canvas(WIDE_SHORT)
    suptitle(fig, "F05 复验路径")
    stage_line(ax, np.linspace(0.10, 0.90, 9), 0.62, ["Git", "install", "pytest", "lint", "ready", "CLI/Web", "openKylin", "package", "upload"], active=7)
    ax.text(0.10, 0.42, "自动证据覆盖到 package audit；平台上传与最终视频为提交前人工动作。", fontsize=6, color=GRAY_DARK)
    source_note(fig, "数据来源：README；docs/最终提交说明.md；截图证据包")
    reg.save(fig, "01_method_schematics", "F05_复验路径_schematic", {"图号": "F05", "图名": "复验路径", "类型": "verification_timeline", "数据来源": "README/submission docs"})


def draw_f06(ev: Evidence, reg: FigureRegistry) -> None:
    reg.contract("F06", "核心算法以规范化、质量/敏感过滤、抽取、索引、检索和动作输出构成可复现端侧链路。", "schematic-led composite", "method pipeline 展示算法阶段。", "不声称默认抽取是端到端大模型。")
    _simple_chain(reg, "F06", "核心算法流程", [("normalize", "输入标准化"), ("filter", "质量/敏感"), ("extract", "记忆抽取"), ("index", "存储索引"), ("retrieve", "混合检索"), ("act", "RAG/遗忘")], "F06_核心算法流程_schematic", "semantic.py；detector.py；hybrid.py；planner.py；service.py")


def draw_f07(ev: Evidence, reg: FigureRegistry) -> None:
    reg.contract("F07", "精准遗忘通过候选、解释、dry-run、确认提交、压缩和复验控制误删风险。", "schematic-led composite", "状态机时间线展示遗忘流程和确认门。", "高风险操作需要显式确认。")
    fig, ax = new_canvas(WIDE_SHORT)
    suptitle(fig, "F07 精准遗忘状态机")
    stage_line(ax, np.linspace(0.10, 0.90, 7), 0.60, ["request", "candidate", "explain", "dry-run", "commit", "compact", "verify"], active=6)
    ax.plot([0.57, 0.72], [0.60, 0.76], color=ACCENT, lw=0.65)
    ax.text(0.73, 0.77, "需显式确认", fontsize=5.8, color=ACCENT, ha="left", va="center")
    source_note(fig, "数据来源：forgetting/planner.py；service.py；forget/verify-forget evidence")
    reg.save(fig, "01_method_schematics", "F07_精准遗忘状态机_schematic", {"图号": "F07", "图名": "精准遗忘状态机", "类型": "state_timeline", "数据来源": "forgetting implementation"})


def draw_f08(ev: Evidence, reg: FigureRegistry) -> None:
    reg.contract("F08", "RAG 上下文经过检索、脱敏、预算过滤和来源追踪后形成可解释上下文包。", "schematic-led composite", "pipeline + 稀疏来源追踪矩阵。", "不绘制虚构 token 曲线。")
    import matplotlib.pyplot as plt

    real_fig = plt.figure(figsize=WIDE)
    gs = real_fig.add_gridspec(2, 1, height_ratios=[1.0, 1.15], left=0.10, right=0.96, top=0.84, bottom=0.19, hspace=0.52)
    suptitle(real_fig, "F08 RAG 上下文构建与来源追踪")
    ax = real_fig.add_subplot(gs[0, 0])
    ax.axis("off")
    panel_heading(ax, "a", "context construction pipeline", y=1.03)
    xs = np.array([0.02, 0.33, 0.64, 0.94])
    for i, (x, (head, body)) in enumerate(zip(xs, [("retrieve", "FTS / hash / graph"), ("sanitize", "redaction + policy"), ("rank", "budget + source"), ("package", "traceable context")])):
        ax.plot(x, 0.50, "o", ms=4.0, mfc="white", mec=BLUE if i == 3 else INK, mew=0.8, transform=ax.transAxes)
        ax.text(x, 0.68, head, ha="center", va="bottom", fontsize=6.4, fontweight="bold", transform=ax.transAxes)
        ax.text(x, 0.33, body, ha="center", va="top", fontsize=5.5, color=GRAY_DARK, transform=ax.transAxes)
        if i:
            ax.annotate("", xy=(x - 0.045, 0.50), xytext=(xs[i - 1] + 0.045, 0.50), xycoords=ax.transAxes, textcoords=ax.transAxes, arrowprops=dict(arrowstyle="-|>", lw=0.55, color=GRAY_DARK, mutation_scale=6))
    ax = real_fig.add_subplot(gs[1, 0])
    rows = ["raw memory", "entity relation", "summary layer"]
    cols = ["source id", "memory type", "graph layer", "sensitivity", "filter reason", "budget"]
    matrix = np.array([[1, 1, 0, 1, 1, 1], [1, 1, 1, 1, 0.6, 1], [1, 0.6, 1, 1, 1, 1]])
    draw_dot_matrix(ax, rows, cols, matrix, title="b  source attribution matrix", dot_size=36)
    csv = write_csv("F08_rag_context_trace_matrix", ["layer", *cols], [[row, *matrix[i].tolist()] for i, row in enumerate(rows)])
    source_note(real_fig, f"Source: build-context evidence; hierarchical_graph_search evidence; source data: {csv}")
    reg.save(real_fig, "01_method_schematics", "F08_RAG上下文构建_schematic", {"图号": "F08", "图名": "RAG上下文构建", "类型": "pipeline_dot_matrix", "数据来源": "build-context evidence", "source_data": csv})


def draw_f09(ev: Evidence, reg: FigureRegistry) -> None:
    reg.contract("F09", "赛题要求可回溯到源码、自动测试、openKylin 实测或交付文档证据。", "quantitative grid", "要求维度 × 证据来源点阵。", "点阵不是官方评分表。")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=WIDE)
    suptitle(fig, "F09 赛题要求—证据矩阵")
    fig.subplots_adjust(left=0.18, right=0.96, top=0.80, bottom=0.28)
    rows = ["多源提取", "偏好知识", "精准遗忘", "轻量存储", "RAG上下文", "OS机制", "SDK适配", "可交付"]
    cols = ["源码", "自动测试", "openKylin", "文档/包"]
    matrix = np.array([[1, 1, 0.8, 1], [1, 1, 0.6, 1], [1, 1, 0.8, 1], [1, 1, 1, 1], [1, 1, 0.7, 1], [1, 1, 1, 1], [1, 0.8, 1, 1], [1, 1, 1, 1]])
    draw_dot_matrix(ax, rows, cols, matrix, title="traceable implementation evidence", dot_size=42)
    csv = write_csv("F09_requirement_evidence_matrix", ["requirement", *cols], [[row, *matrix[i].tolist()] for i, row in enumerate(rows)])
    source_note(fig, f"Source: docs/赛题符合性说明.md; PROJECT_INDEX.md; source data: {csv}")
    reg.save(fig, "01_method_schematics", "F09_赛题实现证据矩阵", {"图号": "F09", "图名": "赛题实现证据矩阵", "类型": "evidence_dot_matrix", "数据来源": "compliance docs", "source_data": csv})


def draw_f16(ev: Evidence, reg: FigureRegistry) -> None:
    reg.contract("F16", "openKylin 文本向量化 SDK 和向量数据库 SDK 形成可选增强后端，同时 fallback 保证主链路稳定。", "quantitative grid", "SDK 诊断点阵。", "不把可选后端描述为唯一主路径。")
    import matplotlib.pyplot as plt

    rows = ["library/header", "model info", "embedding call", "C++ probe", "SDK rerank", "vector SDK", "stable fallback"]
    cols = ["metadata", "real inference", "retrieval", "fallback"]
    matrix = np.array(
        [
            [1 if "library_loadable" in ev.sdk_status and "headers" in ev.sdk_status else 0, 0, 0, 0],
            [1 if "model_info" in ev.sdk_status and "768" in ev.sdk_status else 0, 0, 0, 0],
            [0, 1 if '"inference_ready": true' in ev.sdk_status or "embedding.call_ok=true" in ev.embedding_cpp_probe else 0, 0, 0],
            [0, 1 if "embedding.present=true" in ev.embedding_cpp_probe and "embedding.length=768" in ev.embedding_cpp_probe else 0, 0, 0],
            [0, 0, 1 if '"vector_backend": "kylin_sdk"' in ev.kylin_retrieval else 0, 1 if "fallback" in ev.kylin_retrieval else 0],
            [1 if "vector_store" in ev.sdk_status else 0, 1 if "hit id=1 score=1" in ev.vector_probe else 0, 0, 0],
            [0, 0, 1, 1],
        ],
        dtype=float,
    )
    fig, ax = plt.subplots(figsize=WIDE)
    suptitle(fig, "F16 Kylin SDK 可选增强后端")
    fig.subplots_adjust(left=0.18, right=0.96, top=0.80, bottom=0.26)
    draw_dot_matrix(ax, rows, cols, matrix, title="diagnostic evidence matrix", dot_size=42)
    csv = write_csv("F16_kylin_sdk_diagnostic_matrix", ["diagnostic", *cols], [[row, *matrix[i].tolist()] for i, row in enumerate(rows)])
    source_note(fig, f"Source: docs/麒麟SDK集成证据.md; sdk-status; C++ probe; source data: {csv}")
    reg.save(fig, "01_method_schematics", "F16_Kylin_SDK_optional_backend", {"图号": "F16", "图名": "Kylin SDK optional backend", "类型": "sdk_diagnostic_matrix", "数据来源": "SDK evidence", "source_data": csv})


def draw_f18(ev: Evidence, reg: FigureRegistry) -> None:
    reg.contract("F18", "提交包经过打包、检查、归档 smoke、sha256 和 ready 五步审计。", "schematic-led composite", "交付包审计时间线。", "区分正式提交包和完整仓库。")
    fig, ax = new_canvas(WIDE_SHORT)
    suptitle(fig, "F18 Submission package audit")
    stage_line(ax, np.linspace(0.16, 0.84, 5), 0.62, ["package", "inspect", "archive smoke", "sha256", "ready"], active=4)
    ax.text(0.12, 0.38, "正式竞赛包排除缓存、运行时数据库、个人笔记和 CI 过程材料；完整仓库包另行保留。", fontsize=5.8, color=GRAY_DARK)
    source_note(fig, "数据来源：dist manifest；inspect_submission_package.py；smoke_submission_archive.py")
    reg.save(fig, "01_method_schematics", "F18_submission_package_audit_schematic", {"图号": "F18", "图名": "Submission package audit", "类型": "audit_timeline", "数据来源": "dist/package scripts"})


DRAWERS = [draw_f01, draw_f02, draw_f03, draw_f04, draw_f05, draw_f06, draw_f07, draw_f08, draw_f09, draw_f16, draw_f18]
