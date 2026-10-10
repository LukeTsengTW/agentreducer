from dataclasses import FrozenInstanceError

import pytest

from agentreducer import OracleResult
from agentreducer.agents import (
    AgentContextEvidence, AgentEvaluation, AgentReductionResult, AgentRunResult,
    AgentStage, CodexContextMode, ContextAssurance, ContextCapabilities,
)
from agentreducer.execution import CommandResult


def result(code=0, timed_out=False):
    return CommandResult(["fixture"], code, "done", "", 0.1, timed_out)


def test_context_defaults_to_unverified_controlled():
    context = AgentContextEvidence()
    assert context.mode is CodexContextMode.CONTROLLED
    assert context.assurance is ContextAssurance.UNVERIFIED
    assert context.unknown_sources
    assert not context.native_controlled_verified


def test_ambient_always_explains_conditional_stability():
    context = AgentContextEvidence.ambient()
    assert context.mode is CodexContextMode.AMBIENT
    assert context.assurance is ContextAssurance.AMBIENT
    for phrase in ("not controlled", "not frozen", "conditional", "stable during reduction"):
        assert phrase in context.qualification
    with pytest.raises(FrozenInstanceError):
        context.mode = CodexContextMode.CONTROLLED


def test_context_and_capabilities_snapshot_input_collections():
    unknowns = ["skills"]
    flags = {"--ephemeral"}
    invocation = ["fixture"]
    context = AgentContextEvidence(unknown_sources=unknowns)
    capabilities = ContextCapabilities(invocation, "fixture-1", flags, unknowns)
    unknowns.clear()
    flags.clear()
    invocation.clear()
    assert context.unknown_sources == ("skills",)
    assert capabilities.unknown_sources == ("skills",)
    assert capabilities.supported_flags == frozenset({"--ephemeral"})
    assert capabilities.invocation == ("fixture",)
    with pytest.raises(FrozenInstanceError):
        capabilities.version = "forged"


@pytest.mark.parametrize("version,unknowns", [(None, ()), ("v1", ("hooks",)), ("v1", ())])
def test_caller_cannot_construct_native_verified_evidence(version, unknowns):
    with pytest.raises((TypeError, ValueError)):
        AgentContextEvidence(
            assurance="controlled-verified", version=version, unknown_sources=unknowns,
        )


def test_fake_evidence_cannot_certify_native_isolation():
    context = AgentContextEvidence(
        assurance=ContextAssurance.FAKE_FIXTURE, version="fixture-1", unknown_sources=(),
    )
    assert "fake" in context.qualification.lower()
    assert not context.native_controlled_verified


@pytest.mark.parametrize("kwargs", [
    {"mode": "AMBIENT"},
    {"assurance": ContextAssurance.AMBIENT},
    {"mode": CodexContextMode.AMBIENT},
    {"assurance": ContextAssurance.FAKE_FIXTURE},
    {"unknown_sources": [1]},
])
def test_invalid_context_schema_is_rejected(kwargs):
    with pytest.raises((TypeError, ValueError)):
        AgentContextEvidence(**kwargs)


@pytest.mark.parametrize("command,error,expected", [
    (result(), None, True), (result(1), None, False),
    (result(None, True), None, False), (None, None, False),
    (result(), "launch error", False),
])
def test_agent_success_uses_execution_status_only(command, error, expected):
    run = AgentRunResult(command, error, AgentContextEvidence.ambient())
    assert run.succeeded is expected
    with pytest.raises(FrozenInstanceError):
        run.error = "changed"


def test_evaluation_and_wrapper_keep_qualification_without_execution():
    context = AgentContextEvidence.ambient()
    run = AgentRunResult(result(), None, context)
    evaluation = AgentEvaluation(OracleResult.ERROR, run, None, context, AgentStage.VERIFIER, "bad verifier")
    unchanged_reduction = object()
    wrapper = AgentReductionResult(unchanged_reduction, context)
    assert wrapper.reduction is unchanged_reduction
    assert evaluation.context is wrapper.context is context
    assert "stable during reduction" in wrapper.context.qualification
    with pytest.raises(FrozenInstanceError):
        evaluation.error = None
    with pytest.raises(FrozenInstanceError):
        wrapper.reduction = None


def test_command_argv_snapshot_is_retained_in_agent_result():
    argv = ["fixture", "fixed"]
    command = CommandResult(argv, 0, "", "", 0, False)
    run = AgentRunResult(command, None, AgentContextEvidence.ambient())
    argv.append("changed")
    assert run.command.argv == ("fixture", "fixed")


@pytest.mark.parametrize("domain", ["prompt", "instructions"])
def test_qualified_entry_points_delegate_once_without_metadata_evaluations(monkeypatch, domain):
    from agentreducer.agents import reduction
    context = AgentContextEvidence.ambient()
    calls = []
    unchanged = object()
    candidate = object()
    class Oracle:
        context = AgentContextEvidence.ambient()
        def evaluate(self, value):
            pytest.fail("Wrapper must not evaluate to gather metadata")
    oracle = Oracle()
    reducer_class = reduction.PromptReducer if domain == "prompt" else reduction.InstructionReducer
    def reduce_once(self, actual_candidate, actual_oracle):
        calls.append((actual_candidate, actual_oracle))
        return unchanged
    monkeypatch.setattr(reducer_class, "reduce", reduce_once)
    entry = reduction.reduce_prompt if domain == "prompt" else reduction.reduce_instructions
    result = entry(candidate, oracle)
    assert calls == [(candidate, oracle)]
    assert result.reduction is unchanged and result.context is oracle.context
    assert "stable during reduction" in result.context.qualification
    assert calls == [(candidate, oracle)]


@pytest.mark.parametrize("domain", ["prompt", "instructions"])
@pytest.mark.parametrize("outcomes,error_name", [
    ([OracleResult.PASS], "InitialCandidateNotFailingError"),
    ([OracleResult.FAIL, OracleResult.ERROR], "OracleEvaluationError"),
])
def test_qualified_entry_points_preserve_reducer_exceptions(domain, outcomes, error_name):
    import agentreducer
    from agentreducer.agents import reduce_instructions, reduce_prompt
    from agentreducer.instructions import parse_instructions
    from agentreducer.prompt import split_prompt
    class Oracle:
        context = AgentContextEvidence.ambient()
        calls = 0
        def evaluate(self, candidate):
            value = outcomes[self.calls]
            self.calls += 1
            return value
    oracle = Oracle()
    entry, candidate = (reduce_prompt, split_prompt("trigger")) if domain == "prompt" else (reduce_instructions, parse_instructions("- trigger"))
    with pytest.raises(getattr(agentreducer, error_name)):
        entry(candidate, oracle)
    assert oracle.calls == len(outcomes)
