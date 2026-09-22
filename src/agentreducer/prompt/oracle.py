"""Bridge structured prompt candidates to deterministic text evaluators."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from ..models import OracleResult
from .models import PromptCandidate


class PromptEvaluator(Protocol):
    """Evaluate structured candidates with stable results, without mutation."""

    def evaluate(self, candidate: PromptCandidate) -> OracleResult:
        """Classify whether the prompt reproduces the failure of interest."""
        ...


@dataclass(frozen=True)
class PromptOracle:
    """Render a candidate and call a text evaluator, returning its outcome as-is."""

    evaluator: Callable[[str], OracleResult]

    def evaluate(self, candidate: PromptCandidate) -> OracleResult:
        return self.evaluator(candidate.render())
