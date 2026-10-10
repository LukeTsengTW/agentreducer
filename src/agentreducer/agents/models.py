"""Immutable agent execution diagnostics and qualified reduction results."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, TypeVar

from ..execution.models import CommandResult
from ..models import OracleResult
from .context import AgentContextEvidence


class AgentStage(Enum):
    WORKSPACE = "workspace"
    MATERIALIZATION = "materialization"
    PROMPT = "prompt"
    AGENT = "agent"
    VERIFIER = "verifier"
    POLICY = "policy"
    CLEANUP = "cleanup"


@dataclass(frozen=True)
class AgentRunResult:
    command: CommandResult | None = None
    error: str | None = None
    context: AgentContextEvidence = field(default_factory=AgentContextEvidence)

    def __post_init__(self):
        if self.command is not None and not isinstance(self.command, CommandResult):
            raise TypeError("command must be a CommandResult or None.")
        if self.error is not None and not isinstance(self.error, str):
            raise TypeError("error must be a string or None.")
        if not isinstance(self.context, AgentContextEvidence):
            raise TypeError("context must be AgentContextEvidence.")

    @property
    def succeeded(self) -> bool:
        return (self.command is not None and type(self.command.exit_code) is int
                and self.command.exit_code == 0 and not self.command.timed_out
                and self.error is None)


@dataclass(frozen=True)
class AgentEvaluation:
    outcome: OracleResult
    agent_result: AgentRunResult | None
    verifier_result: CommandResult | None
    context: AgentContextEvidence
    error_stage: AgentStage | None = None
    error: str | None = None


R = TypeVar("R")


@dataclass(frozen=True)
class AgentReductionResult(Generic[R]):
    reduction: R
    context: AgentContextEvidence
