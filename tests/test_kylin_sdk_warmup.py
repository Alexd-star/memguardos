"""Run the embedded warmup coordinator with a fake clock, SDK and HTTP server."""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "warmup_kylin_sdk.sh"
HELPER = SCRIPT.read_text(encoding="utf-8").split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


def report(ok=False, ready=False, error="GTE model loading"):
    return {"embedding": {"available": True, "inference_ready": ready,
                          "model_info": {"name": "GTE", "dim": 768},
                          "embedding_probe": {"ok": ok, "dim": 768, "error": error},
                          "fallback": "hash_embedding"}}


@pytest.fixture
def simulate(tmp_path, monkeypatch, capsys):
    state_path = tmp_path / "memguardos" / "kylin-sdk-warmup.json"
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("MEMGUARD_KYLIN_RUNTIME_HARDENED", "true")
    monkeypatch.delenv("MEMGUARD_KYLIN_WARMUP_PROBE_TIMEOUT", raising=False)
    monkeypatch.delenv("MEMGUARD_KYLIN_WARMUP_RETRY_INTERVAL", raising=False)

    def run(outcomes, budget="120", http=True, **settings):
        for key, value in settings.items():
            monkeypatch.setenv(key, str(value))
        clock = [0.0]
        processes, kills, http_timeouts, waits = [], [], [], []
        initial_states = []
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        monkeypatch.setattr(time, "sleep", lambda duration: clock.__setitem__(0, clock[0] + duration))
        monkeypatch.setattr(signal, "signal", lambda *args: None)
        monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
        monkeypatch.setattr(sys, "argv", ["-", budget, "fake-memctl"])

        class Response:
            status = 200 if http else 503

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class Opener:
            def open(self, request, timeout):
                assert request.full_url == "http://127.0.0.1:8000/v2/health/ready"
                assert 0 < timeout <= 0.25
                http_timeouts.append(timeout)
                if http is None:
                    raise OSError("Triton local HTTP unavailable")
                return Response()

        monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Opener())

        class Process:
            def __init__(self, command, **kwargs):
                assert kwargs == {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                                  "text": True, "start_new_session": True}
                initial_states.append(json.loads(state_path.read_text(encoding="utf-8")))
                self.pid = 7000 + len(processes)
                self.returncode = None
                self.command = command
                self.calls = 0
                self.children_alive = True
                self.outcome = (outcomes(clock[0], len(processes)) if callable(outcomes)
                                else outcomes[min(len(processes), len(outcomes) - 1)])
                clock[0] += self.outcome.get("spawn_duration", 0)
                processes.append(self)

            def communicate(self, timeout):
                self.calls += 1
                waits.append(timeout)
                stdout = self.outcome.get("stdout", json.dumps(self.outcome.get("report", report())))
                stderr = self.outcome.get("stderr", "")
                if self.calls > 1:
                    assert self.returncode is not None and not self.children_alive
                    return stdout, stderr
                if self.outcome.get("interrupt"):
                    raise KeyboardInterrupt("warmup interrupted")
                duration = self.outcome.get("duration", 0.1)
                if duration > timeout:
                    clock[0] += timeout
                    raise subprocess.TimeoutExpired(self.command, timeout, output=stdout, stderr=stderr)
                clock[0] += duration
                self.returncode = self.outcome.get("returncode", 0)
                self.children_alive = False
                return stdout, stderr

            def poll(self):
                return self.returncode

            def wait(self, timeout):
                if self.returncode is None:
                    clock[0] += timeout
                    raise subprocess.TimeoutExpired(self.command, timeout)
                return self.returncode

        def killpg(pid, sig):
            assert pid in {proc.pid for proc in processes}
            kills.append((pid, sig))
            proc = next(proc for proc in processes if proc.pid == pid)
            if sig != signal.SIGTERM or not proc.outcome.get("ignore_term"):
                proc.returncode = -int(sig)
            # Simulate an embedding child ignoring TERM even after its CLI parent exits.
            if sig == signal.SIGKILL:
                proc.children_alive = False

        monkeypatch.setattr(subprocess, "Popen", Process)
        monkeypatch.setattr(os, "killpg", killpg, raising=False)
        exec(compile(HELPER, str(SCRIPT), "exec"), {})
        result = json.loads(state_path.read_text(encoding="utf-8"))
        assert json.loads(capsys.readouterr().out) == result
        assert result["runtime_directory_hardened"] is True
        return result, processes, kills, clock[0], initial_states

    return run


