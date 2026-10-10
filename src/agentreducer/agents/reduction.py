"""Thin metadata wrappers around the unchanged deterministic reducers."""

from ..instructions import InstructionReducer
from ..prompt import PromptReducer
from .context import AgentContextEvidence
from .models import AgentReductionResult


def _context(oracle):
    context = oracle.context
    if not isinstance(context, AgentContextEvidence):
        raise TypeError("Qualified reduction requires AgentContextEvidence.")
    return context


def reduce_prompt(candidate, oracle):
    """Delegate once; metadata reads perform no extra oracle evaluations."""
    context = _context(oracle)
    return AgentReductionResult(PromptReducer().reduce(candidate, oracle), context)


def reduce_instructions(candidate, oracle):
    """Preserve M2 headings, statistics and exceptions without extra trials."""
    context = _context(oracle)
    return AgentReductionResult(InstructionReducer().reduce(candidate, oracle), context)
