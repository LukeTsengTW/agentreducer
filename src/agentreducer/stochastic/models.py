"""Bounded immutable empirical observations and honest execution counts."""

from dataclasses import dataclass, field, fields
from enum import Enum
from fractions import Fraction
from typing import Generic, TypeVar

from ..agents.context import AgentContextEvidence, CodexContextMode
from ..models import OracleResult
from .policy import StochasticPolicy


def _count(value, name):
    if type(value) is not int:
        raise TypeError(f"{name} must be a strict integer.")
    if value < 0:
        raise ValueError(f"{name} must be nonnegative.")


def _context(value):
    if value is not None and not isinstance(value, AgentContextEvidence):
        raise TypeError("context must be AgentContextEvidence or None.")


def qualification(context):
    text = ("Empirical finite-horizon policy; no confidence or iid guarantee. "
            "A completed reduction's minimality refers to retained FAIL and single-deletion "
            "PASS observations during that reduction, relative to the policy, "
            "not deterministic or global minimality. "
            "Early decisions concern hypothetical PASS/FAIL completions only; "
            "unexecuted ERRORs are unknown.")
    if context is not None:
        text += " " + context.qualification
        if context.mode is CodexContextMode.AMBIENT:
            text += (" Uncontrolled, unfrozen ambient context must remain stable across "
                     "all repeated trials and the entire reduction.")
    else:
        text += " No agent context evidence was provided."
    return text


class StopReason(Enum):
    TARGET_REACHED = "target-reached"
    THRESHOLD_UNREACHABLE = "threshold-unreachable"
    FULL_HORIZON = "full-horizon"
    TRIAL_ERROR = "trial-error"
    BUDGET_EXHAUSTED = "budget-exhausted"


@dataclass(frozen=True)
class LaunchCounts:
    agent: int | None = None
    verifier: int | None = None
    agent_lower_bound: int = 0
    verifier_lower_bound: int = 0

    def __post_init__(self):
        for stage in ("agent", "verifier"):
            exact, lower = getattr(self, stage), getattr(self, stage + "_lower_bound")
            _count(lower, stage + "_lower_bound")
            if exact is not None:
                _count(exact, stage)
                if exact < lower:
                    raise ValueError("Exact count contradicts lower bound.")
                object.__setattr__(self, stage + "_lower_bound", exact)

    def __add__(self, other):
        if not isinstance(other, LaunchCounts):
            return NotImplemented
        def exact(left, right):
            return None if left is None or right is None else left + right
        return LaunchCounts(exact(self.agent, other.agent), exact(self.verifier, other.verifier),
                            self.agent_lower_bound + other.agent_lower_bound,
                            self.verifier_lower_bound + other.verifier_lower_bound)


@dataclass(frozen=True)
class BudgetSnapshot:
    limit: int
    used: int

    def __post_init__(self):
        _count(self.limit, "limit")
        _count(self.used, "used")
        if not self.limit or self.used > self.limit:
            raise ValueError("Require 0 <= used <= positive limit.")

    @property
    def remaining(self):
        return self.limit - self.used


@dataclass(frozen=True)
class TrialObservation:
    outcome: OracleResult
    launch_counts: LaunchCounts = field(default_factory=LaunchCounts)
    context: AgentContextEvidence | None = None
    stage: str | None = None
    summary: str | None = None
    reference: str | None = None

    def __post_init__(self):
        if not isinstance(self.outcome, OracleResult):
            raise TypeError("outcome must be OracleResult.")
        if not isinstance(self.launch_counts, LaunchCounts):
            raise TypeError("launch_counts must be LaunchCounts.")
        _context(self.context)
        for name, limit in (("stage", 128), ("summary", 2048), ("reference", 256)):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, str):
                    raise TypeError(f"{name} must be a string or None.")
                if len(value) > limit:
                    raise ValueError(f"{name} exceeds the bounded record limit.")


@dataclass(frozen=True)
class RawTrial:
    """Ephemeral adapter transport. Never retained in stochastic results."""

    outcome: object
    payload: object = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class StochasticCounters:
    budget: BudgetSnapshot
    context: AgentContextEvidence | None = None
    launch_counts: LaunchCounts = field(default_factory=lambda: LaunchCounts(0, 0))
    logical_candidate_evaluations: int = 0
    underlying_trial_attempts: int = 0
    observed_pass_trials: int = 0
    observed_fail_trials: int = 0
    observed_error_trials: int = 0
    committed_pass_trials: int = 0
    committed_fail_trials: int = 0
    committed_error_trials: int = 0
    unreturned_attempts: int = 0
    raised_trial_attempts: int = 0
    invalid_trial_attempts: int = 0
    postprocessing_failed_attempts: int = 0
    classified_pass_candidates: int = 0
    classified_fail_candidates: int = 0
    classified_error_candidates: int = 0
    aborted_candidate_evaluations: int = 0

    def __post_init__(self):
        if not isinstance(self.budget, BudgetSnapshot) or not isinstance(self.launch_counts, LaunchCounts):
            raise TypeError("Counters require immutable budget and launch snapshots.")
        _context(self.context)
        for item in fields(self):
            if item.name not in {"budget", "context", "launch_counts"}:
                _count(getattr(self, item.name), item.name)


@dataclass(frozen=True)
class StochasticEvaluation:
    outcome: OracleResult
    policy: StochasticPolicy
    records: tuple[TrialObservation, ...]
    stop_reason: StopReason
    budget_before: BudgetSnapshot
    budget_after: BudgetSnapshot
    counters: StochasticCounters
    local_launch_counts: LaunchCounts
    context: AgentContextEvidence | None
    qualification: str

    def __post_init__(self):
        records = tuple(self.records)
        if not isinstance(self.outcome, OracleResult) or not isinstance(self.stop_reason, StopReason):
            raise TypeError("Evaluation requires outcome and stop-reason enums.")
        if not isinstance(self.policy, StochasticPolicy):
            raise TypeError("policy must be StochasticPolicy.")
        if any(not isinstance(record, TrialObservation) for record in records):
            raise TypeError("records must contain TrialObservation.")
        if len(records) > self.policy.max_trials:
            raise ValueError("Records exceed the fixed horizon.")
        _context(self.context)
        object.__setattr__(self, "records", records)

    @property
    def target_failures(self):
        return self.policy.target_failures

    @property
    def trials_executed(self):
        return len(self.records)

    @property
    def pass_trials(self):
        return sum(record.outcome is OracleResult.PASS for record in self.records)

    @property
    def fail_trials(self):
        return sum(record.outcome is OracleResult.FAIL for record in self.records)

    @property
    def error_trials(self):
        return sum(record.outcome is OracleResult.ERROR for record in self.records)

    @property
    def observed_failure_rate(self):
        total = self.pass_trials + self.fail_trials
        return Fraction(self.fail_trials, total) if total else None

    @property
    def stopped_early(self):
        return self.trials_executed < self.policy.max_trials

    @property
    def decision_complete(self):
        return self.outcome in (OracleResult.PASS, OracleResult.FAIL)


R = TypeVar("R")


@dataclass(frozen=True)
class StochasticReductionResult(Generic[R]):
    reduction: R
    policy: StochasticPolicy
    counters: StochasticCounters
    budget: BudgetSnapshot
    context: AgentContextEvidence | None
    qualification: str
