"""Fresh-session composition with unchanged domain reducers; no extra trials."""

from ..instructions import InstructionReducer
from ..prompt import PromptReducer
from .models import StochasticReductionResult
from .oracle import RepeatedOracle


def _fresh(session):
    if not isinstance(session, RepeatedOracle):
        raise TypeError("Reduction requires a RepeatedOracle session.")
    if session.snapshot().logical_candidate_evaluations or session.budget.used:
        raise ValueError("Reduction requires a fresh session.")


def _wrap(result, session):
    counters = session.snapshot()
    return StochasticReductionResult(result, session.policy, counters, counters.budget,
                                     session.context, session.qualification)


def reduce_prompt_stochastic(candidate, session):
    _fresh(session)
    return _wrap(PromptReducer().reduce(candidate, session), session)


def reduce_instructions_stochastic(candidate, session):
    _fresh(session)
    return _wrap(InstructionReducer().reduce(candidate, session), session)
