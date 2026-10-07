from __future__ import annotations

from argparse import ArgumentParser
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import shutil
import subprocess


def create_release_attestation(artifact: Path, output_dir: Path) -> dict[str, object]:
    artifact = Path(artifact)
    output_dir = Path(output_dir)
    if not artifact.exists():
        raise FileNotFoundError("artifact not found: %s" % artifact)
    output_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    signature_status = "not_configured"
    signature_file = None
    signer = None

    if shutil.which("minisign"):
        signature_path = output_dir / (artifact.name + ".minisig")
        try:
            subprocess.run(
                ["minisign", "-S", "-m", str(artifact), "-x", str(signature_path)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            signature_status = "signed"
            signature_file = str(signature_path)
            signer = "minisign"
        except subprocess.CalledProcessError:
            signature_status = "not_configured"
    elif shutil.which("gpg"):
        signature_path = output_dir / (artifact.name + ".asc")
        try:
            subprocess.run(
                ["gpg", "--batch", "--yes", "--armor", "--detach-sign", "--output", str(signature_path), str(artifact)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            signature_status = "signed"
            signature_file = str(signature_path)
            signer = "gpg"
        except subprocess.CalledProcessError:
            signature_status = "not_configured"

    sha_file = output_dir / "SHA256SUMS.txt"
    sha_file.write_text("%s  %s\n" % (digest, artifact.name), encoding="utf-8")
    attestation = {
        "schema": "memguardos.release_attestation.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifact": str(artifact),
        "artifact_name": artifact.name,
        "bytes": artifact.stat().st_size,
        "sha256": digest,
        "signature_status": signature_status,
        "signature_file": signature_file,
        "signer": signer,
        "sha256sums": str(sha_file),
        "note": (
            "Cryptographic signature is present and should be verified before release."
            if signature_status == "signed"
            else "No local signing key/tool was configured; this attestation intentionally provides checksum only and does not claim cryptographic signature."
        ),
    }
    (output_dir / "release_attestation.json").write_text(
        json.dumps(attestation, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return attestation


def main() -> int:
    parser = ArgumentParser(description="Create MemGuardOS release checksum and optional cryptographic signature.")
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(create_release_attestation(args.artifact, args.output_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
