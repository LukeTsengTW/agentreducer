import json
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from agentreducer import OracleResult
from agentreducer.agents import (
    AgentContextEvidence, AgentExecutionOracle, AgentRunResult, AgentStage,
    CodexContextMode, CodexRunner, CodexSpec,
)
from agentreducer.agents import oracle as oracle_module
from agentreducer.execution import CommandSpec, ExitCodePolicy, TextFileMaterializer, WorkspaceTemplate
from agentreducer.execution import workspace as workspace_module

FAKE = Path(__file__).parent / "fixtures" / "fake_codex.py"
VERIFIER = """import json, os, sys
from pathlib import Path
with Path(sys.argv[1]).open('a', encoding='utf-8') as stream:
    stream.write(json.dumps({'cwd': str(Path.cwd()), 'agent_token': os.environ.get('CODEX_API_KEY')}) + '\\n')
assert Path('state.txt').read_text() == 'mutated'
assert Path('trial-only.txt').read_text() == 'new'
print('verifier stdout')
print('verifier stderr', file=sys.stderr)
raise SystemExit(int(sys.argv[2]))
"""


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


class RecordingTemplate:
    def __init__(self, source, cleanup_error=None):
        self.template = WorkspaceTemplate(source)
        self.paths = []
        self.cleanup_error = cleanup_error

    @contextmanager
    def create(self):
        with self.template.create() as workspace:
            self.paths.append(workspace.path)
            yield workspace
        if self.cleanup_error:
            raise self.cleanup_error


