"""Reduction of one Markdown instruction document supplied as text."""

from .models import (
    InstructionBlock,
    InstructionBlockKind,
    InstructionCandidate,
    InstructionHeading,
    InstructionReductionResult,
)
from .oracle import InstructionEvaluator, InstructionOracle
from .parser import parse_instructions
from .reducer import InstructionReducer

__all__ = [
    "InstructionBlock",
    "InstructionBlockKind",
    "InstructionCandidate",
    "InstructionEvaluator",
    "InstructionHeading",
    "InstructionOracle",
    "InstructionReducer",
    "InstructionReductionResult",
    "parse_instructions",
]
