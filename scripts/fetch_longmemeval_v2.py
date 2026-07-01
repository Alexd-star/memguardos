#!/usr/bin/env python3
from pathlib import Path
import argparse
import hashlib
import json
import os
import urllib.request


BASE = "https://huggingface.co/datasets/xiaowu0162/longmemeval-v2/resolve/main"
API = "https://huggingface.co/api/datasets/xiaowu0162/longmemeval-v2"
CODE_REVISION = "be15ea6e995462f3391c1a610892df3f67dfa7bd"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def opener(proxy: str):
    resolved = proxy or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    return urllib.request.build_opener(urllib.request.ProxyHandler({"http": resolved, "https": resolved}) if resolved else urllib.request.ProxyHandler())


def download(client, relative: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    with client.open("%s/%s?download=true" % (BASE, relative), timeout=180) as response, temporary.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
    temporary.replace(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description="固定来源下载 LongMemEval-V2 官方数据")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--proxy", default="")
    parser.add_argument("--include-trajectories", action="store_true", help="下载约 1.2 GB 的轨迹 JSONL")
    args = parser.parse_args()
    root = Path(args.output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    client = opener(args.proxy)
    files = ["questions.jsonl", "haystacks/lme_v2_small.json", "checksums.sha256", "SCHEMA.md", "DATA_CARD.md", "LICENSE"]
    if args.include_trajectories:
        files.append("trajectories.jsonl")
    for relative in files:
        destination = root / relative
        if not destination.exists():
            download(client, relative, destination)
    metadata = json.loads(client.open(API, timeout=60).read().decode("utf-8"))
    manifest = {
        "dataset": "xiaowu0162/longmemeval-v2",
        "dataset_revision": metadata.get("sha"),
        "code_repository": "https://github.com/xiaowu0162/LongMemEval-V2",
        "code_revision": CODE_REVISION,
        "files": [
            {"path": str(path.relative_to(root)).replace("\\", "/"), "bytes": path.stat().st_size, "sha256": sha256(path)}
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.name != "manifest.json"
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
