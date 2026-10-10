"""Budgeted empirical repetition of single-trial oracles."""

from .policy import StochasticPolicy
from .models import (
    BudgetSnapshot, LaunchCounts, RawTrial, StochasticCounters, StochasticEvaluation,
    StochasticReductionResult, StopReason, TrialObservation,
)
from .adapters import AgentTrials, CommandTrials, OrdinaryTrials, TrialSource
from .oracle import RepeatedOracle
from .reduction import reduce_instructions_stochastic, reduce_prompt_stochastic

__all__ = ["StochasticPolicy", "BudgetSnapshot", "LaunchCounts", "RawTrial",
           "StochasticCounters", "StochasticEvaluation", "StochasticReductionResult",
           "StopReason", "TrialObservation", "AgentTrials", "CommandTrials",
           "OrdinaryTrials", "TrialSource", "RepeatedOracle",
           "reduce_instructions_stochastic", "reduce_prompt_stochastic"]
