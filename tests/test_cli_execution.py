import pytest

from agentreducer.cli.parser import parse_cli
from agentreducer.cli.config import load_config
from agentreducer.cli.execution import prepare_execution
from agentreducer import InitialCandidateNotFailingError, OracleEvaluationError
from cli_support import make_args


@pytest.mark.parametrize('domain',['prompt','instructions','repository'])
def test_command_composition_reduces_real_workspace_and_counts(tmp_path,domain):
    c=load_config(parse_cli(make_args(tmp_path,domain)),read_stdin=lambda:b'')
    before={p.relative_to(c.source):p.read_bytes() for p in c.source.rglob('*') if p.is_file()}
    execution=prepare_execution(c); result=execution.run(); cost=execution.cost_snapshot(0.5)
    assert result.reduced.files==('bug.txt',) if domain=='repository' else result.reduced.render()=='BUG'
    assert cost.logical_evaluations==result.evaluations==cost.verifier_launches.value
    assert cost.agent_launches.value==0 and cost.underlying_trial_attempts is None
    assert before=={p.relative_to(c.source):p.read_bytes() for p in c.source.rglob('*') if p.is_file()}
    if domain=='repository':
        assert result.visibility.workspace_projection_verified
        assert cost.projection_attempts==cost.completed_projection_checks==result.evaluations


def test_initial_pass_keeps_cost_without_fabricating_result(tmp_path):
    c=load_config(parse_cli(make_args(tmp_path,text='no trigger')),read_stdin=lambda:b'')
    execution=prepare_execution(c)
    with pytest.raises(InitialCandidateNotFailingError): execution.run()
    assert execution.cost_snapshot(0).logical_evaluations==1
