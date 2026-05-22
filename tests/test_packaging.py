from pathlib import Path

from scripts.package_submission import COMPETITION_ROLES, classify_file


def test_competition_package_keeps_formal_and_process_materials_separate():
    formal_paths = [
        "memguard/service.py",
        "scripts/install_openkylin.sh",
        "scripts/collect_openkylin_evidence.sh",
        "native/bpf_lsm/memguard_lsm.bpf.c",
        "docs/项目介绍总览.md",
        "docs/赛题符合性说明.md",
        "docs/evidence/latest/pytest.txt",
        "pyproject.toml",
    ]
    process_paths = [
        "scripts/package_submission.py",
        "scripts/check_competition_ready.py",
        "scripts/check_docs_links.py",
        "docs/赛题要求逐句符合性核查.md",
        "docs/竞赛就绪自检报告.md",
        "CHANGELOG.md",
        ".github/workflows/ci.yml",
        "Dockerfile",
        "Makefile",
        "tests/test_packaging.py",
    ]
    for rel in formal_paths:
        assert classify_file(Path(rel)) in COMPETITION_ROLES, rel
    for rel in process_paths:
        assert classify_file(Path(rel)) not in COMPETITION_ROLES, rel
