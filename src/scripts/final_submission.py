"""Build and verify the official finals repository layout."""

from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib
import json
import subprocess


SOURCE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SOURCE_ROOT.parent
REQUIRED = {
    "README.md", "src/pyproject.toml", "src/memguard/service.py",
    "docs/项目说明书.pdf", "docs/项目创新说明.pdf",
    "docs/成员分工及主要贡献说明.pdf", "docs/人工智能及第三方工具使用说明.pdf",
    "presentation/决赛现场演示PPT.pptx", "presentation/决赛演示视频.mp4",
    "作品原创承诺书.pdf", "tests/README.md", "demo/README.md", "demo/install.sh",
}


def is_finals_layout() -> bool:
    return SOURCE_ROOT.name == "src" and (REPOSITORY_ROOT / "docs/项目说明书.pdf").is_file()


def inspect_final_archive(package: Path, manifest: Path | None) -> dict:
    with ZipFile(package) as archive:
        names = set(archive.namelist())
        hashes = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in names}
        packages = sorted(name for name in names if name.startswith("demo/packages/") and name.endswith(".deb"))
        video_size = archive.getinfo("presentation/决赛演示视频.mp4").file_size if "presentation/决赛演示视频.mp4" in names else 0
    missing = sorted(REQUIRED - names)
    allowed = {"README.md", "src", "docs", "tests", "demo", "presentation", "作品原创承诺书.pdf"}
    unexpected = sorted(name for name in names if name.split("/")[0] not in allowed)
    forbidden = sorted(name for name in names if "__pycache__" in Path(name).parts or name.endswith((".pyc", ".sqlite3", ".segment")))
    manifest_ok = False
    if manifest is not None and manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        manifest_hashes = {item["path"]: item["sha256"] for item in data.get("files", [])}
        manifest_ok = manifest_hashes == hashes and data.get("archive_sha256") == hashlib.sha256(package.read_bytes()).hexdigest()
    return {
        "ok": not missing and not unexpected and not forbidden and len(packages) == 5 and 0 < video_size < 100_000_000 and manifest_ok,
        "layout": "official_finals", "file_count": len(names), "missing_required": missing,
        "unexpected_roots": unexpected, "forbidden_files": forbidden, "debian_packages": packages,
        "video_bytes": video_size, "manifest_ok": manifest_ok,
    }


def build_final_archive(output_dir: Path, timestamp: str, kind: str) -> None:
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=REPOSITORY_ROOT).decode("utf-8").split("\0")
    files = [REPOSITORY_ROOT / name for name in names if name and (REPOSITORY_ROOT / name).is_file()]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT, text=True).strip()
    output_dir.mkdir(parents=True, exist_ok=True)
    kinds = ["competition_submission", "full_repository"] if kind == "all" else ["competition_submission" if kind == "competition" else "full_repository"]
    for archive_kind in kinds:
        archive_path = output_dir / f"memguardos_{archive_kind}_{timestamp}.zip"
        entries = []
        with ZipFile(archive_path, "w", ZIP_DEFLATED, compresslevel=6) as archive:
            for path in files:
                relative = path.relative_to(REPOSITORY_ROOT).as_posix()
                archive.write(path, relative)
                entries.append({"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size})
        manifest_path = archive_path.with_name(archive_path.stem + "_manifest.json")
        manifest_path.write_text(json.dumps({"layout": "official_finals", "source_revision": revision, "files": entries,
            "archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest()}, ensure_ascii=False, indent=2), encoding="utf-8")
        report = inspect_final_archive(archive_path, manifest_path)
        print(json.dumps({"package": str(archive_path), **report}, ensure_ascii=False, indent=2))
        if not report["ok"]:
            raise RuntimeError("Final submission archive validation failed")
