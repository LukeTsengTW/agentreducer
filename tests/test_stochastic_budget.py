from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from threading import Barrier

import pytest

from agentreducer.stochastic.budget import TrialBudget


@pytest.mark.parametrize("limit", [None, True, False, 0, -1, 1.0, "5", float("inf")])
def test_finite_positive_strict_budget_required(limit):
    with pytest.raises((TypeError, ValueError)):
        TrialBudget(limit)


def test_budget_is_atomic_and_never_refunded():
    budget = TrialBudget(37)
    barrier = Barrier(2)
    def reserve():
        barrier.wait(timeout=5)
        return sum(budget._reserve() for _ in range(50))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reserve) for _ in range(2)]
        assert sum(f.result(timeout=10) for f in futures) == 37
    snapshot = budget.snapshot()
    assert snapshot.limit == snapshot.used == 37 and snapshot.remaining == 0
    assert not budget._reserve()
    assert not hasattr(budget, "reset") and not hasattr(budget, "refund")
    with pytest.raises(FrozenInstanceError):
        snapshot.used = 0


@pytest.mark.parametrize("limit", [None, True, 0, -1, 1.0, "5", float("inf"), 1])
def test_session_budget_must_be_explicit_and_permit_minimum(limit):
    from agentreducer.stochastic import RepeatedOracle, StochasticPolicy
    class NeverCalled:
        def evaluate(self, candidate):
            raise AssertionError("Construction must not evaluate")
    with pytest.raises((TypeError, ValueError)):
        RepeatedOracle(NeverCalled(), StochasticPolicy(2,5), max_total_trials=limit)
    with pytest.raises(TypeError):
        RepeatedOracle(NeverCalled(), StochasticPolicy())


def test_session_overlap_rejected_before_extra_trial_and_guard_recovers():
    from threading import Event
    from agentreducer import OracleResult
    from agentreducer.stochastic import RepeatedOracle, StochasticPolicy
    entered, release = Event(), Event()
    class Blocking:
        calls = 0
        def evaluate(self, candidate):
            self.calls += 1
            entered.set()
            assert release.wait(timeout=5)
            return OracleResult.FAIL
    base = Blocking()
    session = RepeatedOracle(base, StochasticPolicy(1,1,1), max_total_trials=2)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(session.evaluate, None)
        try:
            assert entered.wait(timeout=5)
            with pytest.raises(RuntimeError, match="Concurrent or reentrant"):
                session.evaluate(None)
            assert base.calls == session.budget.used == 1
        finally:
            release.set()
        assert future.result(timeout=5) is OracleResult.FAIL
    assert session.evaluate(None) is OracleResult.FAIL
    assert base.calls == session.budget.used == 2
    assert session.snapshot().logical_candidate_evaluations == 2


def test_reentrant_source_call_is_charged_once_and_inner_call_not_entered():
    from agentreducer.stochastic import RepeatedOracle, StochasticPolicy
    class Recursive:
        calls = 0
        def evaluate(self, candidate):
            self.calls += 1
            return session.evaluate(candidate)
    base = Recursive()
    session = RepeatedOracle(base, StochasticPolicy(1,1,1), max_total_trials=2)
    with pytest.raises(RuntimeError, match="Concurrent or reentrant"):
        session.evaluate(None)
    c = session.snapshot()
    assert base.calls == c.underlying_trial_attempts == c.budget.used == 1
    assert c.logical_candidate_evaluations == c.aborted_candidate_evaluations == 1
    assert c.raised_trial_attempts == c.unreturned_attempts == 1
