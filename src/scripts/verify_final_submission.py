"""Verify the submission layout, media links and the installed-package snapshot."""

from pathlib import Path
from zipfile import ZipFile
import hashlib
import json
import re
import shutil
import subprocess
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from scripts.final_submission import REQUIRED, REPOSITORY_ROOT  # noqa: E402


def verify() -> dict:
    root = REPOSITORY_ROOT
    missing = sorted(name for name in REQUIRED if not (root / name).is_file())
    readme = (root / "README.md").read_text(encoding="utf-8")
    media = re.findall(r'(?:src|poster|href)="([^\"]+)"', readme)
    missing_media = [name for name in media if not name.startswith(("https:", "http:", "#")) and not (root / name).exists()]
    packages = root / "demo/packages"
    package_files = sorted(packages.glob("*.deb"))
    hash_failures = []
    expected_names = set()
    sums = packages / "SHA256SUMS.txt"
    if sums.exists():
        for line in sums.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            digest, name = line.split(maxsplit=1)
            name = name.lstrip("*")
            expected_names.add(name)
            file = packages / name
            if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != digest:
                hash_failures.append(name)
    else:
        hash_failures.append("SHA256SUMS.txt missing")
    if expected_names != {file.name for file in package_files}:
        hash_failures.append("checksum inventory differs from Debian package set")
    for file in package_files:
        if file.read_bytes()[:8] != b"!<arch>\n":
            hash_failures.append(file.name + " invalid archive")
    video = root / "presentation/决赛演示视频.mp4"
    video_bytes = video.stat().st_size if video.exists() else 0
    duration = None
    probe = shutil.which("ffprobe")
    if probe and video.exists():
        raw = subprocess.check_output([probe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)], text=True)
        duration = float(json.loads(raw)["format"]["duration"])
    invalid_documents = []
    for name in REQUIRED:
        path = root / name
        if path.is_file() and path.suffix == ".pdf" and path.read_bytes()[:5] != b"%PDF-":
            invalid_documents.append(name)
    pptx = root / "presentation/决赛现场演示PPT.pptx"
    if pptx.exists():
        with ZipFile(pptx) as archive:
            if "ppt/presentation.xml" not in archive.namelist() or archive.testzip() is not None:
                invalid_documents.append(pptx.name)
    return {
        "ok": not missing and not missing_media and not hash_failures and not invalid_documents and len(package_files) == 5 and 0 < video_bytes < 100_000_000 and (duration is None or duration <= 300),
        "missing_required": missing, "missing_readme_media": missing_media,
        "debian_package_count": len(package_files), "package_hash_failures": hash_failures,
        "invalid_documents": invalid_documents, "video_bytes": video_bytes, "video_duration_seconds": duration,
        "video_duration_checked": duration is not None,
    }


def main() -> int:
    result = verify()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
