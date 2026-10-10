from dataclasses import FrozenInstanceError
from fractions import Fraction

import pytest

from agentreducer import OracleResult
from agentreducer.agents import AgentContextEvidence
from agentreducer.stochastic import (
    AgentTrials, BudgetSnapshot, CommandTrials, LaunchCounts, OrdinaryTrials,
    RawTrial, RepeatedOracle, StochasticCounters, StochasticEvaluation,
    StochasticPolicy, StopReason, TrialObservation,
)

P, F, E = OracleResult.PASS, OracleResult.FAIL, OracleResult.ERROR


def test_unknown_launch_counts_keep_proven_lower_bounds():
    total = LaunchCounts(1, 1) + LaunchCounts(None, None, 1, 0)
    assert total == LaunchCounts(None, None, 2, 1)
    assert LaunchCounts(0, 0) + LaunchCounts(1, 2) == LaunchCounts(1, 2)
    assert LaunchCounts(None, 0) + LaunchCounts(0, 1) == LaunchCounts(None, 1)


@pytest.mark.parametrize("kwargs", [dict(agent=True), dict(verifier=-1), dict(agent=1.0),
    dict(agent_lower_bound=-1), dict(verifier_lower_bound=True), dict(agent=1, agent_lower_bound=2)])
def test_invalid_launch_evidence_rejected(kwargs):
    with pytest.raises((TypeError, ValueError)):
        LaunchCounts(**kwargs)


@pytest.mark.parametrize("kwargs", [dict(outcome="PASS"), dict(outcome=P, summary="x" * 2049),
    dict(outcome=P, reference="x" * 257), dict(outcome=P, stage="x" * 129),
    dict(outcome=P, context="verified"), dict(outcome=P, launch_counts={}),
    dict(outcome=P, summary=4)])
def test_invalid_or_unbounded_observations_rejected(kwargs):
    with pytest.raises((TypeError, ValueError)):
        TrialObservation(**kwargs)


def test_detailed_model_copies_records_and_excludes_errors_from_rate():
    policy = StochasticPolicy(5, 5, "0.8")
    budget = BudgetSnapshot(5, 3)
    context = AgentContextEvidence.ambient()
    records = [TrialObservation(F), TrialObservation(F), TrialObservation(E)]
    evaluation = StochasticEvaluation(E, policy, records, StopReason.TRIAL_ERROR,
        BudgetSnapshot(5, 0), budget, StochasticCounters(budget=budget, context=context),
        LaunchCounts(None, None), context, "qualified")
    records.clear()
    assert evaluation.trials_executed == 3
    assert evaluation.fail_trials == 2 and evaluation.error_trials == 1
    assert evaluation.observed_failure_rate == Fraction(1)
    assert evaluation.stopped_early and not evaluation.decision_complete
    assert evaluation.target_failures == 4
    for obj, name, value in [(evaluation, "outcome", P), (budget, "used", 0),
        (evaluation.records[0], "summary", "changed"), (evaluation.counters, "observed_fail_trials", 0)]:
        with pytest.raises(FrozenInstanceError):
            setattr(obj, name, value)


def test_zero_valid_trials_have_no_observed_failure_rate():
    policy = StochasticPolicy()
    budget = BudgetSnapshot(5, 0)
    result = StochasticEvaluation(E, policy, (), StopReason.BUDGET_EXHAUSTED,
        budget, budget, StochasticCounters(budget=budget), LaunchCounts(0, 0), None, "qualified")
    assert result.observed_failure_rate is None


class Scripted:
    def __init__(self, outcomes):
        self.values = iter(outcomes)
        self.calls = 0
    def evaluate(self, candidate):
        self.calls += 1
        value = next(self.values)
        if isinstance(value, BaseException):
            raise value
        return value


