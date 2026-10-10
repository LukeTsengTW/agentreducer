"""Explicit one-call sources; fallible metadata follows raw-outcome capture."""

from dataclasses import dataclass, field
from typing import Generic, Protocol, TypeVar

from ..agents.context import AgentContextEvidence
from ..agents.models import AgentEvaluation, AgentRunResult, AgentStage
from ..execution.models import CommandEvaluation, CommandResult
from .models import LaunchCounts, RawTrial, TrialObservation, _context

T = TypeVar("T")


class TrialSource(Protocol[T]):
    @property
    def context(self) -> AgentContextEvidence | None: ...
    def invoke(self, candidate: T) -> RawTrial: ...
    def summarize(self, raw: RawTrial) -> TrialObservation: ...


@dataclass(frozen=True)
class OrdinaryTrials(Generic[T]):
    oracle: object = field(repr=False)
    context: AgentContextEvidence | None = field(init=False)

    def __post_init__(self):
        context = getattr(self.oracle, "context", None)
        _context(context)
        object.__setattr__(self, "context", context)

    def invoke(self, candidate: T) -> RawTrial:
        return RawTrial(self.oracle.evaluate(candidate))

    def summarize(self, raw: RawTrial) -> TrialObservation:
        return TrialObservation(raw.outcome, context=self.context)


@dataclass(frozen=True)
class CommandTrials(Generic[T]):
    """M3 diagnostics are not automatically safe for inclusion in summaries."""

    oracle: object = field(repr=False)
    include_summary: bool = False
    context: None = field(default=None, init=False)

    def __post_init__(self):
        if type(self.include_summary) is not bool:
            raise TypeError("include_summary must be bool.")

    def invoke(self, candidate: T) -> RawTrial:
        result = self.oracle.evaluate_detailed(candidate)
        return RawTrial(getattr(result, "outcome", None), result)

    def summarize(self, raw: RawTrial) -> TrialObservation:
        if not isinstance(raw.payload, CommandEvaluation):
            raise TypeError("Command trial requires CommandEvaluation.")
        return TrialObservation(raw.outcome)


@dataclass(frozen=True)
class AgentTrials(Generic[T]):
    oracle: object = field(repr=False)
    include_summary: bool = False
    context: AgentContextEvidence = field(init=False)

    def __post_init__(self):
        if type(self.include_summary) is not bool:
            raise TypeError("include_summary must be bool.")
        context = self.oracle.context
        if not isinstance(context, AgentContextEvidence):
            raise TypeError("Agent trials require AgentContextEvidence.")
        object.__setattr__(self, "context", context)

    def invoke(self, candidate: T) -> RawTrial:
        result = self.oracle.evaluate_detailed(candidate)
        return RawTrial(getattr(result, "outcome", None), result)

    def summarize(self, raw: RawTrial) -> TrialObservation:
        result = raw.payload
        if not isinstance(result, AgentEvaluation):
            raise TypeError("Agent trial requires AgentEvaluation.")
        if result.context != self.context:
            raise TypeError("Agent context changed during the trial.")
        agent = result.agent_result
        if agent is not None and not isinstance(agent, AgentRunResult):
            raise TypeError("Invalid agent diagnostics.")
        if agent is not None and agent.context != self.context:
            raise TypeError("Agent run context changed during the trial.")
        verifier = result.verifier_result
        if verifier is not None and not isinstance(verifier, CommandResult):
            raise TypeError("Invalid verifier diagnostics.")
        if result.error_stage is not None and not isinstance(result.error_stage, AgentStage):
            raise TypeError("Invalid agent stage.")
        launches = LaunchCounts(1 if agent is not None and agent.command is not None else None,
                                1 if verifier is not None else None)
        summary = None
        if self.include_summary:
            # M4 has sanitized these diagnostics already. Bound before joining.
            pieces = [result.error or "", agent.error or "" if agent else ""]
            for command in (agent.command if agent else None, verifier):
                if command is not None:
                    pieces.extend((command.stdout, command.stderr))
            summary = "\n".join(piece[:2048] for piece in pieces if piece)[:2048]
        return TrialObservation(raw.outcome, launches, self.context,
                                result.error_stage.value if result.error_stage else None, summary)
