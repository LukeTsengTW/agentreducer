from agentreducer.reporting.models import CountEvidence, CostSnapshot, RunReport
from agentreducer.reporting.human import render_human


def test_human_unknown_counts_and_fixed_error():
    r=RunReport(None,'internal-error',6,None,None,
        {'agent':None,'stochastic':None,'repository_visibility':None},
        CostSnapshot(logical_evaluations=2,verifier_launches=CountEvidence(None,1,'unavailable')),
        error={'code':'INTERNAL_ERROR','stage':None,'message':'Unexpected internal failure.'})
    text=render_human(r)
    assert text.startswith('AgentReducer 0.1.0\n')
    assert 'Oracle evaluations: 2' in text and 'unknown (at least 1)' in text
    assert 'INTERNAL_ERROR' in text and '\x1b' not in text
    assert render_human(r)==text


def test_human_repository_shows_percentage_and_projection_costs():
    r=RunReport('repository','reduced',0,{},
        {'original_file_count':2,'reduced_file_count':0,'pinned_file_count':1,'retained':[],'pinned_paths':['check.py'],
         'file_reduction_percentage':100.0,'original_bytes':20,'reduced_bytes':0,'byte_reduction_percentage':100.0},
        {'agent':None,'stochastic':None,'repository_visibility':{'workspace_projection_verified':True,'external_read_isolation':'not-guaranteed'}},
        CostSnapshot(projection_attempts=4,completed_projection_checks=4))
    text=render_human(r)
    assert '100.00%' in text and 'Bytes: 20 -> 0' in text
    assert 'Projection attempts/completed checks: 4/4' in text
