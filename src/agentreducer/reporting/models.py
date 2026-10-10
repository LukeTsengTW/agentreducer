"""Immutable JSON-safe presentation values; no arbitrary-object serialization."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
import math


def freeze(value):
    if value is None or type(value) in (str, bool, int):
        if isinstance(value, str):
            value.encode("utf-8")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Nonfinite report number")
        return value
    if isinstance(value, Mapping):
        if any(type(k) is not str for k in value):
            raise TypeError("Report keys must be strings")
        return MappingProxyType({k:freeze(v) for k,v in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(freeze(v) for v in value)
    raise TypeError("Unsupported report value")


def count(value, *, optional=False):
    if optional and value is None:
        return
    if type(value) is not int or value < 0:
        raise ValueError("Count must be a nonnegative strict integer")


@dataclass(frozen=True)
class CountEvidence:
    value: int | None
    lower_bound: int
    basis: str

    def __post_init__(self):
        count(self.value,optional=True); count(self.lower_bound)
        if self.value is not None and self.value < self.lower_bound:
            raise ValueError("Lower bound exceeds known count")
        if type(self.basis) is not str or not self.basis or any(ord(c)<32 for c in self.basis):
            raise ValueError("Invalid count basis")

    def __add__(self, other):
        if not isinstance(other,CountEvidence):
            return NotImplemented
        known = self.value + other.value if self.value is not None and other.value is not None else None
        return CountEvidence(known,self.lower_bound+other.lower_bound,
            self.basis if self.basis == other.basis else "mixed-public-evidence")


@dataclass(frozen=True)
class CostSnapshot:
    logical_evaluations: int = 0
    underlying_trial_attempts: int | None = None
    agent_launches: CountEvidence = field(default_factory=lambda:CountEvidence(0,0,"known-no-agent"))
    verifier_launches: CountEvidence = field(default_factory=lambda:CountEvidence(0,0,"returned-command-results"))
    budget: Mapping | None = None
    stochastic_counters: Mapping | None = None
    projection_attempts: int | None = None
    completed_projection_checks: int | None = None
    elapsed_seconds: float = 0.0

    def __post_init__(self):
        count(self.logical_evaluations)
        for name in ("underlying_trial_attempts","projection_attempts","completed_projection_checks"):
            count(getattr(self,name),optional=True)
        if not isinstance(self.agent_launches,CountEvidence) or not isinstance(self.verifier_launches,CountEvidence):
            raise TypeError("Count evidence required")
        if type(self.elapsed_seconds) not in (int,float) or not math.isfinite(self.elapsed_seconds) or self.elapsed_seconds<0:
            raise ValueError("Invalid elapsed duration")
        for name in ("budget","stochastic_counters"):
            value=getattr(self,name)
            if value is not None and not isinstance(value,Mapping):
                raise TypeError("Cost metadata must be a mapping")
            object.__setattr__(self,name,freeze(value))
        if (self.projection_attempts is not None and self.completed_projection_checks is not None
                and self.completed_projection_checks>self.projection_attempts):
            raise ValueError("Completed projections exceed attempts")
        if self.budget is not None:
            if set(self.budget)!={"limit","used","remaining"}:
                raise ValueError("Invalid budget fields")
            for value in self.budget.values():
                count(value)
            if self.budget['limit']<1 or self.budget['used']>self.budget['limit'] or self.budget['remaining']!=self.budget['limit']-self.budget['used']:
                raise ValueError("Inconsistent trial budget")
        if self.stochastic_counters is not None:
            for value in self.stochastic_counters.values():
                count(value)


_EXITS = {"reduced":0,"invalid-configuration":2,"initial-not-failing":3,
    "oracle-error":4,"stochastic-error":5,"internal-error":6,"output-error":7,"cancelled":130}


@dataclass(frozen=True)
class RunReport:
    domain: str | None
    status: str
    exit_code: int
    experiment: Mapping | None
    result: Mapping | None
    context: Mapping
    cost: CostSnapshot
    limitations: tuple[Mapping,...] = ()
    error: Mapping | None = None

    def __post_init__(self):
        if type(self.exit_code) is not int or self.domain not in (None,"prompt","instructions","repository") or _EXITS.get(self.status) != self.exit_code:
            raise ValueError("Invalid report status")
        if (self.status == "reduced") != (self.result is not None):
            raise ValueError("Only completed reductions have results")
        if set(self.context) != {"agent","stochastic","repository_visibility"}:
            raise ValueError("Invalid context fields")
        if not isinstance(self.cost,CostSnapshot):
            raise TypeError("CostSnapshot required")
        for name in ("experiment","result","context","error"):
            value=getattr(self,name)
            if value is not None and not isinstance(value,Mapping):
                raise TypeError("Mapping required")
            object.__setattr__(self,name,freeze(value))
        if any(not isinstance(x,Mapping) or set(x)!={"code","message"}
               or any(type(v) is not str for v in x.values()) for x in self.limitations):
            raise TypeError("Limitations require code and message")
        object.__setattr__(self,"limitations",freeze(tuple(self.limitations)))
