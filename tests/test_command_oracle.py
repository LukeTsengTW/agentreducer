import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from agentreducer import OracleResult


def make_oracle(tmp_path: Path, script: str, *, args=(), timeout=5.0, env=None, policy=None):
    from agentreducer.execution import CommandOracle, CommandSpec, TextFileMaterializer, WorkspaceTemplate

    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    (source / "state.txt").write_bytes(b"original")
    paths = []
    text = TextFileMaterializer("candidate.txt", str)

    class RecordingMaterializer:
        def materialize(self, candidate, workspace):
            paths.append(workspace)
            text.materialize(candidate, workspace)

    kwargs = {} if policy is None else {"exit_code_policy": policy}
    oracle = CommandOracle(
        WorkspaceTemplate(source),
        CommandSpec((sys.executable, "-c", script, *args), timeout, env),
        RecordingMaterializer(), **kwargs,
    )
    return oracle, source, paths


@pytest.mark.parametrize("code,outcome", [(0, OracleResult.PASS), (1, OracleResult.FAIL), (7, OracleResult.FAIL)])
def test_default_command_outcomes_and_diagnostics(tmp_path: Path, capsys, code: int, outcome: OracleResult) -> None:
    oracle, source, paths = make_oracle(tmp_path, f"import sys; print('checked'); print('diagnostic', file=sys.stderr); sys.exit({code})")
    evaluation = oracle.evaluate_detailed("candidate")
    assert evaluation.outcome is outcome
    assert evaluation.error is None
    assert evaluation.command.exit_code == code
    assert evaluation.command.stdout == "checked\n"
    assert evaluation.command.stderr == "diagnostic\n"
    assert evaluation.command.argv[0] == sys.executable
    assert evaluation.command.duration_seconds >= 0
    assert evaluation.command.timed_out is False
    assert (source / "state.txt").read_bytes() == b"original"
    assert not (source / "candidate.txt").exists()
    assert len(paths) == 1 and not paths[0].exists()
    assert capsys.readouterr() == ("", "")


def test_cwd_argv_and_child_environment_are_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTREDUCER_M3_PARENT", "parent value")
    monkeypatch.delenv("AGENTREDUCER_M3_NEW", raising=False)
    script = "import json,os,sys; from pathlib import Path; print(json.dumps([os.getcwd(),sys.argv[1],os.environ['AGENTREDUCER_M3_PARENT'],os.environ['AGENTREDUCER_M3_NEW'],Path('candidate.txt').read_text()]))"
    literal = "argument with spaces & ; $(literal)"
    oracle, source, paths = make_oracle(tmp_path, script, args=(literal,), env={"AGENTREDUCER_M3_PARENT": "child value", "AGENTREDUCER_M3_NEW": "new value"})
    evaluation = oracle.evaluate_detailed("candidate")
    assert evaluation.outcome is OracleResult.PASS
    assert json.loads(evaluation.command.stdout) == [str(paths[0]), literal, "child value", "new value", "candidate"]
    assert os.environ["AGENTREDUCER_M3_PARENT"] == "parent value"
    assert "AGENTREDUCER_M3_NEW" not in os.environ
    assert paths[0] != source and not paths[0].exists()


@pytest.mark.parametrize("method", ["evaluate", "evaluate_detailed"])
def test_each_public_evaluation_launches_exactly_once(tmp_path: Path, method: str) -> None:
    counter = tmp_path / "launches.txt"
    script = "from pathlib import Path; import sys; p=Path(sys.argv[1]); n=int(p.read_text()) if p.exists() else 0; p.write_text(str(n+1))"
    oracle, _, paths = make_oracle(tmp_path, script, args=(str(counter),))
    result = getattr(oracle, method)("candidate")
    outcome = result if method == "evaluate" else result.outcome
    assert outcome is OracleResult.PASS
    assert counter.read_text() == "1"
    assert len(paths) == 1


def test_repeated_commands_receive_pristine_workspace(tmp_path: Path) -> None:
    script = "from pathlib import Path; p=Path('state.txt'); print(p.read_text()); p.write_text('changed'); Path('extra.txt').write_text('new')"
    oracle, source, paths = make_oracle(tmp_path, script)
    assert oracle.evaluate_detailed("first").command.stdout == "original\n"
    assert oracle.evaluate_detailed("second").command.stdout == "original\n"
    assert len(set(paths)) == 2
    assert all(not path.exists() for path in paths)
    assert sorted(p.name for p in source.iterdir()) == ["state.txt"]
    assert (source / "state.txt").read_bytes() == b"original"


