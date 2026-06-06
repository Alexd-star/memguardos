from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from scripts.inspect_submission_package import REQUIRED_PATHS, inspect_package
from scripts.package_submission import COMPETITION_ROLES, ROOT, classify_file, should_skip
from scripts.smoke_submission_archive import command_name


def test_competition_package_keeps_formal_and_process_materials_separate():
    formal_paths = [
        "memguard/service.py",
        "scripts/install_openkylin.sh",
        "scripts/build_deb.sh",
        "scripts/collect_openkylin_evidence.sh",
        "native/bpf_lsm/memguard_lsm.bpf.c",
        "docs/项目介绍总览.md",
        "docs/赛题符合性说明.md",
        "docs/evidence/latest/pytest.txt",
        "pyproject.toml",
    ]
    process_paths = [
        "scripts/package_submission.py",
        "scripts/inspect_submission_package.py",
        "scripts/release_preflight.py",
        "scripts/smoke_submission_archive.py",
        "scripts/check_competition_ready.py",
        "scripts/check_docs_links.py",
        "docs/赛题要求逐句符合性核查.md",
        "docs/竞赛就绪自检报告.md",
        "docs/research/智能体记忆论文深度调研与MemGuardOS改进建议.md",
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
    assert should_skip(ROOT / "docs/research/sources/arxiv_2512.13564.pdf") is True


def test_submission_package_inspector_rejects_process_materials(tmp_path):
    package = tmp_path / "submission.zip"
    manifest = tmp_path / "submission_manifest.json"
    names = sorted(REQUIRED_PATHS | {"scripts/release_preflight.py", "scripts/smoke_submission_archive.py"})
    with ZipFile(package, "w", ZIP_DEFLATED) as zf:
        for name in names:
            zf.writestr(name, "x")
    manifest.write_text(
        '{"files": [%s]}'
        % ",".join('{"path": "%s", "role": "%s"}' % (name, classify_file(Path(name))) for name in names),
        encoding="utf-8",
    )
    report = inspect_package(package, manifest)
    assert report["ok"] is False
    assert "scripts/release_preflight.py" in report["forbidden_hits"]
    assert "scripts/smoke_submission_archive.py" in report["forbidden_hits"]


def test_submission_package_inspector_accepts_required_formal_materials(tmp_path):
    package = tmp_path / "submission.zip"
    manifest = tmp_path / "submission_manifest.json"
    names = sorted(REQUIRED_PATHS)
    with ZipFile(package, "w", ZIP_DEFLATED) as zf:
        for name in names:
            zf.writestr(name, "x")
    manifest.write_text(
        '{"files": [%s]}'
        % ",".join('{"path": "%s", "role": "%s"}' % (name, classify_file(Path(name))) for name in names),
        encoding="utf-8",
    )
    report = inspect_package(package, manifest)
    assert report["ok"] is True
    assert report["missing_required"] == []
    assert report["forbidden_hits"] == []
    assert report["local_path_hits"] == []


def test_submission_archive_smoke_command_names_are_stable():
    assert command_name(["python", "-m", "compileall", "-q", "memguard"]) == "compileall"
    assert command_name(["python", "scripts/web_dashboard_smoke.py"]) == "web_dashboard_smoke.py"
    assert command_name([]) == "unknown"


def test_submission_package_inspector_rejects_local_paths_in_formal_docs(tmp_path):
    package = tmp_path / "submission.zip"
    manifest = tmp_path / "submission_manifest.json"
    names = sorted(REQUIRED_PATHS)
    with ZipFile(package, "w", ZIP_DEFLATED) as zf:
        for name in names:
            body = "cd D:\\Luo_docs\\竞赛\\研创赛\\memguardos" if name == "README.md" else "x"
            zf.writestr(name, body)
    manifest.write_text(
        '{"files": [%s]}'
        % ",".join('{"path": "%s", "role": "%s"}' % (name, classify_file(Path(name))) for name in names),
        encoding="utf-8",
    )
    report = inspect_package(package, manifest)
    assert report["ok"] is False
    assert report["local_path_hits"] == [{"path": "README.md", "line": 1, "markers": ["D:\\", "Luo_docs"]}]