def identities(c):
    observed = c.observed_pass_trials + c.observed_fail_trials + c.observed_error_trials
    committed = c.committed_pass_trials + c.committed_fail_trials + c.committed_error_trials
    assert c.underlying_trial_attempts == c.budget.used
    assert c.underlying_trial_attempts == observed + c.raised_trial_attempts + c.invalid_trial_attempts
    assert c.underlying_trial_attempts == committed + c.unreturned_attempts
    assert c.unreturned_attempts == c.raised_trial_attempts + c.invalid_trial_attempts + c.postprocessing_failed_attempts
    assert c.logical_candidate_evaluations == (c.classified_pass_candidates + c.classified_fail_candidates
        + c.classified_error_candidates + c.aborted_candidate_evaluations)


@pytest.mark.parametrize("minimum,maximum,sequence,expected,reason,executed", [
    (5, 5, [F,F,P,F,F], F, StopReason.FULL_HORIZON, 5),
    (5, 5, [F,F,P,P,F], P, StopReason.FULL_HORIZON, 5),
    (2, 5, [P,P,E], P, StopReason.THRESHOLD_UNREACHABLE, 2),
    (2, 5, [F,F,F,F,E], F, StopReason.TARGET_REACHED, 4),
    (5, 5, [F,F,F,F,F], F, StopReason.FULL_HORIZON, 5),
    (5, 5, [F,F,E,P,P], E, StopReason.TRIAL_ERROR, 3),
    (5, 5, [E,P,P,P,P], E, StopReason.TRIAL_ERROR, 1),
    (1, 1, [P], P, StopReason.FULL_HORIZON, 1),
    (1, 1, [F], F, StopReason.FULL_HORIZON, 1),
])
def test_scripted_classification_and_stop_precedence(minimum, maximum, sequence, expected, reason, executed):
    base = Scripted(sequence)
    session = RepeatedOracle(base, StochasticPolicy(minimum, maximum, "0.8"), max_total_trials=maximum)
    result = session.evaluate_detailed("same candidate")
    assert result.outcome is expected and result.stop_reason is reason
    assert result.trials_executed == base.calls == executed
    assert session.budget.used == executed
    assert result.decision_complete == (expected is not E)
    assert result.context is session.context is None
    assert result.counters == session.snapshot()
    if expected is E and executed == 3:
        assert (result.fail_trials, result.pass_trials, result.error_trials) == (2, 0, 1)
        assert result.observed_failure_rate == 1
    identities(session.snapshot())


def test_safe_decisions_match_every_bounded_complete_sequence():
    from itertools import product
    cases = prefixes = 0
    for horizon in range(1, 8):
        for minimum in range(1, horizon + 1):
            for threshold in [Fraction(1, 10), Fraction(1, 3), Fraction(4, 5), Fraction(1)]:
                policy = StochasticPolicy(minimum, horizon, threshold)
                for sequence in product([P, F], repeat=horizon):
                    base = Scripted(sequence)
                    session = RepeatedOracle(base, policy, max_total_trials=horizon)
                    result = session.evaluate_detailed(())
                    expected = F if sequence.count(F) >= policy.target_failures else P
                    assert result.outcome is expected
                    assert minimum <= result.trials_executed <= horizon
                    assert (result.stop_reason is StopReason.FULL_HORIZON) == (result.trials_executed == horizon)
                    cases += 1
                    prefixes += result.trials_executed
    print(f"M5 arithmetic: N=1..7, all minima, 4 thresholds; sequences={cases}; executed_prefix_trials={prefixes}")


@pytest.mark.parametrize("outcome", [P,F])
@pytest.mark.parametrize("minimum,maximum,threshold", [(1,5,"0.8"), (4,5,"0.8"), (1,5,1), (3,7,"1/3"), (5,5,"0.8")])
def test_deterministic_sources_stop_at_exact_formula(outcome, minimum, maximum, threshold):
    policy = StochasticPolicy(minimum, maximum, threshold)
    base = Scripted([outcome] * maximum)
    session = RepeatedOracle(base, policy, max_total_trials=maximum)
    expected = max(minimum, policy.target_failures if outcome is F else maximum-policy.target_failures+1)
    assert session.evaluate_detailed(None).trials_executed == base.calls == expected


