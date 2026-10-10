"""Block-level instruction reduction via the unchanged generic ddmin engine."""

from collections.abc import Sequence

from .._counting import CountingOracle
from ..models import OracleResult
from ..reducers import ddmin
from .models import InstructionBlock, InstructionCandidate, InstructionReductionResult
from .oracle import InstructionEvaluator


class InstructionReducer:
    """Find a 1-minimal failing subsequence of whole instruction blocks."""

    def reduce(
        self, candidate: InstructionCandidate, oracle: InstructionEvaluator
    ) -> InstructionReductionResult:
        """Count every oracle call; preserve M0 PASS/ERROR/exception behavior."""
        def evaluate(blocks: Sequence[InstructionBlock]) -> OracleResult:
            return oracle.evaluate(InstructionCandidate(tuple(blocks)))

        block_oracle = CountingOracle(evaluate)
        blocks = ddmin(candidate.blocks, block_oracle)
        return InstructionReductionResult(
            original=candidate,
            reduced=InstructionCandidate(tuple(blocks)),
            evaluations=block_oracle.evaluations,
        )
