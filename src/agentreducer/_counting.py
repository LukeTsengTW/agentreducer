"""Private per-reduction instrumentation for M0-compatible sequence oracles."""

from collections.abc import Callable, Sequence
from typing import Generic, TypeVar

from .models import OracleResult

T = TypeVar("T")


class CountingOracle(Generic[T]):
    """Count calls without interpreting outcomes or catching oracle exceptions."""

    def __init__(self, evaluate: Callable[[Sequence[T]], OracleResult]) -> None:
        self._evaluate = evaluate
        self.evaluations = 0

    def evaluate(self, candidate: Sequence[T]) -> OracleResult:
        self.evaluations += 1
        return self._evaluate(candidate)
