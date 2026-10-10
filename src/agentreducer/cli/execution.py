"""Compose unchanged domain reducers and execution layers, without extra trials."""

from dataclasses import dataclass, field

from agentreducer.execution import CommandOracle
from agentreducer.prompt import PromptReducer
from agentreducer.instructions import InstructionReducer
from agentreducer.repository import (RepositoryReducer,reduce_repository_agent,reduce_repository_stochastic)
from agentreducer.agents import (CodexSpec,CodexRunner,CodexContextMode,AgentExecutionOracle,
    reduce_prompt,reduce_instructions,AgentStage)
from agentreducer.stochastic import (AgentTrials,RepeatedOracle,reduce_prompt_stochastic,reduce_instructions_stochastic)
from agentreducer.reporting.models import CountEvidence,CostSnapshot
from agentreducer.reporting.convert import agent_context,stochastic_context
from .inputs import ConfigFailure
from ._observations import DeterministicObservationOracle,CliCommandTrials


_COUNTERS=("logical_candidate_evaluations","underlying_trial_attempts","observed_pass_trials",
    "observed_fail_trials","observed_error_trials","committed_pass_trials","committed_fail_trials",
    "committed_error_trials","unreturned_attempts","raised_trial_attempts","invalid_trial_attempts",
    "postprocessing_failed_attempts","classified_pass_candidates","classified_fail_candidates",
    "classified_error_candidates","aborted_candidate_evaluations")


@dataclass
class ExecutionSession:
    config: object = field(repr=False)
    oracle: object = field(repr=False)
    observations: object = field(default=None,repr=False)
    session: object = field(default=None,repr=False)

    def run(self):
        c=self.config
        if self.session is not None:
            if c.domain=="prompt":
                return reduce_prompt_stochastic(c.candidate,self.session)
            if c.domain=="instructions":
                return reduce_instructions_stochastic(c.candidate,self.session)
            return reduce_repository_stochastic(c.candidate,self.session,projection=c.projection)
        if c.oracle=="codex":
            if c.domain=="prompt":
                return reduce_prompt(c.candidate,self.observations)
            if c.domain=="instructions":
                return reduce_instructions(c.candidate,self.observations)
            return reduce_repository_agent(c.candidate,self.observations,projection=c.projection)
        if c.domain=="prompt":
            return PromptReducer().reduce(c.candidate,self.observations)
        if c.domain=="instructions":
            return InstructionReducer().reduce(c.candidate,self.observations)
        return RepositoryReducer().reduce(c.candidate,self.observations,projection=c.projection)

    def cost_snapshot(self,elapsed):
        projection=self.config.projection
        common={"elapsed_seconds":elapsed,
            "projection_attempts":projection.projection_attempts if projection else None,
            "completed_projection_checks":projection.completed_projection_checks if projection else None}
        if self.session is not None:
            counts=self.session.snapshot(); launches=counts.launch_counts
            return CostSnapshot(logical_evaluations=counts.logical_candidate_evaluations,
                underlying_trial_attempts=counts.underlying_trial_attempts,
                agent_launches=CountEvidence(launches.agent,launches.agent_lower_bound,"public-stochastic-snapshot"),
                verifier_launches=CountEvidence(launches.verifier,launches.verifier_lower_bound,"public-stochastic-snapshot"),
                budget={"limit":counts.budget.limit,"used":counts.budget.used,"remaining":counts.budget.remaining},
                stochastic_counters={name:getattr(counts,name) for name in _COUNTERS},**common)
        o=self.observations
        return CostSnapshot(logical_evaluations=o.logical_evaluations,
            agent_launches=o.agent_launches,verifier_launches=o.verifier_launches,**common)

    def safe_stage(self):
        if self.session is None:
            return self.observations.last_stage
        last=self.session.last_evaluation
        stage=last.records[-1].stage if last and last.records else None
        return stage if stage in {s.value for s in AgentStage} else None

    def contexts(self):
        return {"agent":agent_context(getattr(self.oracle,"context",None)),
            "stochastic":stochastic_context(self.config.stochastic_policy,self.cost_snapshot(0),
                getattr(self.oracle,"context",None),self.stochastic_decision())}

    def stochastic_decision(self):
        last=self.session.last_evaluation if self.session else None
        if last is None:
            return None
        return {"outcome":last.outcome.value,"stop_reason":last.stop_reason.value,
            "decision_complete":last.decision_complete,"trials_executed":last.trials_executed,
            "target_failures":last.target_failures,"fail_trials":last.fail_trials,
            "pass_trials":last.pass_trials,"error_trials":last.error_trials}


def prepare_execution(config):
    if config.oracle=="command":
        oracle=CommandOracle(config.workspace,config.verifier,config.materializer,config.exit_policy)
    else:
        try:
            runner=CodexRunner(CodexSpec(model=config.model,argv_prefix=config.codex_argv,
                timeout_seconds=config.agent_timeout,context_mode=CodexContextMode.AMBIENT))
        except (OSError,ValueError,TypeError):
            raise ConfigFailure("CODEX_PREPARATION_FAILED") from None
        oracle=AgentExecutionOracle(config.workspace,runner,config.verifier,
            config.render_prompt,config.materializer,config.exit_policy)
    if config.stochastic_policy is not None:
        source=CliCommandTrials(oracle) if config.oracle=="command" else AgentTrials(oracle,include_summary=False)
        repeated=RepeatedOracle.from_trials(source,config.stochastic_policy,max_total_trials=config.max_total_trials)
        return ExecutionSession(config,oracle,session=repeated)
    return ExecutionSession(config,oracle,observations=DeterministicObservationOracle(oracle,config.oracle))