def setup_oracle(tmp_path, *, mode="mutate", verifier_code=0, **kwargs):
    source = tmp_path / "source"
    source.mkdir()
    (source / "state.txt").write_bytes(b"original")
    (source / "AGENTS.md").write_text("original instructions", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    template = RecordingTemplate(source)
    runner = CodexRunner(CodexSpec("fixture", (sys.executable, str(FAKE.resolve())), 0.4 if mode == "timeout" else 5,
                                 {"AGENTREDUCER_FAKE_MODE": mode, "AGENTREDUCER_FAKE_COUNTER": str(tmp_path / "agents.jsonl")},
                                 CodexContextMode.AMBIENT))
    verifier = CommandSpec((sys.executable, "-c", VERIFIER, str(tmp_path / "verifiers.jsonl"), str(verifier_code)), 5)
    oracle = AgentExecutionOracle(template, runner, verifier, lambda candidate: candidate, **kwargs)
    return oracle, template, source, before


def assert_clean(template, source, before):
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before
    assert len(set(template.paths)) == len(template.paths)
    assert all(not path.exists() for path in template.paths)


@pytest.mark.parametrize("code,outcome", [(0, OracleResult.PASS), (1, OracleResult.FAIL)])
def test_one_workspace_agent_mutation_then_verifier_and_fresh_trials(tmp_path, code, outcome):
    oracle, template, source, before = setup_oracle(tmp_path, verifier_code=code)
    detail = oracle.evaluate_detailed("task")
    assert detail.outcome is outcome
    assert detail.error is detail.error_stage is None
    assert detail.agent_result.succeeded
    assert "verifier stdout" in detail.verifier_result.stdout
    assert "verifier stderr" in detail.verifier_result.stderr
    assert oracle.evaluate("second task") is outcome
    agents, verifiers = rows(tmp_path / "agents.jsonl"), rows(tmp_path / "verifiers.jsonl")
    assert len(agents) == len(verifiers) == len(template.paths) == 2
    assert [Path(row["cwd"]) for row in agents] == [Path(row["cwd"]) for row in verifiers] == template.paths
    assert detail.context is oracle.context
    assert "stable during reduction" in detail.context.qualification
    assert_clean(template, source, before)


def test_materialization_precedes_render_and_agent_in_actual_workspace(tmp_path):
    oracle, template, source, before = setup_oracle(tmp_path)
    materializer = TextFileMaterializer("AGENTS.md", lambda candidate: candidate)
    def render(candidate):
        assert (template.paths[-1] / "AGENTS.md").read_text() == candidate
        return "fixed task"
    oracle = replace(oracle, materializer=materializer, render_prompt=render)
    assert oracle.evaluate("candidate instructions") is OracleResult.PASS
    assert rows(tmp_path / "agents.jsonl")[0]["prompt"] == "fixed task"
    assert_clean(template, source, before)


@pytest.mark.parametrize("mode", ["nonzero", "timeout"])
def test_agent_execution_error_stops_before_verifier(tmp_path, mode):
    oracle, template, source, before = setup_oracle(tmp_path, mode=mode)
    detail = oracle.evaluate_detailed("task")
    assert detail.outcome is OracleResult.ERROR
    assert detail.error_stage is AgentStage.AGENT
    assert detail.agent_result.command is not None and detail.verifier_result is None
    assert len(rows(tmp_path / "agents.jsonl")) == 1
    assert rows(tmp_path / "verifiers.jsonl") == []
    assert "conditional" in detail.context.qualification
    assert_clean(template, source, before)


def test_unknown_verifier_code_is_error_with_both_results(tmp_path):
    oracle, template, source, before = setup_oracle(tmp_path, verifier_code=7, exit_code_policy=ExitCodePolicy({0}, {1}))
    detail = oracle.evaluate_detailed("task")
    assert detail.outcome is OracleResult.ERROR and detail.error_stage is AgentStage.POLICY
    assert detail.agent_result.succeeded and detail.verifier_result.exit_code == 7
    assert_clean(template, source, before)


@pytest.mark.parametrize("bad_result", [None, "not a result", AgentRunResult(),
    AgentRunResult(context=AgentContextEvidence())])
def test_invalid_agent_result_or_context_never_launches_verifier(tmp_path, bad_result):
    oracle, template, source, before = setup_oracle(tmp_path)
    class BadRunner:
        context = oracle.context
        def run(self, **kwargs):
            return bad_result
    detail = replace(oracle, agent=BadRunner()).evaluate_detailed("task")
    assert detail.outcome is OracleResult.ERROR and detail.error_stage is AgentStage.AGENT
    assert detail.verifier_result is None
    assert rows(tmp_path / "verifiers.jsonl") == []
    assert_clean(template, source, before)


@pytest.mark.parametrize("phase", ["workspace", "materialization", "prompt", "agent", "verifier", "policy"])
def test_phase_exceptions_keep_ambient_context_and_prior_diagnostics(tmp_path, monkeypatch, phase):
    oracle, template, source, before = setup_oracle(tmp_path)
    def failure(*args, **kwargs):
        raise OSError("phase failure")
    if phase == "workspace":
        class BadWorkspace:
            @contextmanager
            def create(self):
                failure()
                yield
        oracle = replace(oracle, workspace=BadWorkspace())
    elif phase == "materialization":
        class BadMaterializer:
            materialize = staticmethod(failure)
        oracle = replace(oracle, materializer=BadMaterializer())
    elif phase == "prompt":
        oracle = replace(oracle, render_prompt=lambda _: 42)
    elif phase == "agent":
        class BadRunner:
            context = oracle.context
            run = staticmethod(failure)
        oracle = replace(oracle, agent=BadRunner())
    elif phase == "verifier":
        monkeypatch.setattr(oracle_module, "run_command", failure)
    else:
        class BadPolicy:
            classify = staticmethod(failure)
        oracle = replace(oracle, exit_code_policy=BadPolicy())
    detail = oracle.evaluate_detailed("task")
    assert detail.outcome is OracleResult.ERROR and detail.error_stage.value == phase
    assert detail.error and "stable during reduction" in detail.context.qualification
    assert (detail.agent_result is not None) == (phase in {"verifier", "policy"})
    assert (detail.verifier_result is not None) == (phase == "policy")
    assert_clean(template, source, before)


def test_verifier_timeout_and_invalid_policy_are_errors(tmp_path, monkeypatch):
    oracle, template, source, before = setup_oracle(tmp_path)
    oracle = replace(oracle, verifier=CommandSpec((sys.executable, "-c", "import time; print('partial', flush=True); time.sleep(30)"), 0.4))
    detail = oracle.evaluate_detailed("task")
    assert detail.outcome is OracleResult.ERROR and detail.error_stage is AgentStage.VERIFIER
    assert detail.verifier_result.timed_out and "partial" in detail.verifier_result.stdout
    class InvalidPolicy:
        def classify(self, code):
            return "PASS"
    detail = replace(oracle, verifier=CommandSpec((sys.executable, "-c", "pass")), exit_code_policy=InvalidPolicy()).evaluate_detailed("task")
    assert detail.outcome is OracleResult.ERROR and detail.error_stage is AgentStage.POLICY
    assert detail.verifier_result is not None
    assert_clean(template, source, before)


@pytest.mark.parametrize("agent_mode", ["mutate", "nonzero"])
def test_cleanup_error_keeps_earlier_result_and_error(tmp_path, agent_mode):
    oracle, template, source, before = setup_oracle(tmp_path, mode=agent_mode)
    template.cleanup_error = OSError("cleanup failed")
    detail = oracle.evaluate_detailed("task")
    assert detail.outcome is OracleResult.ERROR and detail.error_stage is AgentStage.CLEANUP
    assert "cleanup failed" in detail.error
    assert detail.agent_result.command is not None
    assert (detail.verifier_result is not None) == (agent_mode == "mutate")
    if agent_mode == "nonzero":
        assert "Agent exit code: 7" in detail.error
    assert_clean(template, source, before)


def test_agent_only_secret_is_not_forwarded_to_verifier_and_all_public_diagnostics_are_clean(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-inherited")
    oracle, template, source, before = setup_oracle(tmp_path)
    env = dict(oracle.agent.spec.env, CODEX_API_KEY="dummy-agent-only", AGENTREDUCER_FAKE_ECHO_NAMES="CODEX_API_KEY,OPENAI_API_KEY")
    agent = CodexRunner(replace(oracle.agent.spec, env=env))
    verifier = replace(oracle.verifier, env={"CUSTOM_TOKEN": "dummy-verifier-only"}, argv=(sys.executable, "-c", "import os; print(os.environ['CUSTOM_TOKEN'])\n" + VERIFIER, str(tmp_path / "verifiers.jsonl"), "0"))
    detail = replace(oracle, agent=agent, verifier=verifier, sensitive_env_names={"CUSTOM_TOKEN"}).evaluate_detailed("task")
    assert detail.outcome is OracleResult.PASS
    assert rows(tmp_path / "verifiers.jsonl")[0]["agent_token"] is None
    assert "[REDACTED]" in detail.verifier_result.stdout
    for secret in ("dummy-agent-only", "dummy-inherited", "dummy-verifier-only"):
        assert secret not in repr(detail)
    template.cleanup_error = OSError("cleanup dummy-agent-only dummy-verifier-only")
    failure = replace(oracle, agent=agent, verifier=verifier, sensitive_env_names={"CUSTOM_TOKEN"}).evaluate_detailed("task")
    assert "dummy-" not in failure.error and "[REDACTED]" in failure.error
    assert_clean(template, source, before)


@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("phase", ["materialization", "prompt", "agent", "verifier"])
def test_original_cancellation_survives_workspace_cleanup_failure_with_redacted_note(tmp_path, monkeypatch, kind, phase):
    oracle, template, source, before = setup_oracle(tmp_path)
    agent = CodexRunner(replace(oracle.agent.spec, env=dict(oracle.agent.spec.env, CODEX_API_KEY="dummy-agent-only")))
    oracle = replace(oracle, agent=agent)
    cancellation = kind("cancel")
    def cancel(*args, **kwargs):
        raise cancellation
    if phase == "materialization":
        class CancellingMaterializer:
            materialize = staticmethod(cancel)
        oracle = replace(oracle, materializer=CancellingMaterializer())
    elif phase == "prompt":
        oracle = replace(oracle, render_prompt=cancel)
    elif phase == "agent":
        monkeypatch.setattr(CodexRunner, "run", cancel)
    else:
        monkeypatch.setattr(oracle_module, "run_command", cancel)
    original = workspace_module.tempfile.TemporaryDirectory
    class CleanupFailure(original):
        def cleanup(self):
            super().cleanup()
            raise OSError("cleanup dummy-agent-only")
    monkeypatch.setattr(workspace_module.tempfile, "TemporaryDirectory", CleanupFailure)
    with pytest.raises(kind) as caught:
        oracle.evaluate_detailed("task")
    assert caught.value is cancellation
    assert "dummy-agent-only" not in " ".join(cancellation.__notes__)
    assert "[REDACTED]" in " ".join(cancellation.__notes__)
    assert_clean(template, source, before)


def test_native_controlled_oracle_has_zero_agent_and_verifier_launches(tmp_path):
    oracle, template, source, before = setup_oracle(tmp_path)
    runner = CodexRunner(replace(oracle.agent.spec, context_mode=CodexContextMode.CONTROLLED))
    detail = replace(oracle, agent=runner).evaluate_detailed("task")
    assert detail.outcome is OracleResult.ERROR and detail.error_stage is AgentStage.AGENT
    assert detail.agent_result.command is detail.verifier_result is None
    assert rows(tmp_path / "agents.jsonl") == rows(tmp_path / "verifiers.jsonl") == []
    assert_clean(template, source, before)


def test_verifier_normalized_multiline_secret_is_redacted_without_changing_child_value(tmp_path):
    oracle, template, source, before = setup_oracle(tmp_path)
    secret = "DUMMY_VERIFIER_FIRST\r\nDUMMY_VERIFIER_LAST"
    script = "import os; value=os.environ['CUSTOM_TOKEN'].encode('utf-8'); os.write(1,value); os.write(2,value)"
    verifier = CommandSpec((sys.executable, "-c", script), 5, {"CUSTOM_TOKEN": secret})
    detail = replace(oracle, verifier=verifier, sensitive_env_names={"CUSTOM_TOKEN"}).evaluate_detailed("task")
    assert detail.outcome is OracleResult.PASS
    assert "DUMMY_VERIFIER" not in repr(detail)
    assert detail.verifier_result.stdout == detail.verifier_result.stderr == "[REDACTED]"
    assert verifier.env["CUSTOM_TOKEN"] == secret
    assert_clean(template, source, before)
