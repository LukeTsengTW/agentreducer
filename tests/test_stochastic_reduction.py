from dataclasses import FrozenInstanceError

import pytest

from agentreducer import InitialCandidateNotFailingError, OracleEvaluationError, OracleResult
from agentreducer.instructions import InstructionCandidate, InstructionReducer, parse_instructions
from agentreducer.prompt import PromptCandidate, PromptReducer, PromptSplitMode, split_prompt
from agentreducer.stochastic import (
    RepeatedOracle, StochasticPolicy, reduce_instructions_stochastic, reduce_prompt_stochastic,
)

P, F, E = OracleResult.PASS, OracleResult.FAIL, OracleResult.ERROR


class RecordingSession(RepeatedOracle):
    def evaluate(self, candidate):
        outcome = super().evaluate(candidate)
        self.trace.append((candidate, outcome))
        return outcome


def make_candidate(domain, text):
    return split_prompt(text, PromptSplitMode.LINE) if domain == "prompt" else parse_instructions(text)


def assert_witnesses(result, trace):
    def same_occurrences(left, right):
        return (left == right and len(left.blocks) == len(right.blocks)
                and all(a is b for a,b in zip(left.blocks,right.blocks)))
    assert any(same_occurrences(candidate,result.reduced) and outcome is F for candidate, outcome in trace)
    for index in range(len(result.reduced.blocks)):
        blocks = result.reduced.blocks[:index] + result.reduced.blocks[index + 1:]
        deletion = (PromptCandidate(blocks, result.reduced.mode) if isinstance(result.reduced, PromptCandidate)
                    else InstructionCandidate(blocks))
        assert any(same_occurrences(candidate,deletion) and outcome is P for candidate, outcome in trace)


@pytest.mark.parametrize("domain", ["prompt", "instructions"])
@pytest.mark.parametrize("case", ["two-triggers", "singleton", "empty-failing", "duplicates", "nonstationary"])
def test_actual_reduction_trace_supplies_minimality_witnesses(domain, case):
    text = "A\nB\nUSE_CACHE\nC\nD\nAUTH_MODULE\nE\nF" if domain == "prompt" else "# Rules\n- A\n- USE_CACHE\n- AUTH_MODULE\n- B"
    if case == "singleton": text = "KEEP"
    if case == "duplicates": text = "KEEP\nKEEP\nother" if domain == "prompt" else "# Rules\n- KEEP\n- KEEP\n- other"
    candidate = make_candidate(domain, text)
    class Base:
        calls = 0
        def evaluate(self, current):
            self.calls += 1
            rendered = current.render()
            if case == "empty-failing": return F
            if case == "nonstationary": return F if current.blocks and self.calls % 3 == 1 else P
            if case == "duplicates": return F if rendered.count("KEEP") >= 2 else P
            if case == "singleton": return F if "KEEP" in rendered else P
            return F if "USE_CACHE" in rendered and "AUTH_MODULE" in rendered else P
    base = Base()
    session = RecordingSession(base, StochasticPolicy(1,1,1), max_total_trials=200)
    session.trace = []
    wrapper = reduce_prompt_stochastic if domain == "prompt" else reduce_instructions_stochastic
    qualified = wrapper(candidate, session)
    result = qualified.reduction
    assert result.original is candidate
    assert_witnesses(result, session.trace)
    count = result.evaluations
    assert len(session.trace) == count == base.calls == qualified.budget.used
    assert qualified.counters.logical_candidate_evaluations == count
    assert qualified.counters.underlying_trial_attempts == count
    assert qualified.counters.observed_fail_trials == qualified.counters.committed_fail_trials
    assert qualified.context is None
    assert "relative to the policy" in qualified.qualification
    with pytest.raises(FrozenInstanceError): qualified.context = None
    if case == "two-triggers":
        assert result.reduced_block_count == 2
        assert "USE_CACHE" in result.reduced.render() and "AUTH_MODULE" in result.reduced.render()
    if case == "empty-failing": assert result.reduced_block_count == 0
    if case == "duplicates": assert result.reduced_block_count == 2
    print(f"M5 trace {domain}/{case}: logical={count}; retained FAIL + {result.reduced_block_count} deletion PASS witnesses; extra0")


@pytest.mark.parametrize("domain", ["prompt", "instructions"])
def test_wrapper_delegates_once_and_metadata_is_free(domain, monkeypatch):
    reducer = PromptReducer if domain == "prompt" else InstructionReducer
    calls = []
    original = reducer.reduce
    def spy(self, candidate, oracle):
        calls.append(candidate)
        return original(self, candidate, oracle)
    monkeypatch.setattr(reducer, "reduce", spy)
    class Base:
        trials = 0
        def evaluate(self, candidate):
            self.trials += 1
            return F if "KEEP" in candidate.render() else P
    base = Base()
    candidate = make_candidate(domain, "KEEP")
    session = RepeatedOracle(base, StochasticPolicy(2,2,1), max_total_trials=20)
    qualified = (reduce_prompt_stochastic if domain == "prompt" else reduce_instructions_stochastic)(candidate, session)
    assert calls == [candidate]
    assert base.trials == 2 * qualified.reduction.evaluations == qualified.budget.used
    assert qualified.reduction.original_character_count == len(candidate.render())
    assert qualified.reduction.reduced_character_count == len(qualified.reduction.reduced.render())
    assert session.snapshot() == qualified.counters
    assert base.trials == qualified.budget.used


@pytest.mark.parametrize("domain", ["prompt", "instructions"])
@pytest.mark.parametrize("outcome,error", [(P,InitialCandidateNotFailingError), (E,OracleEvaluationError)])
def test_wrapper_preserves_initial_pass_and_error(domain, outcome, error):
    class Base:
        def evaluate(self, candidate): return outcome
    session = RepeatedOracle(Base(), StochasticPolicy(1,1,1), max_total_trials=10)
    with pytest.raises(error):
        (reduce_prompt_stochastic if domain == "prompt" else reduce_instructions_stochastic)(make_candidate(domain,"KEEP"), session)
    assert session.budget.used == 1
    assert session.last_evaluation.outcome is outcome


@pytest.mark.parametrize("domain", ["prompt", "instructions"])
def test_used_session_rejected_before_any_new_trial(domain):
    class Base:
        calls = 0
        def evaluate(self, candidate):
            self.calls += 1
            return F
    base = Base()
    session = RepeatedOracle(base, StochasticPolicy(1,1,1), max_total_trials=10)
    session.evaluate(None)
    with pytest.raises(ValueError, match="fresh"):
        (reduce_prompt_stochastic if domain == "prompt" else reduce_instructions_stochastic)(make_candidate(domain,"KEEP"), session)
    assert base.calls == session.budget.used == 1


@pytest.mark.parametrize("failure", [RuntimeError("fixture"), KeyboardInterrupt(), SystemExit(7)])
def test_wrapper_propagates_original_exception_without_partial_result(failure):
    class Base:
        def evaluate(self, candidate): raise failure
    session = RepeatedOracle(Base(), StochasticPolicy(1,1,1), max_total_trials=10)
    with pytest.raises(type(failure)) as raised:
        reduce_prompt_stochastic(split_prompt("KEEP"), session)
    assert raised.value is failure
    assert session.last_evaluation is None and session.budget.used == 1
