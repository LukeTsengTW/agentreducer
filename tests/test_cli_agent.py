import json
import os
import pytest

from agentreducer.cli.parser import parse_cli
from agentreducer.cli.config import load_config
from agentreducer.cli.execution import prepare_execution
from agentreducer import OracleEvaluationError
from cli_support import make_args, safe_environment


@pytest.mark.parametrize('domain',['prompt','instructions','repository'])
def test_fake_agent_qualified_results_and_no_extra_launches(tmp_path,monkeypatch,domain):
    monkeypatch.setattr(os,'environ',safe_environment(tmp_path))
    c=load_config(parse_cli(make_args(tmp_path,domain,agent=True)),read_stdin=lambda:b'')
    execution=prepare_execution(c); result=execution.run(); cost=execution.cost_snapshot(0)
    assert result.context.mode.value=='ambient'
    assert cost.agent_launches.value==cost.verifier_launches.value==cost.logical_evaluations
    assert len((tmp_path/'agent.jsonl').read_text().splitlines())==cost.logical_evaluations
    assert result.reduction.reduced.files==('bug.txt',) if domain=='repository' else result.reduction.reduced.render()=='BUG'


def test_fake_agent_failure_never_runs_verifier(tmp_path,monkeypatch):
    monkeypatch.setattr(os,'environ',safe_environment(tmp_path))
    c=load_config(parse_cli(make_args(tmp_path,agent=True,behavior='agent-error')),read_stdin=lambda:b'')
    execution=prepare_execution(c)
    with pytest.raises(OracleEvaluationError): execution.run()
    cost=execution.cost_snapshot(0)
    assert cost.agent_launches.value==1 and cost.verifier_launches.value==0
    assert execution.safe_stage()=='agent'
