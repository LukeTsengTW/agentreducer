import pytest

from agentreducer import OracleResult
from agentreducer.execution import CommandEvaluation, CommandResult
from agentreducer.agents import AgentEvaluation, AgentStage, AgentContextEvidence
from agentreducer.cli._observations import DeterministicObservationOracle, CliCommandTrials


class Detailed:
    def __init__(self,result): self.result=result; self.calls=[]
    def evaluate_detailed(self,candidate): self.calls.append(candidate); return self.result


def command(): return CommandResult(('private',),1,'PRIVATE','PRIVATE',0.01,False)


def test_command_counts_one_detailed_call_and_does_not_store_payload():
    source=Detailed(CommandEvaluation(OracleResult.FAIL,command()))
    obs=DeterministicObservationOracle(source,'command')
    assert obs.evaluate('candidate') is OracleResult.FAIL
    assert source.calls == ['candidate']
    assert obs.logical_evaluations == 1 and obs.verifier_launches.value == 1
    assert obs.agent_launches.value == 0
    assert 'PRIVATE' not in repr(obs.__dict__)


def test_missing_command_record_is_unknown():
    obs=DeterministicObservationOracle(Detailed(CommandEvaluation(OracleResult.ERROR)),'command')
    obs.evaluate('x')
    assert obs.verifier_launches.value is None and obs.verifier_launches.lower_bound == 0


@pytest.mark.parametrize('stage',[AgentStage.WORKSPACE,AgentStage.MATERIALIZATION,AgentStage.PROMPT])
def test_pre_agent_stages_prove_zero(stage):
    result=AgentEvaluation(OracleResult.ERROR,None,None,AgentContextEvidence.ambient(),stage)
    obs=DeterministicObservationOracle(Detailed(result),'codex'); obs.evaluate('x')
    assert obs.agent_launches.value == obs.verifier_launches.value == 0


@pytest.mark.parametrize('exception',[SystemExit('PRIVATE'),KeyboardInterrupt()])
def test_runtime_baseexception_identity_is_preserved(exception):
    class Raising:
        def evaluate_detailed(self,c): raise exception
    obs=DeterministicObservationOracle(Raising(),'command')
    with pytest.raises(type(exception)) as got: obs.evaluate('x')
    assert got.value is exception and obs.logical_evaluations == 1
