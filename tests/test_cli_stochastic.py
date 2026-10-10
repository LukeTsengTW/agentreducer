from types import SimpleNamespace
import pytest

from agentreducer import OracleResult, OracleEvaluationError
from agentreducer.cli.parser import parse_cli
from agentreducer.cli.config import load_config
from agentreducer.cli.execution import prepare_execution
from agentreducer.cli._observations import CliCommandTrials
from agentreducer.stochastic import RepeatedOracle, StochasticPolicy
from cli_support import make_args


@pytest.mark.parametrize('domain',['prompt','instructions','repository'])
def test_fresh_m5_session_counts_real_command_trials(tmp_path,domain):
    c=load_config(parse_cli(make_args(tmp_path,domain,stochastic=True)),read_stdin=lambda:b'')
    execution=prepare_execution(c)
    assert isinstance(execution.session,RepeatedOracle) and execution.session.budget.used==0
    result=execution.run(); cost=execution.cost_snapshot(0)
    assert cost.underlying_trial_attempts==5*cost.logical_evaluations==cost.verifier_launches.value
    assert cost.agent_launches.value==0 and cost.budget['used']==cost.underlying_trial_attempts
    assert cost.stochastic_counters['observed_fail_trials']==cost.stochastic_counters['committed_fail_trials']
    with pytest.raises(ValueError): execution.run()


def test_command_postprocessing_failure_preserves_raw_outcome():
    class Malformed:
        def evaluate_detailed(self,candidate): return SimpleNamespace(outcome=OracleResult.PASS)
    session=RepeatedOracle.from_trials(CliCommandTrials(Malformed()),StochasticPolicy(1,1,1),max_total_trials=1)
    with pytest.raises(TypeError): session.evaluate_detailed(None)
    counters=session.snapshot()
    assert counters.observed_pass_trials==1 and counters.committed_pass_trials==0
    assert counters.postprocessing_failed_attempts==1 and counters.aborted_candidate_evaluations==1


def test_budget_error_is_public_and_zero_trial_precheck(tmp_path):
    args=make_args(tmp_path,stochastic=True)+['--max-total-trials','5']
    execution=prepare_execution(load_config(parse_cli(args),read_stdin=lambda:b''))
    with pytest.raises(OracleEvaluationError): execution.run()
    cost=execution.cost_snapshot(0)
    assert cost.budget['used']==5 and cost.logical_evaluations==2
    assert execution.session.last_evaluation.trials_executed==0


def test_early_decisions_use_actual_short_horizons_without_hidden_calls(tmp_path):
    args=make_args(tmp_path,text='BUG',stochastic=True)+['--min-trials','1']
    execution=prepare_execution(load_config(parse_cli(args),read_stdin=lambda:b''))
    result=execution.run(); cost=execution.cost_snapshot(0)
    assert result.reduction.reduced.render()=='BUG'
    assert cost.logical_evaluations==2 and cost.underlying_trial_attempts==6
    assert cost.verifier_launches.value==6
    assert execution.session.last_evaluation.stop_reason.value=='threshold-unreachable'


def test_missing_command_record_keeps_unknown_verifier_count():
    from agentreducer.execution import CommandEvaluation
    class Missing:
        def evaluate_detailed(self,c): return CommandEvaluation(OracleResult.ERROR)
    session=RepeatedOracle.from_trials(CliCommandTrials(Missing()),StochasticPolicy(1,1,1),max_total_trials=1)
    assert session.evaluate(None) is OracleResult.ERROR
    counts=session.snapshot()
    assert counts.launch_counts.agent==0 and counts.launch_counts.verifier is None
    assert counts.launch_counts.verifier_lower_bound==0
