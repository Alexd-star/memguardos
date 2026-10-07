"""Isolated shell tests: temporary models, mocked services, no SDK or VM calls."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


SCRIPT = (Path(__file__).resolve().parents[1] / "src") / "scripts" / "fix_kylin_embedding_runtime.sh"
BASH = (r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else shutil.which("bash"))
pytestmark = pytest.mark.skipif(not BASH or not Path(BASH).exists(), reason="bash unavailable")

DRIVER = r'''
id() { if [ "$1" = -u ]; then echo "${TEST_UID:-1000}"; else echo testuser; fi; }
sudo() { echo "sudo $*" >> "$CALLS"; shift; "$@"; }
systemctl() {
  echo "systemctl $*" >> "$CALLS"
  case "$*" in
    "--user cat "*) [ "${TEST_MANAGED:-1}" = 1 ] ;;
    "--user show "*) echo "${TEST_MAINPID:-123}" ;;
    *restart*) return "${TEST_RESTART_RC:-0}" ;;
    *) return 0 ;;
  esac
}
pgrep() { echo "pgrep $*" >> "$CALLS"; printf '%s' "${TEST_PIDS:-}"; }
sleep() { :; }
test() {
  if [ "${1:-}" = -S ]; then [ "${TEST_SOCKET:-1}" = 1 ]; else builtin test "$@"; fi
}
journalctl() { :; }
ls() { :; }
kill() { echo "FORBIDDEN kill $*" >> "$CALLS"; return 97; }
rm() { echo "FORBIDDEN rm $*" >> "$CALLS"; return 97; }
nohup() { echo "FORBIDDEN nohup $*" >> "$CALLS"; return 97; }
ln() { echo "FORBIDDEN ln $*" >> "$CALLS"; return 97; }
source "$0"
'''


@pytest.fixture
def sandbox(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / SCRIPT.name
    script.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    source, target, backup = (tmp_path / name for name in ("source", "target", "backup"))
    source.mkdir()
    target.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    calls = tmp_path / "calls.txt"
    env = dict(os.environ, PYTHON_BIN=Path(sys.executable).as_posix(),
               MEMGUARD_SOURCE_MODEL_REPO=source.as_posix(),
               MEMGUARD_KYTENSOR_MODEL_REPO=target.as_posix(),
               MEMGUARD_MODEL_REPO_BACKUP_ROOT=backup.as_posix(),
               HOME=home.as_posix(), CALLS=calls.as_posix(), PYTHONDONTWRITEBYTECODE="1")

    def run(*args, **settings):
        calls.write_text("", encoding="utf-8")
        result = subprocess.run([BASH, "-c", DRIVER, script.as_posix(), *args],
                                cwd=tmp_path, env=dict(env, **settings),
                                capture_output=True, text=True, timeout=20)
        log = calls.read_text(encoding="utf-8")
        assert "FORBIDDEN" not in log
        return result, log

    return source, target, backup, home, run


def model(root, name):
    directory = root / name
    directory.mkdir()
    (directory / "weights.bin").write_bytes(b"keep model weights")
    return directory


def link(destination, source):
    try:
        destination.symlink_to(source, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")


def test_default_is_read_only_and_does_not_populate_other_repository(sandbox):
    source, target, backup, home, run = sandbox
    model(source, "source_only")
    model(target, "target_only")
    result, log = run()
    assert result.returncode == 0, result.stderr
    assert "duplicate_models" in result.stdout
    assert "sudo" not in log and "restart" not in log
    assert {p.name for p in target.iterdir()} == {"target_only"}
    assert not backup.exists() and not list(home.iterdir())


def test_default_diagnoses_duplicate_without_touching_it(sandbox):
    source, target, backup, _, run = sandbox
    original = model(source, "embedding")
    link(target / "embedding", original)
    result, log = run()
    assert result.returncode == 0, result.stderr
    assert '"embedding"' in result.stdout
    assert (target / "embedding").is_symlink()
    assert not backup.exists() and "sudo" not in log


def test_explicit_repair_preserves_models_and_backs_up_only_duplicate_link(sandbox):
    source, target, backup, _, run = sandbox
    original = model(source, "embedding")
    model(source, "source_only")
    unique = model(target, "unique")
    link(target / "embedding", original)
    raw_target = os.readlink(target / "embedding")
    result, log = run("--fix-model-repo")
    assert result.returncode == 0, result.stderr
    assert "restart" not in log
    assert not (target / "embedding").exists()
    assert {p.name for p in target.iterdir()} == {"unique"}
    assert (original / "weights.bin").read_bytes() == b"keep model weights"
    assert (unique / "weights.bin").read_bytes() == b"keep model weights"
    receipt = next(backup.glob("*/restore.json"))
    items = json.loads(receipt.read_text())
    assert items[0]["moved"] is True
    assert items[0]["link_target"] == raw_target
    assert os.readlink(receipt.parent / "embedding") == raw_target
    again, _ = run("--fix-model-repo")
    assert again.returncode == 0 and len(list(backup.iterdir())) == 1


@pytest.mark.parametrize("different_link", [False, True])
def test_ambiguous_duplicate_refuses_entire_repair(sandbox, different_link):
    source, target, backup, _, run = sandbox
    original = model(source, "a_safe")
    link(target / "a_safe", original)
    model(source, "z_conflict")
    if different_link:
        other = model(backup.parent, "other")
        link(target / "z_conflict", other)
    else:
        model(target, "z_conflict")
    result, log = run("--fix-model-repo", "--restart-kytensor")
    assert result.returncode != 0 and "ambiguous duplicate" in result.stderr
    assert (target / "a_safe").is_symlink()
    assert (target / "z_conflict" / "weights.bin").exists()
    assert not backup.exists() and "systemctl restart" not in log


def test_backup_inside_active_repository_is_refused(sandbox):
    source, target, _, _, run = sandbox
    original = model(source, "embedding")
    link(target / "embedding", original)
    backup = target / "backups"
    result, _ = run("--fix-model-repo", MEMGUARD_MODEL_REPO_BACKUP_ROOT=backup.as_posix())
    assert result.returncode != 0 and "outside both" in result.stderr
    assert (target / "embedding").is_symlink() and not backup.exists()


def test_restart_kytensor_requires_unique_names(sandbox):
    source, target, _, _, run = sandbox
    model(source, "duplicate")
    model(target, "duplicate")
    result, log = run("--restart-kytensor")
    assert result.returncode != 0 and "duplicate model names" in result.stderr
    assert "systemctl restart" not in log


def test_restart_runtime_also_refuses_duplicate_names(sandbox):
    source, target, _, _, run = sandbox
    model(source, "duplicate")
    model(target, "duplicate")
    result, log = run("--restart-runtime")
    assert result.returncode != 0 and "duplicate model names" in result.stderr
    assert "--user restart" not in log


def test_explicit_restart_only_restarts_requested_service(sandbox):
    *_, run = sandbox
    result, log = run("--restart-kytensor")
    assert result.returncode == 0, result.stderr
    assert "systemctl restart kytensor.service" in log
    assert "--user restart" not in log


@pytest.mark.parametrize("settings,message", [
    ({"TEST_MANAGED": "0"}, "no managed user"),
    ({"TEST_UID": "0"}, "not root"),
    ({"TEST_PIDS": "123\n456"}, "multiple user runtimes"),
    ({"TEST_PIDS": "456"}, "multiple user runtimes"),
    ({"TEST_PIDS": "123", "TEST_MAINPID": "0"}, "multiple user runtimes"),
])
def test_unmanaged_ambiguous_or_root_runtime_refused(sandbox, settings, message):
    *_, home, run = sandbox
    result, log = run("--restart-runtime", **settings)
    assert result.returncode != 0 and message in result.stderr
    assert "--user restart" not in log and not list(home.iterdir())


def test_managed_runtime_restart_is_same_user_and_has_no_overrides(sandbox):
    *_, home, run = sandbox
    result, log = run("--restart-runtime", TEST_PIDS="123")
    assert result.returncode == 0, result.stderr
    assert "pgrep -u 1000 -f" in log
    assert "--user restart kylin-ai-runtime.service" in log
    assert "sudo" not in log and not list(home.iterdir())


@pytest.mark.parametrize("settings", [{"TEST_SOCKET": "0"}, {"TEST_RESTART_RC": "5"}])
def test_runtime_restart_failure_is_not_hidden_or_force_cleaned(sandbox, settings):
    *_, run = sandbox
    result, _ = run("--restart-runtime", **settings)
    assert result.returncode != 0


def test_legacy_opt_outs_and_timeout_do_not_enable_mutation(sandbox):
    *_, run = sandbox
    result, log = run("--fix-model-repo", "--no-model-repo-fix",
                      "--restart-runtime", "--no-restart-runtime",
                      "--restart-kytensor", "--no-restart-kytensor",
                      "--probe", "--no-probe", "--probe-timeout", "5")
    assert result.returncode == 0, result.stderr
    assert "sudo" not in log and "restart" not in log
    assert "embedding probe" not in result.stdout


@pytest.mark.parametrize("args", [("--probe-timeout",), ("--probe-timeout", "0"),
                                  ("--probe-timeout", "abc"), ("--unknown",)])
def test_bad_arguments_fail_before_actions(sandbox, args):
    *_, run = sandbox
    result, log = run(*args)
    assert result.returncode == 2 and not log


def test_help_and_shell_syntax(sandbox):
    *_, run = sandbox
    result, log = run("--help")
    assert result.returncode == 0 and "read-only diagnostics" in result.stdout and not log
    syntax = subprocess.run([BASH, "-n", SCRIPT.as_posix()], capture_output=True, text=True)
    assert syntax.returncode == 0, syntax.stderr
    text = SCRIPT.read_text()
    assert all(command not in text for command in ("rm -rf", "kill -9", "nohup", "ln -s"))


@pytest.mark.parametrize("scenario", ["alias", "conflict", "unsafe_backup"])
def test_repository_helper_with_simulated_link_primitives(tmp_path, monkeypatch, scenario):
    """Exercise the exact embedded helper even on Windows without symlink privilege."""
    source, target, backup = (tmp_path / name for name in ("source", "target", "backup"))
    source.mkdir()
    target.mkdir()
    original = model(source, "embedding")
    alias = target / "embedding"
    alias.mkdir()
    model(source, "source_only")
    unique = model(target, "unique")
    if scenario == "conflict":
        model(source, "z_conflict")
        model(target, "z_conflict")
    if scenario == "unsafe_backup":
        backup = target / "backup"
    real_is_symlink, real_samefile, real_readlink = Path.is_symlink, Path.samefile, os.readlink
    monkeypatch.setattr(Path, "is_symlink",
                        lambda path: path == alias or real_is_symlink(path))
    monkeypatch.setattr(Path, "samefile",
                        lambda path, other: real_samefile(original if path == alias else path, other))
    monkeypatch.setattr(os, "readlink",
                        lambda path, *args, **kwargs: str(original) if Path(path) == alias
                        else real_readlink(path, *args, **kwargs))
    monkeypatch.setattr(sys, "argv", ["-", "repair", str(source), str(target), str(backup)])
    helper = SCRIPT.read_text().split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    if scenario != "alias":
        with pytest.raises(SystemExit, match="ambiguous duplicate|outside both"):
            exec(compile(helper, str(SCRIPT), "exec"), {})
        assert alias.exists() and not backup.exists()
    else:
        exec(compile(helper, str(SCRIPT), "exec"), {})
        assert not alias.exists()
        assert {path.name for path in target.iterdir()} == {"unique"}
        receipt = next(backup.glob("*/restore.json"))
        items = json.loads(receipt.read_text())
        assert items == [{"name": "embedding", "original_path": str(alias),
                          "link_target": str(original), "retained_model": str(original.resolve()),
                          "moved": True}]
        assert (receipt.parent / "embedding").is_dir()
    assert (original / "weights.bin").read_bytes() == b"keep model weights"
    assert (unique / "weights.bin").read_bytes() == b"keep model weights"
