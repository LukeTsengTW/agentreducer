"""Structured instruction and rendered-text oracle contracts."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from ..models import OracleResult
from .models import InstructionCandidate


class InstructionEvaluator(Protocol):
    """Evaluate immutable candidates with stable outcomes."""

    def evaluate(self, candidate: InstructionCandidate) -> OracleResult:
        ...


@dataclass(frozen=True)
class InstructionOracle:
    """Render the candidate and forward a text evaluator's outcome unchanged."""

    evaluator: Callable[[str], OracleResult]

    def evaluate(self, candidate: InstructionCandidate) -> OracleResult:
        return self.evaluator(candidate.render())
