import pytest

from agentreducer.prompt import PromptBlock, PromptCandidate, PromptReductionResult
from agentreducer.instructions import parse_instructions, InstructionReductionResult
from agentreducer.agents import AgentReductionResult, AgentContextEvidence
from agentreducer.stochastic import StochasticPolicy, StochasticReductionResult, StochasticCounters, BudgetSnapshot
from agentreducer.reporting.models import CostSnapshot
from agentreducer.reporting.convert import success_report, failure_report, reduced_bytes


def test_equal_blocks_keep_original_occurrences_and_no_plaintext():
    a,b=PromptBlock('PRIVATE'),PromptBlock('PRIVATE')
    original=PromptCandidate((a,b)); result=PromptReductionResult(original,PromptCandidate((b,)),3)
    report=success_report('prompt',result,experiment={},cost=CostSnapshot())
    assert report.result['retained'][0]['original_index'] == 1
    assert report.result['original_block_count'] == 2 and report.result['reduced_block_count'] == 1
    assert 'PRIVATE' not in repr(report)
    assert reduced_bytes('prompt',result) == b'PRIVATE'


def test_identical_object_repetition_is_ambiguous_and_rejected():
    a=PromptBlock('same'); original=PromptCandidate((a,a))
    with pytest.raises(ValueError):
        success_report('prompt',PromptReductionResult(original,PromptCandidate((a,)),1),experiment={},cost=CostSnapshot())


def test_instruction_headings_are_digests_and_renderer_is_exact():
    c=parse_instructions('# PRIVATE_HEADING\n\n  body')
    result=InstructionReductionResult(c,c,1)
    r=success_report('instructions',result,experiment={},cost=CostSnapshot())
    assert 'PRIVATE_HEADING' not in repr(r)
    assert r.result['retained'][0]['heading_path'][0]['level'] == 1
    assert len(r.result['retained'][0]['heading_path'][0]['title_sha256']) == 64
    assert reduced_bytes('instructions',result) == c.render().encode()


def test_agent_and_policy_qualifications_survive_unwrapping():
    c=PromptCandidate((PromptBlock('x'),)); core=PromptReductionResult(c,c,1)
    context=AgentContextEvidence.ambient()
    r=success_report('prompt',AgentReductionResult(core,context),experiment={},cost=CostSnapshot())
    assert r.context['agent']['mode'] == 'ambient'
    assert 'conditional' in ' '.join(x['message'] for x in r.limitations)
    counts=StochasticCounters(BudgetSnapshot(10,5),context=context)
    wrapped=StochasticReductionResult(core,StochasticPolicy(),counts,counts.budget,context,'Empirical policy qualification')
    r=success_report('prompt',wrapped,experiment={},cost=CostSnapshot())
    assert r.context['stochastic']['policy']['failure_threshold'] == {'numerator':4,'denominator':5}
    assert r.result['minimality'] == 'policy-relative observed 1-minimality'


def test_failure_has_no_reduced_artifact():
    r=failure_report('repository','oracle-error',4,{'code':'ORACLE_ERROR','stage':None,'message':'Oracle failed.'},experiment={},cost=CostSnapshot(),contexts={})
    assert r.result is None
    assert r.context['repository_visibility']['workspace_projection_verified'] is False
    with pytest.raises((TypeError,ValueError)): reduced_bytes('prompt',None)
