"""Qualified composition preserves existing context, policy and charged counts."""
import pytest

from agentreducer import OracleResult, InitialCandidateNotFailingError, OracleEvaluationError
from agentreducer.repository import RepositoryManifest, RepositoryFile
from agentreducer.repository.models import ExternalReadIsolation
from agentreducer.repository.reduction import reduce_repository_agent, reduce_repository_stochastic
from agentreducer.agents import AgentContextEvidence
from agentreducer.stochastic import RepeatedOracle, StochasticPolicy

def candidate():
    return RepositoryManifest(tuple(RepositoryFile(p,"0"*64,1,0o644)
        for p in ("auth","cache","noise"))).candidate()

class Counted:
    def __init__(self,context=None,primary=None,outcome=None):
        self.context=context;self.calls=[];self.primary=primary;self.outcome=outcome
    def evaluate(self,candidate):
        self.calls.append(candidate.files)
        if self.primary is not None:raise self.primary
        if self.outcome is not None:return self.outcome
        return OracleResult.FAIL if {"auth","cache"}<=set(candidate.files) else OracleResult.PASS

def test_m4_nested_context_and_repository_qualification_add_zero_calls():
    context=AgentContextEvidence.ambient(version="offline-unit")
    oracle=Counted(context)
    result=reduce_repository_agent(candidate(),oracle)
    assert result.context is context
    assert result.reduction.reduced.files==("auth","cache")
    assert result.reduction.evaluations==len(oracle.calls)
    assert not result.reduction.visibility.workspace_projection_verified
    assert result.reduction.visibility.external_read_isolation is ExternalReadIsolation.NOT_GUARANTEED
    assert "projected initial workspace" in result.reduction.qualification
    assert "not controlled and not frozen" in result.context.qualification

def test_m4_invalid_context_fails_before_samples():
    for context in (None,"ambient"):
        oracle=Counted(context)
        with pytest.raises(TypeError):reduce_repository_agent(candidate(),oracle)
        assert oracle.calls==[]

def test_m5_preserves_exact_public_counters_policy_context_and_qualification():
    context=AgentContextEvidence.ambient(version="offline-unit")
    oracle=Counted(context)
    policy=StochasticPolicy(min_trials=5,max_trials=5,failure_threshold="4/5")
    session=RepeatedOracle(oracle,policy,max_total_trials=200)
    result=reduce_repository_stochastic(candidate(),session)
    C=result.reduction.evaluations
    assert result.reduction.reduced.files==("auth","cache")
    assert result.counters==session.snapshot()
    assert result.policy is policy and result.context is context
    assert result.budget.used==len(oracle.calls)==5*C
    assert result.counters.logical_candidate_evaluations==C
    assert result.qualification.startswith(session.qualification)
    assert "observation" in result.qualification and "projected initial workspace" in result.qualification
    assert not result.reduction.visibility.workspace_projection_verified
    assert result.reduction.visibility.external_read_isolation is ExternalReadIsolation.NOT_GUARANTEED

def test_pure_m5_without_projection_or_context_keeps_unknown_visibility():
    session=RepeatedOracle(Counted(),StochasticPolicy(min_trials=1,max_trials=1,failure_threshold=1),
                           max_total_trials=50)
    result=reduce_repository_stochastic(candidate(),session)
    assert result.context is None
    assert result.reduction.visibility.external_read_isolation is ExternalReadIsolation.UNKNOWN
    assert not result.reduction.visibility.workspace_projection_verified

def test_used_session_rejected_without_extra_trial():
    oracle=Counted()
    session=RepeatedOracle(oracle,StochasticPolicy(min_trials=1,max_trials=1,failure_threshold=1),
                           max_total_trials=50)
    session.evaluate(candidate());before=session.snapshot();calls=len(oracle.calls)
    with pytest.raises(ValueError):reduce_repository_stochastic(candidate(),session)
    assert session.snapshot()==before and len(oracle.calls)==calls

def test_invalid_session_rejected_before_any_work():
    with pytest.raises(TypeError):reduce_repository_stochastic(candidate(),object())

@pytest.mark.parametrize("kind",["agent","stochastic"])
@pytest.mark.parametrize("primary",[ValueError("custom"),KeyboardInterrupt("cancel"),SystemExit(3)])
def test_qualified_wrappers_preserve_primary_exception(kind,primary):
    oracle=Counted(AgentContextEvidence.ambient(),primary=primary)
    with pytest.raises(type(primary)) as caught:
        if kind=="agent":reduce_repository_agent(candidate(),oracle)
        else:
            session=RepeatedOracle(oracle,StochasticPolicy(min_trials=1,max_trials=1,failure_threshold=1),
                                   max_total_trials=10)
            reduce_repository_stochastic(candidate(),session)
    assert caught.value is primary
    assert len(oracle.calls)==1

@pytest.mark.parametrize("outcome,error",[
    (OracleResult.PASS,InitialCandidateNotFailingError),
    (OracleResult.ERROR,OracleEvaluationError),
])
def test_wrapper_keeps_initial_pass_and_error_semantics(outcome,error):
    oracle=Counted(AgentContextEvidence.ambient(),outcome=outcome)
    with pytest.raises(error):reduce_repository_agent(candidate(),oracle)
    assert len(oracle.calls)==1

