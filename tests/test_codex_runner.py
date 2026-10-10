import os
import json
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agentreducer.agents import CodexContextMode, CodexRunner, CodexSpec
from agentreducer.agents import codex

FAKE = Path(__file__).parent / "fixtures" / "fake_codex.py"


def fake_runner(tmp_path, *, mode=CodexContextMode.AMBIENT, timeout=5, env=None):
    environment = {
        "AGENTREDUCER_FAKE_COUNTER": str(tmp_path / "agents.jsonl"),
        "AGENTREDUCER_FAKE_PREFLIGHT_COUNTER": str(tmp_path / "probes.jsonl"),
    }
    environment.update(env or {})
    return CodexRunner(CodexSpec("fixture-model", (sys.executable, str(FAKE.resolve())), timeout, environment, mode))


def records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_spec_snapshots_configuration_and_hides_environment():
    env = {"CODEX_API_KEY": "dummy-secret"}
    prefix = [sys.executable, "fixture.py"]
    names = ["CUSTOM_TOKEN"]
    spec = CodexSpec("fixture-model", argv_prefix=prefix, env=env, sensitive_env_names=names)
    env.clear()
    prefix.clear()
    names.clear()
    assert spec.argv_prefix == (sys.executable, "fixture.py")
    assert spec.env["CODEX_API_KEY"] == "dummy-secret"
    assert spec.sensitive_env_names == frozenset({"CUSTOM_TOKEN"})
    assert "dummy-secret" not in repr(spec)
    assert spec.context_mode is CodexContextMode.CONTROLLED
    with pytest.raises(TypeError):
        spec.env["new"] = "value"
    with pytest.raises(FrozenInstanceError):
        spec.model = "changed"


@pytest.mark.parametrize("model", ["", " ", "two models", "x\0y", None, 1])
def test_invalid_model_fails_before_discovery(model, monkeypatch):
    monkeypatch.setattr(codex.shutil, "which", lambda *_: pytest.fail("discovery during config"))
    with pytest.raises((TypeError, ValueError)):
        CodexSpec(model)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, "5"])
def test_invalid_timeout_is_rejected(timeout):
    with pytest.raises((TypeError, ValueError)):
        CodexSpec("fixture", timeout_seconds=timeout)


@pytest.mark.parametrize("prefix", ["command args", [], [""], [1], ["x\0y"], [sys.executable, ""]])
def test_invalid_prefix_is_rejected(prefix):
    with pytest.raises((TypeError, ValueError)):
        CodexSpec("fixture", argv_prefix=prefix)


@pytest.mark.parametrize("env", [{"BAD=KEY": "x"}, {"": "x"}, {"TOKEN": "x\0y"}, {"KEY": 1}])
def test_invalid_environment_is_rejected(env):
    with pytest.raises((TypeError, ValueError)):
        CodexSpec("fixture", env=env)


def test_trusted_native_prefix_is_resolved_once_without_process(monkeypatch, tmp_path):
    monkeypatch.setattr(codex.shutil, "which", lambda *_: pytest.fail("unexpected PATH search"))
    runner = CodexRunner(CodexSpec("fixture", argv_prefix=(sys.executable, str(tmp_path / "fake.py"))))
    assert runner.invocation == (str(Path(sys.executable).resolve()), str(tmp_path / "fake.py"))
    assert runner.context.mode is CodexContextMode.CONTROLLED
    with pytest.raises(FrozenInstanceError):
        runner.spec = None


def test_default_windows_resolution_prefers_native_executable(monkeypatch):
    calls = []
    monkeypatch.setattr(codex, "_WINDOWS", True)
    monkeypatch.setattr(codex.shutil, "which", lambda name: calls.append(name) or sys.executable)
    runner = CodexRunner(CodexSpec("fixture", context_mode=CodexContextMode.AMBIENT))
    assert calls == ["codex.exe"]
    assert runner.context.mode is CodexContextMode.AMBIENT
    assert "conditional" in runner.context.qualification


def test_batch_only_install_reports_native_requirement(monkeypatch):
    monkeypatch.setattr(codex, "_WINDOWS", True)
    monkeypatch.setattr(codex.shutil, "which", lambda name: "C:/fake/codex.cmd" if name == "codex.cmd" else None)
    with pytest.raises(ValueError, match="native.*batch"):
        CodexRunner(CodexSpec("fixture"))


