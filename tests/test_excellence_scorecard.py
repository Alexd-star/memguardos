from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

FORMAL_DOCS = [
    "README.md",
    "docs/13_测试说明.md",
    "docs/15_更新日志.md",
    "docs/平台摘要.md",
    "docs/最终提交说明.md",
    "docs/测试报告.md",
    "docs/真实Agent闭环测试报告.md",
    "docs/真实Agent记忆价值评测报告.md",
    "docs/赛题要求逐句符合性核查.md",
    "docs/赛题符合性说明.md",
    "docs/评委证据包索引.md",
    "docs/答辩提纲.md",
    "docs/答辩稿.md",
    "docs/项目介绍总览.md",
    "docs/交付文档/MemGuardOS中文操作手册.md",
]

OPENKYLIN_NATIVE_PRODUCT_DOCS = [
    "README.md",
    "native/ukui/README.md",
    "docs/openKylin验证记录.md",
    "docs/openKylin原生化适配说明.md",
    "docs/Peony与UKUI桌面集成实现说明.md",
    "docs/部署手册.md",
    "docs/操作系统机制说明.md",
    "docs/交付文档/MemGuardOS中文操作手册.md",
]


def _load_scorecard_module():
    path = ROOT / "scripts/generate_excellence_scorecard.py"
    spec = importlib.util.spec_from_file_location("generate_excellence_scorecard", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_excellence_scorecard_is_generated_from_current_evidence(tmp_path: Path) -> None:
    module = _load_scorecard_module()
    output = tmp_path / "超标准交付评分矩阵.md"

    module.write_scorecard(output)

    text = output.read_text(encoding="utf-8")
    assert "MemGuardOS 超标准交付评分矩阵" in text
    assert "308 passed, 1 skipped" in text
    assert "openKylin 自动化测试" in text
    assert "passed" in text
    assert "49.48x" in text
    assert "OWASP Agentic AI" in text
    assert "NIST AI RMF" in text
    assert "LangMem" in text
    assert "OpenAI Agents SDK" in text
    assert "不能夸大" in text


def test_formal_docs_do_not_keep_stale_test_counts() -> None:
    stale_tokens = ["283 passed", "284 passed", "289 passed", "290 passed", "292 passed", "293 passed", "294 passed", "295 passed", "296 passed"]
    offenders: list[str] = []
    for rel in FORMAL_DOCS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for token in stale_tokens:
            if token in text:
                offenders.append(f"{rel}: {token}")
    assert offenders == []


def test_openkylin_product_docs_use_mature_user_language() -> None:
    forbidden_tokens = [
        "端侧向量能力",
        "端侧向量能力检查",
        "检查端侧向量能力",
        "预热 Kylin SDK",
        "【当前任务】",
    ]
    offenders: list[str] = []
    for rel in OPENKYLIN_NATIVE_PRODUCT_DOCS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for token in forbidden_tokens:
            if token in text:
                offenders.append(f"{rel}: {token}")
    assert offenders == []