def test_partial_budget_is_error_and_sticky_without_fake_error_trial():
    base = Scripted([F,P,F])
    session = RepeatedOracle(base, StochasticPolicy(2,5,"0.8"), max_total_trials=2)
    first = session.evaluate_detailed("first")
    assert first.outcome is E and first.stop_reason is StopReason.BUDGET_EXHAUSTED
    assert (first.fail_trials, first.pass_trials, first.error_trials) == (1,1,0)
    assert first.observed_failure_rate == Fraction(1,2)
    assert not first.decision_complete
    second = session.evaluate_detailed("next")
    assert second.outcome is E and second.trials_executed == 0
    assert second.local_launch_counts == LaunchCounts(0,0)
    assert base.calls == 2 and session.budget.used == 2
    identities(session.snapshot())
    print("M5 budget: partial F1/P1/E0 -> ERROR at2/2; next precheck0calls")


@pytest.mark.parametrize("failure", [RuntimeError("fixture"), KeyboardInterrupt(), SystemExit(9)])
def test_exception_charged_and_stale_last_cleared(failure):
    base = Scripted([F, failure, F])
    session = RepeatedOracle(base, StochasticPolicy(1,1,1), max_total_trials=3)
    assert session.evaluate(None) is F and session.last_evaluation is not None
    with pytest.raises(type(failure)) as raised:
        session.evaluate(None)
    assert raised.value is failure and session.last_evaluation is None
    c = session.snapshot()
    assert c.underlying_trial_attempts == c.budget.used == 2
    assert c.observed_fail_trials == c.committed_fail_trials == 1
    assert c.raised_trial_attempts == c.unreturned_attempts == c.aborted_candidate_evaluations == 1
    identities(c)
    assert session.evaluate(None) is F


@pytest.mark.parametrize("invalid", [None, "FAIL", True, 1, object()])
def test_invalid_underlying_value_is_charged_then_propagates(invalid):
    base = Scripted([invalid])
    session = RepeatedOracle(base, StochasticPolicy(1,1,1), max_total_trials=1)
    with pytest.raises(TypeError):
        session.evaluate(None)
    c = session.snapshot()
    assert c.invalid_trial_attempts == c.unreturned_attempts == c.budget.used == 1
    assert c.observed_error_trials == c.committed_error_trials == 0
    identities(c)


@pytest.mark.parametrize("outcome", [P,F,E])
def test_summary_failure_keeps_raw_outcome_separate(outcome):
    failure = RuntimeError("summary fixture")
    class Source:
        context = None
        calls = 0
        def invoke(self, candidate):
            self.calls += 1
            return RawTrial(outcome)
        def summarize(self, raw):
            raise failure
    source = Source()
    session = RepeatedOracle.from_trials(source, StochasticPolicy(1,1,1), max_total_trials=1)
    with pytest.raises(RuntimeError) as raised:
        session.evaluate("candidate")
    assert raised.value is failure and source.calls == 1
    c = session.snapshot()
    assert getattr(c, "observed_" + outcome.name.lower() + "_trials") == 1
    assert c.committed_pass_trials == c.committed_fail_trials == c.committed_error_trials == 0
    assert c.postprocessing_failed_attempts == c.unreturned_attempts == c.budget.used == 1
    assert c.observed_error_trials == int(outcome is E)
    assert session.last_evaluation is None
    identities(c)
    print(f"M5 postprocessing: raw={outcome.name}; observed1/committed0/attempt1/unreturned1; trialERROR={c.observed_error_trials}")


def test_no_cached_samples_and_no_metadata_evaluations():
    base = Scripted([F,F,P,F,F] * 2)
    session = RepeatedOracle(base, StochasticPolicy(), max_total_trials=10)
    assert session.evaluate("duplicate") is F
    prior = session.last_evaluation
    assert session.evaluate_detailed("duplicate").outcome is F
    assert session.last_evaluation is not prior
    assert base.calls == 10 and session.snapshot().logical_candidate_evaluations == 2
    for _ in range(3):
        session.context, session.snapshot(), session.budget, session.last_evaluation, session.qualification
    assert base.calls == 10
    identities(session.snapshot())


