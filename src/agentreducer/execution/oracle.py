"""Generic deterministic evaluation without coupling to candidate domains."""

from dataclasses import dataclass, field
from typing import Generic, TypeVar

from ..models import OracleResult
from .materializer import CandidateMaterializer
from .models import CommandEvaluation, CommandResult, CommandSpec, ExitCodePolicy
from .runner import run_command
from .workspace import WorkspaceTemplate

T = TypeVar("T")


@dataclass(frozen=True)
class CommandOracle(Generic[T]):
    """Materialize and evaluate each candidate in its own disposable copy."""

    workspace: WorkspaceTemplate
    command: CommandSpec
    materializer: CandidateMaterializer[T]
    exit_code_policy: ExitCodePolicy = field(default_factory=ExitCodePolicy)

    def evaluate(self, candidate: T) -> OracleResult:
        """Perform exactly one detailed evaluation and expose its enum outcome."""
        return self.evaluate_detailed(candidate).outcome

    def evaluate_detailed(self, candidate: T) -> CommandEvaluation:
        """Return diagnostics; ordinary setup/execution/cleanup errors are ERROR.

        KeyboardInterrupt and other cancellation BaseExceptions propagate while
        the workspace context still performs cleanup.
        """
        command: CommandResult | None = None
        try:
            with self.workspace.create() as workspace:
                self.materializer.materialize(candidate, workspace.path)
                command = run_command(self.command, cwd=workspace.path)
                if command.timed_out:
                    evaluation = CommandEvaluation(
                        OracleResult.ERROR, command,
                        f"Command timeout after {self.command.timeout_seconds} seconds.",
                    )
                else:
                    outcome = self.exit_code_policy.classify(command.exit_code)
                    if not isinstance(outcome, OracleResult):
                        raise TypeError("Exit-code policy must return an OracleResult.")
                    evaluation = CommandEvaluation(
                        outcome, command,
                        f"Unclassified exit code: {command.exit_code}"
                        if outcome is OracleResult.ERROR else None,
                    )
            return evaluation
        except Exception as exc:
            return CommandEvaluation(
                OracleResult.ERROR, command, f"{type(exc).__name__}: {exc}"
            )