def test_missing_executable_is_error_and_workspace_is_cleaned(tmp_path: Path) -> None:
    from agentreducer.execution import CommandOracle, CommandSpec

    oracle, _, paths = make_oracle(tmp_path, "")
    oracle = CommandOracle(oracle.workspace, CommandSpec((str(tmp_path / "missing-executable"),)), oracle.materializer)
    result = oracle.evaluate_detailed("candidate")
    assert result.outcome is OracleResult.ERROR
    assert result.command is None
    assert "FileNotFoundError" in result.error
    assert len(paths) == 1 and not paths[0].exists()


def test_timeout_preserves_partial_output_and_reaps_direct_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    processes = []
    original_popen = subprocess.Popen

    def recording_popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    oracle, _, paths = make_oracle(tmp_path, "import sys,time; print('partial out', flush=True); print('partial err', file=sys.stderr, flush=True); time.sleep(30)", timeout=0.5)
    result = oracle.evaluate_detailed("candidate")
    assert result.outcome is OracleResult.ERROR
    assert result.command.timed_out is True
    assert result.command.exit_code is None
    assert result.command.stdout == "partial out\n"
    assert result.command.stderr == "partial err\n"
    assert "timeout" in result.error.lower()
    assert len(processes) == 1 and processes[0].poll() is not None
    assert len(paths) == 1 and not paths[0].exists()


def test_timeout_without_output_has_string_diagnostics(tmp_path: Path) -> None:
    oracle, _, paths = make_oracle(tmp_path, "import time; time.sleep(30)", timeout=0.1)
    result = oracle.evaluate_detailed("candidate")
    assert result.outcome is OracleResult.ERROR
    assert result.command.stdout == result.command.stderr == ""
    assert not paths[0].exists()


@pytest.mark.parametrize("code,outcome", [(2, OracleResult.PASS), (3, OracleResult.FAIL), (4, OracleResult.ERROR)])
def test_custom_exit_policy_classifies_real_process(tmp_path: Path, code: int, outcome: OracleResult) -> None:
    from agentreducer.execution import ExitCodePolicy

    oracle, _, _ = make_oracle(tmp_path, f"import sys; sys.exit({code})", policy=ExitCodePolicy({2}, {3}))
    result = oracle.evaluate_detailed("candidate")
    assert result.outcome is outcome
    assert result.command.exit_code == code


def test_materializer_failure_does_not_launch_command(tmp_path: Path) -> None:
    from agentreducer.execution import CommandOracle

    counter = tmp_path / "should-not-exist"
    oracle, source, paths = make_oracle(tmp_path, "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", args=(str(counter),))

    class FailedMaterializer:
        def materialize(self, candidate, workspace):
            paths.append(workspace)
            raise RuntimeError("materialization unavailable")

    result = CommandOracle(oracle.workspace, oracle.command, FailedMaterializer()).evaluate_detailed("candidate")
    assert result.outcome is OracleResult.ERROR
    assert result.command is None
    assert "materialization unavailable" in result.error
    assert not counter.exists()
    assert len(paths) == 1 and not paths[0].exists()
    assert (source / "state.txt").read_bytes() == b"original"


def test_invalid_or_removed_source_is_error(tmp_path: Path) -> None:
    oracle, source, paths = make_oracle(tmp_path, "")
    (source / "state.txt").unlink()
    source.rmdir()
    result = oracle.evaluate_detailed("candidate")
    assert result.outcome is OracleResult.ERROR
    assert result.command is None
    assert "FileNotFoundError" in result.error
    assert paths == []


def test_workspace_copy_error_is_not_a_test_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oracle, _, paths = make_oracle(tmp_path, "")

    def failed_copy(*args, **kwargs):
        raise OSError("copy unavailable")

    monkeypatch.setattr(shutil, "copytree", failed_copy)
    result = oracle.evaluate_detailed("candidate")
    assert result.outcome is OracleResult.ERROR
    assert result.command is None and paths == []
    assert "copy unavailable" in result.error