def test_ordinary_and_detailed_sources_invoke_only_one_method():
    from agentreducer.execution.models import CommandEvaluation
    class CommandSpy:
        calls = 0
        def evaluate(self, candidate):
            raise AssertionError("Do not call both methods")
        def evaluate_detailed(self, candidate):
            self.calls += 1
            return CommandEvaluation(F, None, "raw unsafe diagnostic")
    base = CommandSpy()
    session = RepeatedOracle.from_trials(CommandTrials(base, include_summary=True),
        StochasticPolicy(1,1,1), max_total_trials=1)
    result = session.evaluate_detailed(None)
    assert base.calls == 1 and result.outcome is F
    assert result.records[0].summary is None
    assert result.local_launch_counts == LaunchCounts()
    ordinary = Scripted([P])
    raw = OrdinaryTrials(ordinary).invoke(None)
    assert raw.outcome is P and ordinary.calls == 1


@pytest.mark.parametrize("mismatch", [False, True])
def test_agent_source_preserves_context_or_aborts_after_raw_outcome(mismatch):
    from agentreducer.agents import AgentEvaluation, AgentRunResult
    from agentreducer.execution import CommandResult
    context = AgentContextEvidence.ambient(version="test")
    changed = AgentContextEvidence.ambient(version="changed")
    command = CommandResult(("fixture",), 0, "redacted" * 1000, "", 0.01, False)
    class AgentSpy:
        calls = 0
        def __init__(self):
            self.context = context
        def evaluate(self, candidate):
            raise AssertionError("Do not call both methods")
        def evaluate_detailed(self, candidate):
            self.calls += 1
            evidence = changed if mismatch else context
            return AgentEvaluation(P, AgentRunResult(command, None, evidence), command, evidence)
    base = AgentSpy()
    session = RepeatedOracle.from_trials(AgentTrials(base, include_summary=True),
        StochasticPolicy(1,1,1), max_total_trials=1)
    if mismatch:
        with pytest.raises(TypeError):
            session.evaluate(None)
        assert session.snapshot().observed_pass_trials == 1
        assert session.snapshot().committed_pass_trials == 0
    else:
        result = session.evaluate_detailed(None)
        assert result.context is session.context is context
        assert result.local_launch_counts == LaunchCounts(1,1)
        assert len(result.records[0].summary) <= 2048
        assert "all repeated trials and the entire reduction" in result.qualification
        assert not result.context.native_controlled_verified
    assert base.calls == 1
    identities(session.snapshot())


def test_summary_cannot_change_outcome_or_context():
    class Source:
        context = None
        def invoke(self, candidate): return RawTrial(F)
        def summarize(self, raw): return TrialObservation(P)
    session = RepeatedOracle.from_trials(Source(), StochasticPolicy(1,1,1), max_total_trials=1)
    with pytest.raises(TypeError): session.evaluate(None)
    assert session.snapshot().observed_fail_trials == 1
    assert session.snapshot().committed_pass_trials == 0
    identities(session.snapshot())


@pytest.mark.parametrize("pre", [None, LaunchCounts(), LaunchCounts(0,None), RuntimeError("pre fixture")])
def test_failed_pre_observer_has_no_charge_or_call(pre):
    base = Scripted([F])
    def observe():
        if isinstance(pre, BaseException): raise pre
        return pre
    session = RepeatedOracle(base, StochasticPolicy(1,1,1), max_total_trials=1, launch_observer=observe)
    with pytest.raises((TypeError, RuntimeError)):
        session.evaluate(None)
    c = session.snapshot()
    assert base.calls == c.underlying_trial_attempts == c.budget.used == 0
    assert c.logical_candidate_evaluations == c.aborted_candidate_evaluations == 1
    assert c.launch_counts == LaunchCounts(0,0)
    identities(c)


@pytest.mark.parametrize("after", [None, LaunchCounts(), LaunchCounts(4,4), RuntimeError("post fixture")])
def test_failed_post_observer_preserves_raw_outcome_and_budget(after):
    observations = iter([LaunchCounts(5,5), after])
    def observe():
        value = next(observations)
        if isinstance(value, BaseException): raise value
        return value
    base = Scripted([P])
    session = RepeatedOracle(base, StochasticPolicy(1,1,1), max_total_trials=1, launch_observer=observe)
    with pytest.raises((TypeError, RuntimeError)):
        session.evaluate(None)
    c = session.snapshot()
    assert base.calls == c.underlying_trial_attempts == c.budget.used == 1
    assert c.observed_pass_trials == 1 and c.committed_pass_trials == c.observed_error_trials == 0
    assert c.postprocessing_failed_attempts == c.unreturned_attempts == 1
    assert c.launch_counts.agent is c.launch_counts.verifier is None
    identities(c)