@pytest.mark.parametrize("filename", ["codex.CMD", "codex.BaT", "cmd.exe", "powershell.exe", "pwsh.exe", "bash", "sh", "wsl.exe"])
def test_batch_and_shell_launchers_are_rejected(filename, tmp_path):
    exe = tmp_path / filename
    exe.write_bytes(b"fixture")
    with pytest.raises(ValueError, match="batch|shell"):
        CodexRunner(CodexSpec("fixture", argv_prefix=(str(exe),)))


def test_missing_native_executable_is_configuration_error(monkeypatch, tmp_path):
    monkeypatch.setattr(codex.shutil, "which", lambda *_: None)
    with pytest.raises(FileNotFoundError):
        CodexRunner(CodexSpec("fixture"))
    with pytest.raises(FileNotFoundError):
        CodexRunner(CodexSpec("fixture", argv_prefix=(str(tmp_path / "missing.exe"),)))


def test_explicit_native_executable_requires_absolute_path():
    with pytest.raises(ValueError, match="absolute"):
        CodexRunner(CodexSpec("fixture", argv_prefix=("python.exe",)))


def test_known_value_redaction_is_longest_first_and_diagnostics_only():
    from agentreducer.agents._diagnostics import DiagnosticRedactor, normalize_output
    first = {"TOKEN": "dummy-abcdef", "EMPTY": ""}
    second = {"TOKEN": "dummy-abc", "OTHER": "短い秘密"}
    redact = DiagnosticRedactor.from_environments({"TOKEN", "EMPTY", "OTHER"}, first, second)
    raw = "dummy-abcdef dummy-abc 短い秘密 untouched"
    assert redact(raw) == "[REDACTED] [REDACTED] [REDACTED] untouched"
    assert raw == "dummy-abcdef dummy-abc 短い秘密 untouched"
    assert first["TOKEN"] == "dummy-abcdef"
    assert "dummy-abc" not in repr(redact)
    assert normalize_output(b"a\r\nb\xff\r") == "a\nb\ufffd\n"
    assert normalize_output(None) == ""


def test_redaction_sanitizes_command_snapshots_and_exception_notes():
    from agentreducer.agents._diagnostics import DiagnosticRedactor
    from agentreducer.execution import CommandResult
    redact = DiagnosticRedactor.from_environments({"TOKEN"}, {"TOKEN": "dummy-secret"})
    raw = CommandResult(("fixture", "dummy-secret"), 0, "dummy-secret", "dummy-secret", 0.1, False)
    clean = redact.command(raw)
    assert "dummy-secret" not in repr(clean)
    assert raw.stdout == "dummy-secret"
    cancellation = KeyboardInterrupt("stop")
    cancellation.add_note("cleanup dummy-secret")
    redact.notes(cancellation)
    assert cancellation.__notes__ == ["cleanup [REDACTED]"]


def test_explicit_model_free_preflight_is_separate_and_cannot_certify_controlled(tmp_path, monkeypatch):
    runner = fake_runner(tmp_path, mode=CodexContextMode.CONTROLLED)
    assert records(tmp_path / "probes.jsonl") == []
    assert records(tmp_path / "agents.jsonl") == []
    actual_run = subprocess.run
    observations = []
    def recording_run(*args, **kwargs):
        observations.append(kwargs)
        return actual_run(*args, **kwargs)
    monkeypatch.setattr(codex.subprocess, "run", recording_run)
    report = runner.preflight(cwd=tmp_path)
    assert report.version == "codex fixture-1.0"
    assert report.invocation == runner.invocation
    assert report.supported_flags == codex.REQUIRED_FLAGS
    assert report.errors == ()
    assert set(report.unknown_sources) >= {"skills", "plugins", "hooks", "managed injection"}
    assert len(records(tmp_path / "probes.jsonl")) == 3
    assert records(tmp_path / "agents.jsonl") == []
    assert all(item["stdin"] is subprocess.DEVNULL and item["shell"] is False for item in observations)
    assert all(0 < item["timeout"] <= 5 for item in observations)
    result = runner.run(workspace=tmp_path, prompt="DO NOT EXECUTE")
    assert not result.succeeded and "unavailable" in result.error
    assert records(tmp_path / "agents.jsonl") == []
    assert len(records(tmp_path / "probes.jsonl")) == 3


@pytest.mark.parametrize("probe_mode", ["nonzero", "timeout"])
def test_preflight_errors_are_bounded_and_keep_unknowns(tmp_path, probe_mode):
    runner = fake_runner(tmp_path, timeout=0.3, env={"AGENTREDUCER_FAKE_PREFLIGHT_MODE": probe_mode})
    report = runner.preflight(cwd=tmp_path)
    assert report.version is None
    assert report.errors
    assert report.unknown_sources
    assert records(tmp_path / "agents.jsonl") == []