def test_delayed_gte_ready_after_93_seconds_is_retried_within_total_budget(simulate):
    def delayed(now, index):
        ready = now + 15 >= 93
        return {"duration": 15, "report": report(ready, ready, None if ready else f"GTE loading {index}")}

    result, processes, kills, elapsed, states = simulate(delayed)
    assert result["ok"] is True and result["status"] == "ready" and not result["degraded"]
    assert 93 <= elapsed < 120 < 150
    assert len(processes) == 6 and not kills
    assert [item["embedding_probe"]["error"] for item in result["attempts"][:-1]] == [
        f"GTE loading {index}" for index in range(5)]
    assert all(state["ok"] is False for state in states)
    assert all(float(proc.command[-1]) <= 15 for proc in processes)


def test_real_probe_success_does_not_require_triton_http(simulate):
    result, processes, kills, _, _ = simulate([{"report": report(True, True, None)}], http=None)
    assert result["ok"] and result["inference_ready"] and result["dim"] == 768
    assert result["attempts"][0]["triton_http_ready"] is None
    assert "unavailable" in result["attempts"][0]["triton_http_error"]
    assert len(processes) == 1 and not kills


@pytest.mark.parametrize("sdk,returncode", [
    (report(False, False, "Timeout was reached"), 0),
    (report(False, True, "embedding error"), 0),
    (report(True, False, None), 0),
    (report(True, True, None), 3),
    ({"embedding": {"available": True, "inference_ready": True, "embedding_probe": None}}, 0),
])
def test_cli_exit_and_http_ready_are_not_inference_success(simulate, sdk, returncode):
    result, processes, kills, elapsed, _ = simulate(
        [{"report": sdk, "returncode": returncode}], budget="4",
        MEMGUARD_KYLIN_WARMUP_RETRY_INTERVAL="1")
    assert not result["ok"] and not result["inference_ready"] and result["degraded"]
    assert result["status"] == "degraded" and result["deadline_exhausted"]
    assert all(not attempt["ok"] for attempt in result["attempts"])
    assert all(attempt["triton_http_ready"] for attempt in result["attempts"])
    assert result["attempts"][0]["embedding_probe"] == sdk["embedding"]["embedding_probe"]
    assert processes and not kills and elapsed <= 4


def test_each_error_and_stderr_survives_later_success(simulate):
    result, _, _, _, _ = simulate([
        {"report": report(False, False, "first model error"), "stderr": "warning one"},
        {"report": report(False, False, "second model error"), "stderr": "warning two"},
        {"report": report(True, True, None)},
    ])
    assert result["ok"] and result["error"] is None
    assert result["last_error"] == "second model error"
    assert [attempt["stderr"] for attempt in result["attempts"]] == ["warning one", "warning two", ""]
    assert [attempt["error"] for attempt in result["attempts"]] == [
        "first model error", "second model error", None]


def test_malformed_cli_output_preserved_and_retried(simulate):
    result, _, _, _, _ = simulate([
        {"stdout": "not JSON", "stderr": "real CLI failure", "returncode": 1},
        {"stdout": "startup log\n" + json.dumps(report(True, True, None)) + "\ntrailing log"},
    ])
    assert result["ok"] and result["attempt_count"] == 2
    first = result["attempts"][0]
    assert first["stdout"] == "not JSON" and first["stderr"] == "real CLI failure"
    assert first["returncode"] == 1 and "status object" in first["error"]