@pytest.mark.parametrize("failure", [RuntimeError("original fixture"), KeyboardInterrupt(), SystemExit(4)])
def test_secondary_observer_failure_never_replaces_underlying_exception(failure):
    reads = 0
    def observe():
        nonlocal reads
        reads += 1
        if reads == 2: raise RuntimeError("secondary sentinel must not be copied")
        return LaunchCounts(0,0)
    base = Scripted([failure])
    session = RepeatedOracle(base, StochasticPolicy(1,1,1), max_total_trials=1, launch_observer=observe)
    with pytest.raises(type(failure)) as raised:
        session.evaluate(None)
    assert raised.value is failure
    assert failure.__notes__ == ["Secondary launch observation failed."]
    assert base.calls == session.budget.used == 1 and reads == 2
    assert session.last_evaluation is None
    assert session.snapshot().raised_trial_attempts == 1
    identities(session.snapshot())


def test_observer_proves_exact_launches_even_when_underlying_raises():
    observed = iter([LaunchCounts(4,8), LaunchCounts(5,8)])
    failure = KeyboardInterrupt()
    session = RepeatedOracle(Scripted([failure]), StochasticPolicy(1,1,1), max_total_trials=1,
        launch_observer=lambda: next(observed))
    with pytest.raises(KeyboardInterrupt): session.evaluate(None)
    assert session.snapshot().launch_counts == LaunchCounts(1,0)
    identities(session.snapshot())


def test_summary_failure_can_retain_independent_launch_delta():
    class Source:
        context = None
        def invoke(self, candidate): return RawTrial(P)
        def summarize(self, raw): raise RuntimeError("summary fixture")
    observations = iter([LaunchCounts(1,2), LaunchCounts(2,3)])
    session = RepeatedOracle.from_trials(Source(), StochasticPolicy(1,1,1), max_total_trials=1,
        launch_observer=lambda: next(observations))
    with pytest.raises(RuntimeError): session.evaluate(None)
    assert session.snapshot().launch_counts == LaunchCounts(1,1)
    assert session.snapshot().observed_pass_trials == 1
    identities(session.snapshot())


def test_observer_cannot_contradict_proven_source_count():
    class Source:
        context = None
        def invoke(self, candidate): return RawTrial(F)
        def summarize(self, raw): return TrialObservation(F, LaunchCounts(1,1))
    session = RepeatedOracle.from_trials(Source(), StochasticPolicy(1,1,1), max_total_trials=1,
        launch_observer=lambda: LaunchCounts(0,0))
    with pytest.raises(TypeError, match="contradicts"):
        session.evaluate(None)
    c = session.snapshot()
    assert c.observed_fail_trials == 1 and c.committed_fail_trials == 0
    assert c.launch_counts == LaunchCounts(None,None,1,1)
    identities(c)


def test_ephemeral_raw_payload_and_prior_last_are_released():
    import gc
    import weakref
    payload_refs = []
    class Payload: pass
    class Source:
        context = None
        def invoke(self, candidate):
            payload = Payload()
            payload.large_output = "x" * 200000
            payload_refs.append(weakref.ref(payload))
            return RawTrial(F, payload)
        def summarize(self, raw): return TrialObservation(F)
    session = RepeatedOracle.from_trials(Source(), StochasticPolicy(1,1,1), max_total_trials=2)
    first = session.evaluate_detailed("first")
    previous = weakref.ref(first)
    assert session.evaluate("second") is F
    del first
    gc.collect()
    assert previous() is None and all(ref() is None for ref in payload_refs)
    assert session.last_evaluation.records == (TrialObservation(F),)


