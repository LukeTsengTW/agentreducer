"""Oracle outcomes and errors that prevent a valid reduction."""

from collections.abc import Sequence
from enum import Enum


class OracleResult(Enum):
    """PASS: no reproduction; FAIL: reproduction; ERROR: evaluation was invalid."""

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


class InitialCandidateNotFailingError(ValueError):
    """The initial candidate returned PASS, so there is no failure to reduce."""


class OracleEvaluationError(RuntimeError):
    """The oracle returned ERROR; candidate contains the unevaluable input."""

    def __init__(self, candidate: Sequence[object]) -> None:
        self.candidate: tuple[object, ...] = tuple(candidate)
        super().__init__(
            f"Oracle returned ERROR for a candidate with {len(self.candidate)} items."
        )