def test_missing_preflight_flag_is_reported_without_model_execution(tmp_path):
    runner = fake_runner(tmp_path, env={"AGENTREDUCER_FAKE_MISSING_FLAG": "--ephemeral"})
    report = runner.preflight(cwd=tmp_path)
    assert "--ephemeral" not in report.supported_flags
    assert any("--ephemeral" in error for error in report.errors)
    assert report.unknown_sources
    assert records(tmp_path / "agents.jsonl") == []


def test_controlled_auth_override_and_caller_evidence_cannot_enable_execution(tmp_path):
    runner = fake_runner(tmp_path, mode=CodexContextMode.CONTROLLED, env={"CODEX_API_KEY": "dummy-auth"})
    result = runner.run(workspace=tmp_path, prompt="task")
    assert result.command is None
    assert not result.succeeded
    assert "dummy-auth" not in repr(result)
    assert result.context is runner.context
    assert not result.context.native_controlled_verified
    assert records(tmp_path / "agents.jsonl") == records(tmp_path / "probes.jsonl") == []
    with pytest.raises(TypeError):
        CodexRunner(runner.spec, context=object())


def test_ambient_process_receives_exact_stdin_argv_cwd_and_one_launch(tmp_path, monkeypatch):
    runner = fake_runner(tmp_path)
    prompt = "Unicode 繁體中文\r\nsecond line\n`$(echo nope)` & | ; \"quote\"\0end"
    actual_run = subprocess.run
    observations = []
    def recording_run(*args, **kwargs):
        observations.append((args, kwargs))
        return actual_run(*args, **kwargs)
    monkeypatch.setattr(codex.subprocess, "run", recording_run)
    result = runner.run(workspace=tmp_path, prompt=prompt)
    assert result.succeeded
    record = records(tmp_path / "agents.jsonl")[0]
    assert record["prompt"] == prompt
    assert Path(record["cwd"]) == tmp_path
    assert record["argv"] == [
        "--ask-for-approval", "never", "exec", "--model", "fixture-model",
        "--sandbox", "workspace-write", "--skip-git-repo-check", "--ephemeral",
        "--ignore-user-config", "--color", "never", "-",
    ]
    assert prompt not in repr(result.command.argv)
    assert len(observations) == len(records(tmp_path / "agents.jsonl")) == 1
    assert observations[0][1]["shell"] is False
    assert observations[0][1]["input"] == prompt.encode("utf-8")
    assert records(tmp_path / "probes.jsonl") == []
    assert "stable during reduction" in result.context.qualification
    assert result.command.duration_seconds >= 0


@pytest.mark.parametrize("mode", ["nonzero", "invalid-utf8", "timeout"])
def test_real_fake_process_status_and_partial_diagnostics(tmp_path, mode):
    runner = fake_runner(tmp_path, timeout=0.4 if mode == "timeout" else 5,
                         env={"AGENTREDUCER_FAKE_MODE": mode})
    result = runner.run(workspace=tmp_path, prompt="task")
    assert len(records(tmp_path / "agents.jsonl")) == 1
    assert "conditional" in result.context.qualification
    if mode == "nonzero":
        assert not result.succeeded
        assert result.command.exit_code == 7
        assert "PASS" in result.command.stdout
        assert result.error
    elif mode == "invalid-utf8":
        assert result.succeeded
        assert "\ufffd" in result.command.stdout and "\ufffd" in result.command.stderr
    else:
        assert not result.succeeded
        assert result.command.timed_out and result.command.exit_code is None
        assert "partial stdout" in result.command.stdout
        assert "partial stderr" in result.command.stderr
        pid = records(tmp_path / "agents.jsonl")[0]["pid"]
        if os.name == "nt":
            import ctypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            handle = kernel.OpenProcess(0x1000, False, pid)
            if handle:
                code = ctypes.c_ulong()
                kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
                kernel.CloseHandle(ctypes.c_void_p(handle))
                assert code.value != 259  # STILL_ACTIVE
        else:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)


