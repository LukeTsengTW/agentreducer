"""The structural contract for caller-supplied deterministic oracles."""

from collections.abc import Sequence
from typing import Protocol, TypeVar

from .models import OracleResult

T_contra = TypeVar("T_contra", contravariant=True)


class Oracle(Protocol[T_contra]):
    """An oracle must give stable results and must not mutate candidate items."""

    def evaluate(self, candidate: Sequence[T_contra]) -> OracleResult:
        """Classify whether this candidate reproduces the failure of interest."""
        ...