def test_overall_timeout_cleans_only_created_groups_including_embedding_children(simulate):
    result, processes, kills, elapsed, _ = simulate(
        [{"duration": 1000, "stdout": "partial SDK diagnostic", "stderr": "embedding child hung"}],
        budget="5")
    assert not result["ok"] and result["degraded"] and result["deadline_exhausted"]
    assert elapsed <= 5 and len(processes) == 1
    assert kills == [(processes[0].pid, signal.SIGTERM), (processes[0].pid, signal.SIGKILL)]
    assert not processes[0].children_alive
    attempt = result["attempts"][0]
    assert attempt["timed_out"] and "overall timeout" in attempt["error"]
    assert attempt["stdout"] == "partial SDK diagnostic" and attempt["stderr"] == "embedding child hung"
    assert float(processes[0].command[-1]) < 5


def test_timeout_can_retry_and_then_succeed_without_resetting_deadline(simulate):
    result, processes, kills, elapsed, _ = simulate([
        {"duration": 1000, "stderr": "hung first attempt"},
        {"report": report(True, True, None)},
    ], budget="40", MEMGUARD_KYLIN_WARMUP_PROBE_TIMEOUT="5")
    assert result["ok"] and 15 <= elapsed < 40
    assert len(processes) == 2 and len(kills) == 2
    assert result["attempts"][0]["timed_out"]


def test_spawn_delay_and_term_resistant_cli_stay_inside_absolute_deadline(simulate):
    result, processes, kills, elapsed, _ = simulate(
        [{"duration": 1000, "spawn_duration": 1, "ignore_term": True}], budget="5")
    assert not result["ok"] and result["deadline_exhausted"]
    assert elapsed <= 5 and len(processes) == 1 and len(kills) == 2
    assert not processes[0].children_alive


def test_timeout_keeps_already_emitted_real_probe_failure(simulate):
    failure = report(False, False, "real embedding transport error")
    result, _, _, _, _ = simulate([{ "duration": 1000, "report": failure}], budget="5")
    assert not result["ok"] and result["attempts"][0]["timed_out"]
    assert result["embedding_probe"] == failure["embedding"]["embedding_probe"]
    assert result["attempts"][0]["embedding_probe"]["error"] == "real embedding transport error"


def test_previous_boot_ready_state_is_invalidated_before_first_probe(simulate, tmp_path):
    path = tmp_path / "memguardos" / "kylin-sdk-warmup.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"ok": True, "inference_ready": True}))
    _, _, _, _, states = simulate([{"report": report(True, True, None)}])
    assert states[0]["ok"] is False and states[0]["degraded"] is True


def test_interruption_cleans_owned_group_and_writes_degraded_state(simulate):
    result, processes, kills, _, _ = simulate([{"interrupt": True}])
    assert result["status"] == "degraded" and not result["ok"]
    assert len(processes) == 1 and len(kills) == 2 and not processes[0].children_alive
    assert "interrupted" in result["error"]


@pytest.mark.parametrize("budget", ["0", "-1", "nan", "inf", "invalid"])
def test_invalid_total_budget_is_degraded_without_spawning_cli(simulate, budget):
    result, processes, kills, _, _ = simulate([], budget=budget)
    assert not result["ok"] and result["status"] == "degraded" and result["error"]
    assert not processes and not kills


def test_small_budget_remains_bounded_without_starting_a_cli(simulate):
    result, processes, _, elapsed, _ = simulate([], budget="0.1")
    assert result["deadline_exhausted"] and not result["ok"] and not processes
    assert elapsed <= 0.1


def test_shell_syntax_default_budget_and_no_shared_runtime_mutation():
    bash = r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else shutil.which("bash")
    if not bash or not Path(bash).exists():
        pytest.skip("bash unavailable")
    result = subprocess.run([bash, "-n", SCRIPT.as_posix()], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    source = SCRIPT.read_text(encoding="utf-8")
    assert "MEMGUARD_KYLIN_WARMUP_TIMEOUT:-120" in source
    assert "start_new_session=True" in source
    assert 'stat -c %u "${RUNTIME_DIR}"' in source and 'chmod 0700 "${RUNTIME_DIR}"' in source
    assert all(command not in source for command in ("rm -rf", "pkill", "pgrep", "systemctl restart"))
