"""Prompt reduction using the unchanged M0 ddmin engine."""

from collections.abc import Sequence

from .._counting import CountingOracle
from ..models import OracleResult
from ..reducers import ddmin
from .models import (
    PromptBlock,
    PromptCandidate,
    PromptReductionResult,
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
        def evaluate(blocks: Sequence[PromptBlock]) -> OracleResult:
            return oracle.evaluate(PromptCandidate(tuple(blocks), candidate.mode))

        block_oracle = CountingOracle(evaluate)
        blocks = ddmin(candidate.blocks, block_oracle)
        return PromptReductionResult(
            original=candidate,
            reduced=PromptCandidate(tuple(blocks), candidate.mode),
            evaluations=block_oracle.evaluations,
        )
