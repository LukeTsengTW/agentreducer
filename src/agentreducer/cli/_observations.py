"""Observe returned public execution records without saving diagnostics."""

from dataclasses import replace

from agentreducer.execution import CommandResult
from agentreducer.agents import AgentStage
from agentreducer.reporting.models import CountEvidence
from agentreducer.stochastic import CommandTrials, LaunchCounts


def safe_stage(result):
    stage=getattr(result,"error_stage",None)
    return stage.value if isinstance(stage,AgentStage) else None


def record_launch_evidence(result, kind):
    if kind == "command":
        command=getattr(result,"command",None)
        v=1 if isinstance(command,CommandResult) else None
        return CountEvidence(0,0,"known-no-agent"),CountEvidence(v,1 if v else 0,"returned-command-results" if v else "unavailable")
    stage=getattr(result,"error_stage",None)
    if stage in (AgentStage.WORKSPACE,AgentStage.MATERIALIZATION,AgentStage.PROMPT):
        return CountEvidence(0,0,"pre-launch-stage"),CountEvidence(0,0,"pre-launch-stage")
    agent=getattr(result,"agent_result",None)
    a=1 if isinstance(getattr(agent,"command",None),CommandResult) else None
    v=1 if isinstance(getattr(result,"verifier_result",None),CommandResult) else None
    if stage == AgentStage.AGENT and v is None:
        v=0
    return (CountEvidence(a,1 if a else 0,"returned-command-results" if a else "unavailable"),
        CountEvidence(v,1 if v else 0,"returned-command-results" if v else "pre-launch-stage" if v==0 else "unavailable"))


class DeterministicObservationOracle:
    def __init__(self, oracle, kind):
        self.oracle=oracle
        self.kind=kind
        self.logical_evaluations=0
        self.agent_launches=CountEvidence(0,0,"known-no-agent" if kind=="command" else "returned-command-results")
        self.verifier_launches=CountEvidence(0,0,"returned-command-results")
        self.last_stage=None
        self.context=getattr(oracle,"context",None)

    def evaluate(self,candidate):
        self.logical_evaluations+=1
        try:
            result=self.oracle.evaluate_detailed(candidate)
        except BaseException:
            # A raised call provides no returned launch record. Preserve the
            # primary object and already witnessed lower bounds.
            if self.kind != "command":
                self.agent_launches += CountEvidence(None,0,"unavailable")
            self.verifier_launches += CountEvidence(None,0,"unavailable")
            raise
        a,v=record_launch_evidence(result,self.kind)
        self.agent_launches+=a; self.verifier_launches+=v
        self.last_stage=safe_stage(result)
        return result.outcome


class CliCommandTrials:
    def __init__(self,oracle):
        self._source=CommandTrials(oracle,include_summary=False)
        self.context=None

    def invoke(self,candidate):
        return self._source.invoke(candidate)

    def summarize(self,raw):
        observation=self._source.summarize(raw)
        a,v=record_launch_evidence(raw.payload,"command")
        return replace(observation,launch_counts=LaunchCounts(a.value,v.value,a.lower_bound,v.lower_bound))
