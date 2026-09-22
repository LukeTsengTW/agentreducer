"""Prompt reduction using the unchanged M0 ddmin engine."""

from collections.abc import Sequence

from ..models import OracleResult
from ..reducers import ddmin
from .models import (
    PromptBlock,
    PromptCandidate,
    PromptReductionResult,
    PromptSplitMode,
)
from .oracle import PromptEvaluator


class PromptReducer:
    """Reduce whole blocks to a 1-minimal candidate; retain M0 error semantics."""

    def reduce(
        self, candidate: PromptCandidate, oracle: PromptEvaluator
    ) -> PromptReductionResult:
        """Return the reduced prompt and the actual number of evaluations made.

        Each call uses its own counter. Initial and empty-candidate checks count;
        no extra oracle calls are made to calculate statistics. Errors propagate
        without returning a partial result.
        """
        block_oracle = _CountingBlockOracle(oracle, candidate.mode)
        blocks = ddmin(candidate.blocks, block_oracle)
        return PromptReductionResult(
            original=candidate,
            reduced=PromptCandidate(tuple(blocks), candidate.mode),
            evaluations=block_oracle.evaluations,
        )


class _CountingBlockOracle:
    """Adapt M0's sequence oracle contract to structured prompts for one run."""

    def __init__(self, oracle: PromptEvaluator, mode: PromptSplitMode) -> None:
        self.oracle = oracle
        self.mode = mode
        self.evaluations = 0

    def evaluate(self, candidate: Sequence[PromptBlock]) -> OracleResult:
        prompt = PromptCandidate(tuple(candidate), self.mode)
        self.evaluations += 1
        return self.oracle.evaluate(prompt)