def test_fake_context_is_never_promoted_to_native_proof():
    from agentreducer.agents import ContextAssurance
    context = AgentContextEvidence(assurance=ContextAssurance.FAKE_FIXTURE,
        version="stochastic-fake", unknown_sources=())
    base = Scripted([F])
    base.context = context
    session = RepeatedOracle(base, StochasticPolicy(1,1,1), max_total_trials=1)
    result = session.evaluate_detailed(None)
    assert result.context is result.counters.context is context
    assert result.records[0].context is context
    assert "fake-fixture" in result.qualification
    assert not context.native_controlled_verified


def test_malformed_raw_source_is_invalid_without_observed_enum():
    class Source:
        context = None
        def invoke(self, candidate): return object()
        def summarize(self, raw): raise AssertionError("Invalid raw should not be summarized")
    session = RepeatedOracle.from_trials(Source(), StochasticPolicy(1,1,1), max_total_trials=1)
    with pytest.raises(TypeError): session.evaluate(None)
    assert session.snapshot().invalid_trial_attempts == 1
    assert session.snapshot().observed_pass_trials == session.snapshot().observed_fail_trials == 0
    identities(session.snapshot())


@pytest.mark.parametrize("post_failure", [None, RuntimeError("post fixture"), KeyboardInterrupt()])
def test_failed_post_observer_retains_completed_agent_command_lower_bounds(post_failure):
    from agentreducer.agents import AgentEvaluation, AgentRunResult
    from agentreducer.execution import CommandResult
    context = AgentContextEvidence.ambient(version="review-fixture")
    command = CommandResult(("fixture",),0,"","",0.01,False)
    class AgentSpy:
        calls = 0
        def __init__(self): self.context = context
        def evaluate_detailed(self,candidate):
            self.calls += 1
            return AgentEvaluation(P,AgentRunResult(command,None,context),command,context)
    snapshots = iter([LaunchCounts(0,0),post_failure])
    def observe():
        value = next(snapshots)
        if isinstance(value,BaseException): raise value
        return value
    base = AgentSpy()
    session = RepeatedOracle.from_trials(AgentTrials(base),StochasticPolicy(1,1,1),
        max_total_trials=1,launch_observer=observe)
    with pytest.raises(TypeError if post_failure is None else type(post_failure)) as raised:
        session.evaluate(None)
    if post_failure is not None: assert raised.value is post_failure
    c = session.snapshot()
    assert c.launch_counts == LaunchCounts(None,None,1,1)
    assert base.calls == c.budget.used == c.underlying_trial_attempts == 1
    assert c.observed_pass_trials == c.postprocessing_failed_attempts == c.unreturned_attempts == 1
    assert c.committed_pass_trials == c.observed_error_trials == 0
    assert session.last_evaluation is None
    identities(c)


@pytest.mark.parametrize("annotation_failure", [RuntimeError("annotation fixture"),KeyboardInterrupt(),SystemExit(8)])
def test_failing_secondary_annotation_cannot_replace_original_cancellation(annotation_failure):
    class Cancel(KeyboardInterrupt):
        def add_note(self,note): raise annotation_failure
    original = Cancel()
    snapshots = iter([LaunchCounts(0,0),None])
    base = Scripted([original])
    session = RepeatedOracle(base,StochasticPolicy(1,1,1),max_total_trials=1,
        launch_observer=lambda:next(snapshots))
    with pytest.raises(KeyboardInterrupt) as raised:
        session.evaluate(None)
    assert raised.value is original
    assert base.calls == session.budget.used == 1
    assert session.last_evaluation is None
    assert session.snapshot().raised_trial_attempts == session.snapshot().unreturned_attempts == 1
    identities(session.snapshot())


def test_nonlist_existing_notes_cannot_break_cancellation_preservation():
    original = KeyboardInterrupt()
    original.__notes__ = ()
    snapshots = iter([LaunchCounts(0,0),None])
    session = RepeatedOracle(Scripted([original]),StochasticPolicy(1,1,1),max_total_trials=1,
        launch_observer=lambda:next(snapshots))
    with pytest.raises(KeyboardInterrupt) as raised: session.evaluate(None)
    assert raised.value is original and session.budget.used == 1
    identities(session.snapshot())
