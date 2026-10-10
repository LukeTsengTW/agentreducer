import json
from pathlib import Path
import subprocess
import sys

import pytest
from cli_support import ROOT, safe_environment

FIELDS = {"status", "qualification", "oracle_outcome", "agent_stage", "error_stage",
          "agent_command_result_present", "agent_exit_code", "agent_timed_out",
          "verifier_command_result_present", "verifier_exit_code", "fixture_changed_bytes",
          "fixture_correct_behavior", "source_unchanged", "workspace_cleaned",
          "agent_task_processes", "verifier_processes", "backend_model_requests"}
FAKE = ROOT / "tests/fixtures/fake_smoke_agent.py"
SMOKE = ROOT / "smoke/codex_smoke.py"


def invoke(tmp_path, case, *, live=True, executable=None, extra=()):
    env = safe_environment(tmp_path)
    env["PATH"] = str(Path(sys.executable).parent)
    events = tmp_path / "events.jsonl"
    argv = [sys.executable, str(SMOKE), "--context-mode", "ambient", "--model", "gpt-6-luna",
            "--executable", str(executable or sys.executable), "--launcher-arg", str(FAKE),
            "--launcher-arg", case, "--launcher-arg", str(events), *extra]
    if live:
        argv.append("--live")
    completed = subprocess.run(argv, cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
                               capture_output=True, shell=False, timeout=30)
    assert b"M7_OBS_PRIVATE_CANARY" not in completed.stdout + completed.stderr
    assert str(tmp_path).encode() not in completed.stdout + completed.stderr
    report = json.loads(completed.stdout)
    assert set(report) == FIELDS
    assert report["backend_model_requests"] is None
    for value in report.values():
        if isinstance(value, str):
            assert str(tmp_path) not in value and "M7_OBS_PRIVATE_CANARY" not in value
    for name in ("agent_command_result_present", "agent_timed_out", "verifier_command_result_present",
                 "fixture_changed_bytes", "fixture_correct_behavior", "source_unchanged", "workspace_cleaned"):
        assert report[name] is None or type(report[name]) is bool
    for name in ("agent_task_processes", "verifier_processes"):
        assert report[name] is None or type(report[name]) is int and report[name] in (0, 1)
    assert report["agent_exit_code"] is None or type(report["agent_exit_code"]) is int
    assert report["agent_timed_out"] is None or type(report["agent_timed_out"]) is bool
    records = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()] if events.exists() else []
    return completed.returncode, report, records


@pytest.mark.parametrize("case", ["correct", "correct-format", "no-op", "agent-error", "wrong-file", "wrong-content"])
def test_actual_smoke_scalar_results_and_privacy(tmp_path, case):
    code, report, records = invoke(tmp_path, case)
    assert sum(record["phase"] == "fake-preflight" for record in records) == 3
    assert sum(record["phase"] == "fake-agent" for record in records) == 1
    assert report["agent_task_processes"] == 1
    assert report["agent_command_result_present"] is True
    assert report["source_unchanged"] is report["workspace_cleaned"] is True
    assert report["agent_timed_out"] is False
    if case.startswith("correct"):
        assert code == 0 and report["status"] == "verified" and report["oracle_outcome"] == "PASS"
        assert report["agent_stage"] == "completed" and report["error_stage"] is None
        assert report["agent_exit_code"] == report["verifier_exit_code"] == 0
        assert report["fixture_changed_bytes"] is report["fixture_correct_behavior"] is True
    elif case == "agent-error":
        assert code == 1 and report["oracle_outcome"] == "ERROR"
        assert report["agent_stage"] == "error" and report["error_stage"] == "agent"
        assert report["agent_exit_code"] == 23
        assert report["verifier_command_result_present"] is False
        assert report["verifier_processes"] == 0 and report["verifier_exit_code"] is None
        assert report["fixture_changed_bytes"] is report["fixture_correct_behavior"] is None
    else:
        assert code == 1 and report["status"] == "failed" and report["oracle_outcome"] == "FAIL"
        assert report["agent_stage"] == "completed" and report["agent_exit_code"] == 0
        assert report["verifier_command_result_present"] is True and report["verifier_processes"] == 1
        assert report["verifier_exit_code"] == 1 and report["fixture_correct_behavior"] is False
        assert report["fixture_changed_bytes"] is (case == "wrong-content")


def test_preflight_failure_omits_raw_diagnostics(tmp_path):
    code, report, records = invoke(tmp_path, "preflight-error")
    assert code == 2 and report["status"] == "preflight-failed"
    assert report["agent_task_processes"] == report["verifier_processes"] == 0
    assert all(record["phase"] == "fake-preflight" for record in records)


def test_opt_in_guard_precedes_any_child(tmp_path):
    code, report, records = invoke(tmp_path, "correct", live=False)
    assert code == 2 and report["status"] == "invalid-configuration" and not records
    assert report["agent_task_processes"] == report["verifier_processes"] == 0


def test_preparation_failure_omits_exception_and_path(tmp_path):
    code, report, records = invoke(tmp_path, "correct", executable=tmp_path / "M7_OBS_PRIVATE_CANARY.exe")
    assert code == 2 and report["status"] == "preparation-failed" and not records
    assert report["agent_stage"] == "not-started"


def test_argument_failure_omits_raw_arguments(tmp_path):
    code, report, records = invoke(tmp_path, "correct", extra=("--M7_OBS_PRIVATE_CANARY",))
    assert code == 2 and report["status"] == "invalid-configuration" and not records


def test_controlled_guard_launches_no_child(tmp_path):
    code, report, records = invoke(tmp_path, "correct", extra=("--context-mode", "controlled"))
    assert code == 2 and report["status"] == "unavailable" and not records
    assert report["agent_task_processes"] == report["verifier_processes"] == 0
