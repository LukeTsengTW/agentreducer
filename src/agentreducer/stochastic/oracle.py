"""Serial fresh sampling with exact threshold arithmetic and hard call budget."""

from dataclasses import fields, replace
from threading import Lock
from typing import Generic, TypeVar

from ..models import OracleResult
from .adapters import OrdinaryTrials
from .budget import TrialBudget
from .models import (
    LaunchCounts, RawTrial, StochasticCounters, StochasticEvaluation, StopReason,
    TrialObservation, _context, qualification,
)
from .policy import StochasticPolicy

T = TypeVar("T")


class RepeatedOracle(Generic[T]):
    """One session owns a finite nonrefundable trial budget and at most one result.

    Sources/observers are trusted: invoke performs one fresh underlying call;
    summarize and the observer perform no evaluations. A budget bounds source
    attempts, not opaque source-internal or backend requests.
    """

    def __init__(self, oracle, policy, *, max_total_trials, launch_observer=None):
        self._prepare(OrdinaryTrials(oracle), policy, max_total_trials, launch_observer)

    @classmethod
    def from_trials(cls, source, policy, *, max_total_trials, launch_observer=None):
        session = cls.__new__(cls)
        session._prepare(source, policy, max_total_trials, launch_observer)
        return session

    def _prepare(self, source, policy, limit, observer):
        if not isinstance(policy, StochasticPolicy):
            raise TypeError("policy must be StochasticPolicy.")
        budget = TrialBudget(limit)
        if limit < policy.min_trials:
            raise ValueError("Budget must permit at least min_trials.")
        if observer is not None and not callable(observer):
            raise TypeError("launch_observer must be callable.")
        context = source.context
        _context(context)
        self._source, self._policy, self._budget = source, policy, budget
        self._observer, self._context = observer, context
        self._guard = Lock()
        self._last_evaluation = None
        self._launch_counts = LaunchCounts(0, 0)
        self._counts = {item.name: 0 for item in fields(StochasticCounters)
                        if item.name not in {"budget", "context", "launch_counts"}}

    @property
    def policy(self):
        return self._policy

    @property
    def context(self):
        return self._context

    @property
    def budget(self):
        return self._budget.snapshot()

    @property
    def last_evaluation(self):
        return self._last_evaluation

    @property
    def qualification(self):
        return qualification(self.context)

    def snapshot(self):
        return StochasticCounters(budget=self.budget, context=self.context,
                                  launch_counts=self._launch_counts, **self._counts)

    def evaluate(self, candidate: T) -> OracleResult:
        return self.evaluate_detailed(candidate).outcome

    def evaluate_detailed(self, candidate: T) -> StochasticEvaluation:
        if not self._guard.acquire(blocking=False):
            raise RuntimeError("Concurrent or reentrant use of one session is unsupported.")
        try:
            self._last_evaluation = None
            self._counts["logical_candidate_evaluations"] += 1
            records = []
            before = self.budget
            launches = LaunchCounts(0, 0)

            def finish(outcome, reason):
                self._counts["classified_" + outcome.name.lower() + "_candidates"] += 1
                result = StochasticEvaluation(outcome, self.policy, tuple(records), reason,
                    before, self.budget, self.snapshot(), launches, self.context, self.qualification)
                self._last_evaluation = result
                return result

            try:
                if before.remaining < self.policy.min_trials:
                    return finish(OracleResult.ERROR, StopReason.BUDGET_EXHAUSTED)
                failures = 0
                while True:
                    if not self.budget.remaining:
                        return finish(OracleResult.ERROR, StopReason.BUDGET_EXHAUSTED)
                    observation = self._trial(candidate)
                    if observation is None:
                        return finish(OracleResult.ERROR, StopReason.BUDGET_EXHAUSTED)
                    records.append(observation)
                    launches += observation.launch_counts
                    if observation.outcome is OracleResult.ERROR:
                        return finish(OracleResult.ERROR, StopReason.TRIAL_ERROR)
                    failures += observation.outcome is OracleResult.FAIL
                    n = len(records)
                    if n < self.policy.min_trials:
                        continue
                    if n == self.policy.max_trials:
                        outcome = OracleResult.FAIL if failures >= self.policy.target_failures else OracleResult.PASS
                        return finish(outcome, StopReason.FULL_HORIZON)
                    if failures >= self.policy.target_failures:
                        return finish(OracleResult.FAIL, StopReason.TARGET_REACHED)
                    if failures + self.policy.max_trials - n < self.policy.target_failures:
                        return finish(OracleResult.PASS, StopReason.THRESHOLD_UNREACHABLE)
            except BaseException:
                self._counts["aborted_candidate_evaluations"] += 1
                raise
        finally:
            self._guard.release()

    def _observe(self):
        value = self._observer()
        if not isinstance(value, LaunchCounts) or value.agent is None or value.verifier is None:
            raise TypeError("Observer must provide exact LaunchCounts snapshots.")
        return value

    @staticmethod
    def _delta(before, after):
        if after.agent < before.agent or after.verifier < before.verifier:
            raise TypeError("Observer counters must be monotonic.")
        return LaunchCounts(after.agent - before.agent, after.verifier - before.verifier)

    @staticmethod
    def _reconcile(observed, evidence):
        for stage in ("agent", "verifier"):
            exact = getattr(evidence, stage)
            lower = getattr(evidence, stage + "_lower_bound")
            measured = getattr(observed, stage)
            if measured < lower or exact is not None and measured != exact:
                raise TypeError("Observer contradicts source launch evidence.")
        return observed

    def _trial(self, candidate):
        pre = self._observe() if self._observer is not None else None
        if not self._budget._reserve():
            return None
        self._counts["underlying_trial_attempts"] += 1
        status = "raised_trial_attempts"
        launches = LaunchCounts()
        raw = None
        observation = None
        primary = None
        try:
            try:
                raw = self._source.invoke(candidate)
                status = "invalid_trial_attempts"
                if not isinstance(raw, RawTrial) or not isinstance(raw.outcome, OracleResult):
                    raise TypeError("Trial source must return a raw OracleResult.")
                self._counts["observed_" + raw.outcome.name.lower() + "_trials"] += 1
                status = "postprocessing_failed_attempts"
                # Metadata is observation-only. Extract proven launch evidence
                # before the fallible post-observer, while raw outcome is already
                # counted and no TrialObservation has yet been committed.
                observation = self._source.summarize(raw)
                if not isinstance(observation, TrialObservation):
                    raise TypeError("Trial source must summarize a TrialObservation.")
                launches = observation.launch_counts
                if observation.outcome is not raw.outcome:
                    raise TypeError("Summary cannot change the underlying outcome.")
                if observation.context != self.context:
                    raise TypeError("Trial context differs from the captured session context.")
            except BaseException as exc:
                primary = exc
                raise
            finally:
                if pre is not None:
                    try:
                        delta = self._delta(pre, self._observe())
                        launches = self._reconcile(delta, launches)
                    except BaseException:
                        launches = LaunchCounts(None, None, launches.agent_lower_bound,
                                                launches.verifier_lower_bound)
                        if primary is not None:
                            try:
                                primary.add_note("Secondary launch observation failed.")
                            except BaseException:
                                # Annotation is best effort, including custom
                                # cancellation objects and malformed __notes__.
                                pass
                        else:
                            raise
            observation = replace(observation, launch_counts=launches, context=self.context)
            self._counts["committed_" + raw.outcome.name.lower() + "_trials"] += 1
            status = None
            return observation
        finally:
            if status is not None:
                self._counts[status] += 1
                self._counts["unreturned_attempts"] += 1
            self._launch_counts += launches
            raw = None
