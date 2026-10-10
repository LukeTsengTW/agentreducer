"""Thin M4/M5 wrappers; preserve existing qualifications and add no samples."""
from dataclasses import replace

from ..agents.context import AgentContextEvidence
from ..agents.models import AgentReductionResult
from ..stochastic import RepeatedOracle
from ..stochastic.models import StochasticReductionResult
from .models import ExternalReadIsolation, RepositoryVisibilityEvidence
from .reducer import RepositoryReducer


def _agent_visibility(result, context):
    if context is None:
        return result
    visibility = RepositoryVisibilityEvidence(
        result.visibility.workspace_projection_verified, ExternalReadIsolation.NOT_GUARANTEED
    )
    return replace(result, visibility=visibility)


def reduce_repository_agent(candidate, oracle, *, projection=None):
    context = getattr(oracle, "context", None)
    if not isinstance(context, AgentContextEvidence):
        raise TypeError("Qualified repository reduction requires AgentContextEvidence.")
    result = RepositoryReducer().reduce(candidate, oracle, projection=projection)
    return AgentReductionResult(_agent_visibility(result, context), context)


def reduce_repository_stochastic(candidate, session, *, projection=None):
    if not isinstance(session, RepeatedOracle):
        raise TypeError("Repository stochastic reduction requires RepeatedOracle.")
    if session.snapshot().logical_candidate_evaluations or session.budget.used:
        raise ValueError("Repository reduction requires a fresh session.")
    result = RepositoryReducer().reduce(candidate, session, projection=projection)
    counters = session.snapshot()
    if result.evaluations != counters.logical_candidate_evaluations:
        raise RuntimeError("Repository and session classification counts differ.")
    result = _agent_visibility(result, session.context)
    return StochasticReductionResult(
        result, session.policy, counters, counters.budget, session.context,
        session.qualification + " " + result.qualification,
    )

