from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="构建并隔离安装 MemGuardOS Python 发布包")
    parser.add_argument("--keep", action="store_true", help="保留临时目录用于排查")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 10):
        raise SystemExit("MemGuardOS requires Python 3.10+")

    if args.keep:
        work = Path(tempfile.mkdtemp(prefix="memguardos-dist-smoke-"))
        report = run_smoke(work)
        report["work_dir"] = str(work)
    else:
        with tempfile.TemporaryDirectory(prefix="memguardos-dist-smoke-") as raw:
            report = run_smoke(Path(raw))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


def run_smoke(work: Path) -> dict[str, object]:
    dist = work / "dist"
    environment = work / "venv"
    home = work / "home"
    dist.mkdir(parents=True)
    home.mkdir(parents=True)
    build = _run([sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", str(dist)], ROOT)
    if build.returncode != 0:
        return _report(False, build=build, reason="build_failed")

    wheels = sorted(dist.glob("memguardos-*.whl"))
    sdists = sorted(dist.glob("memguardos-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        return {"ok": False, "reason": "unexpected_artifact_set", "wheels": len(wheels), "sdists": len(sdists)}

    venv.EnvBuilder(with_pip=True, clear=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    install = _run([str(python), "-m", "pip", "install", "--no-deps", str(wheels[0])], work)
    if install.returncode != 0:
        return _report(False, install=install, reason="install_failed")

    probe_code = """
import json
from importlib import metadata, resources
from memguard import __version__

aliases = json.loads(resources.files('memguard.resources').joinpath('technical_aliases.json').read_text(encoding='utf-8'))
assert metadata.version('memguardos') == __version__
assert any('ripgrep' in values for values in aliases.values())
print(json.dumps({'version': __version__, 'resource_aliases': len(aliases)}, ensure_ascii=False))
""".strip()
    probe_env = {
        **os.environ,
        "MEMGUARD_HOME": str(home),
        "HOME": str(home),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    probe = _run([str(python), "-c", probe_code], work, env=probe_env)
    cli = _run([str(python), "-m", "memguard.cli.memguard", "status"], work, env=probe_env)
    hook = _run([str(python), "-m", "memguard.integrations.openhands_memory_hook", "--help"], work, env=probe_env)
    ok = all(result.returncode == 0 for result in (probe, cli, hook))
    return {
        "ok": ok,
        "wheel": wheels[0].name,
        "sdist": sdists[0].name,
        "probe": _result(probe),
        "cli": _result(cli),
        "openhands_hook": _result(hook),
    }


def _run(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", check=False)


def _result(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return {"returncode": completed.returncode, "output": completed.stdout[-2000:]}


def _report(ok: bool, *, reason: str, **results: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return {"ok": ok, "reason": reason, **{name: _result(value) for name, value in results.items()}}


if __name__ == "__main__":
    raise SystemExit(main())