def test_child_secret_values_are_unchanged_but_exposed_diagnostics_are_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-inherited-long")
    runner = fake_runner(tmp_path, env={
        "CODEX_API_KEY": "dummy-agent-only", "CUSTOM_TOKEN": "dummy-custom",
        "AGENTREDUCER_FAKE_ECHO_NAMES": "OPENAI_API_KEY,CODEX_API_KEY,CUSTOM_TOKEN",
    })
    runner = CodexRunner(CodexSpec(runner.spec.model, runner.spec.argv_prefix, 5, runner.spec.env,
                                  CodexContextMode.AMBIENT, {"CUSTOM_TOKEN"}))
    raw_inputs = []
    actual_run = subprocess.run
    def recording_run(*args, **kwargs):
        raw_inputs.append((kwargs["env"].copy(), kwargs["input"]))
        return actual_run(*args, **kwargs)
    monkeypatch.setattr(codex.subprocess, "run", recording_run)
    result = runner.run(workspace=tmp_path, prompt="task dummy-agent-only")
    assert result.succeeded
    for value in ("dummy-inherited-long", "dummy-agent-only", "dummy-custom"):
        assert value not in repr(result)
    assert result.command.stdout.count("[REDACTED]") == 4  # three values plus echoed stdin
    assert raw_inputs[0][0]["CODEX_API_KEY"] == "dummy-agent-only"
    assert raw_inputs[0][1] == b"task dummy-agent-only"
    assert os.environ.get("CODEX_API_KEY") != "dummy-agent-only"


def test_disappeared_executable_and_invalid_prompt_are_ambient_errors(tmp_path):
    executable = tmp_path / ("native.exe" if os.name == "nt" else "native")
    executable.write_bytes(b"not executed")
    executable.chmod(0o700)
    runner = CodexRunner(CodexSpec("fixture", (str(executable),), context_mode=CodexContextMode.AMBIENT))
    executable.unlink()
    result = runner.run(workspace=tmp_path, prompt="task")
    assert result.command is None and result.error
    assert "conditional" in result.context.qualification
    bad_prompt = fake_runner(tmp_path).run(workspace=tmp_path, prompt=42)
    assert bad_prompt.error and bad_prompt.command is None
    assert records(tmp_path / "agents.jsonl") == []


@pytest.mark.parametrize("out,err", [(b"partial\xff", None), (None, b"error\xfe")])
def test_timeout_bytes_and_none_are_normalized(tmp_path, monkeypatch, out, err):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 5, output=out, stderr=err)
    monkeypatch.setattr(codex.subprocess, "run", timeout)
    result = fake_runner(tmp_path).run(workspace=tmp_path, prompt="task")
    assert result.command.timed_out
    assert isinstance(result.command.stdout, str) and isinstance(result.command.stderr, str)


@pytest.mark.parametrize("exception", [KeyboardInterrupt("cancel"), SystemExit(9)])
def test_runner_preserves_cancellation_object_and_redacts_adapter_notes(tmp_path, monkeypatch, exception):
    def cancel(*args, **kwargs):
        exception.add_note("child cleanup dummy-agent-only")
        raise exception
    monkeypatch.setattr(codex.subprocess, "run", cancel)
    runner = fake_runner(tmp_path, env={"CODEX_API_KEY": "dummy-agent-only"})
    with pytest.raises(type(exception)) as caught:
        runner.run(workspace=tmp_path, prompt="task")
    assert caught.value is exception
    assert exception.__notes__ == ["child cleanup [REDACTED]"]


@pytest.mark.parametrize("target_name", ["codex.CMD", "codex.BaT", "pwsh.exe"])
def test_resolved_alias_target_must_also_reject_batch_or_shell(tmp_path, monkeypatch, target_name):
    alias = tmp_path / "trusted-name.exe"
    target = tmp_path / target_name
    alias.write_bytes(b"alias fixture")
    target.write_bytes(b"target fixture")
    target.chmod(0o700)
    original_resolve = Path.resolve
    def resolve(path, *args, **kwargs):
        return target if path == alias else original_resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError, match="batch|shell"):
        CodexRunner(CodexSpec("fixture", (str(alias),)))


@pytest.mark.parametrize("phase", ["success", "timeout", "preflight"])
def test_adapter_line_normalization_cannot_expose_multiline_known_secret(tmp_path, monkeypatch, phase):
    secret = "DUMMY_MULTILINE_FIRST\r\nDUMMY_MULTILINE_LAST"
    runner = fake_runner(tmp_path, env={"CODEX_API_KEY": secret})
    def process(argv, **kwargs):
        if phase == "timeout":
            raise subprocess.TimeoutExpired(argv, 5, output=secret.encode("utf-8"), stderr=secret.encode("utf-8"))
        output = secret if phase == "preflight" else secret.encode("utf-8")
        return subprocess.CompletedProcess(argv, 0, output, output)
    monkeypatch.setattr(codex.subprocess, "run", process)
    result = runner.preflight(cwd=tmp_path) if phase == "preflight" else runner.run(workspace=tmp_path, prompt="task")
    assert "DUMMY_MULTILINE" not in repr(result)
    assert "[REDACTED]" in repr(result)