@pytest.mark.parametrize("invalid_result", [False, True])
def test_policy_errors_retain_completed_command(tmp_path: Path, invalid_result: bool) -> None:
    class BrokenPolicy:
        def classify(self, code):
            if invalid_result:
                return "pass"
            raise RuntimeError("policy unavailable")

    oracle, _, paths = make_oracle(tmp_path, "print('collected')", policy=BrokenPolicy())
    result = oracle.evaluate_detailed("candidate")
    assert result.outcome is OracleResult.ERROR
    assert result.command.exit_code == 0
    assert result.command.stdout == "collected\n"
    assert result.error
    assert len(paths) == 1 and not paths[0].exists()


def test_cleanup_failure_overrides_success_and_retains_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_cleanup = tempfile.TemporaryDirectory.cleanup

    def cleanup_then_error(temporary):
        original_cleanup(temporary)
        raise OSError("cleanup unavailable")

    monkeypatch.setattr(tempfile.TemporaryDirectory, "cleanup", cleanup_then_error)
    oracle, _, paths = make_oracle(tmp_path, "print('collected')")
    result = oracle.evaluate_detailed("candidate")
    assert result.outcome is OracleResult.ERROR
    assert result.command.stdout == "collected\n"
    assert "cleanup unavailable" in result.error
    assert not paths[0].exists()


@pytest.mark.parametrize("cancellation_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("cleanup_fails", [False, True])
@pytest.mark.parametrize("method", ["evaluate", "evaluate_detailed"])
def test_cancellation_propagates_even_when_cleanup_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancellation_type, cleanup_fails: bool, method: str) -> None:
    from agentreducer.execution import CommandOracle

    original_cleanup = tempfile.TemporaryDirectory.cleanup
    if cleanup_fails:
        def cleanup_then_error(temporary):
            original_cleanup(temporary)
            raise OSError("cleanup unavailable")

        monkeypatch.setattr(tempfile.TemporaryDirectory, "cleanup", cleanup_then_error)

    counter = tmp_path / "should-not-launch"
    oracle, source, paths = make_oracle(tmp_path, "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", args=(str(counter),))
    cancellation = cancellation_type("trial cancelled")

    class CancelledMaterializer:
        def materialize(self, candidate, workspace):
            paths.append(workspace)
            raise cancellation

    cancelled_oracle = CommandOracle(oracle.workspace, oracle.command, CancelledMaterializer())
    with pytest.raises(cancellation_type) as caught:
        getattr(cancelled_oracle, method)("candidate")
    assert caught.value is cancellation
    assert len(paths) == 1 and not paths[0].exists()
    assert not counter.exists()
    assert (source / "state.txt").read_bytes() == b"original"
    if cleanup_fails:
        assert any("OSError: cleanup unavailable" in note for note in cancellation.__notes__)
    else:
        assert not getattr(cancellation, "__notes__", [])


def test_output_decoding_preserves_diagnostics_for_invalid_utf8(tmp_path: Path) -> None:
    oracle, _, _ = make_oracle(tmp_path, "import os; os.write(1, b'\\xff'); os.write(2,b'\\xfe')")
    result = oracle.evaluate_detailed("candidate")
    assert result.outcome is OracleResult.PASS
    assert result.command.stdout == result.command.stderr == "\ufffd"


def test_low_level_runner_returns_nonzero_without_oracle_classification(tmp_path: Path) -> None:
    from agentreducer.execution import CommandSpec, run_command

    result = run_command(CommandSpec((sys.executable, "-c", "import sys; sys.exit(5)")), cwd=tmp_path)
    assert result.exit_code == 5
    assert result.timed_out is False


@pytest.mark.skipif(os.name != "nt", reason="Windows OS batch execution rule")
@pytest.mark.parametrize("executable", ["untrusted.bat", "untrusted.CMD"])
def test_windows_batch_commands_are_rejected_without_shell_fallback(tmp_path: Path, executable: str) -> None:
    from agentreducer.execution import CommandOracle, CommandSpec

    oracle, _, paths = make_oracle(tmp_path, "")
    result = CommandOracle(oracle.workspace, CommandSpec((executable,)), oracle.materializer).evaluate_detailed("candidate")
    assert result.outcome is OracleResult.ERROR
    assert result.command is None
    assert "batch" in result.error.lower()
    assert not paths[0].exists()
