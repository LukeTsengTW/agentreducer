import json
import pytest

from agentreducer.reporting.models import CountEvidence, CostSnapshot, RunReport
from agentreducer.reporting.json import render_json, report_mapping


def test_unknown_failure_schema_is_stable_and_complete():
    r=RunReport(None,'internal-error',6,None,None,
        {'agent':None,'stochastic':None,'repository_visibility':None},
        CostSnapshot(agent_launches=CountEvidence(None,2,'unavailable')),
        error={'code':'INTERNAL_ERROR','stage':None,'message':'Unexpected internal failure.'})
    text=render_json(r); value=json.loads(text)
    assert list(value) == ['schema_version','tool','domain','status','exit_code','experiment','result','context','cost','limitations','error']
    assert value['tool'] == {'name':'agentreducer','version':'0.1.0'}
    assert value['schema_version'] == 1 and value['result'] is None
    assert value['cost']['agent_launches'] == {'value':None,'lower_bound':2,'basis':'unavailable'}
    assert text == render_json(r) and text.endswith('\n') and '\r' not in text
    mapping=report_mapping(r); mapping['tool']['version']='changed'
    assert report_mapping(r)['tool']['version'] == '0.1.0'


def test_utf8_and_nonfinite_rejected_before_json():
    with pytest.raises(ValueError): CostSnapshot(elapsed_seconds=float('nan'))
    r=RunReport(None,'invalid-configuration',2,{'label':'字'},None,
        {'agent':None,'stochastic':None,'repository_visibility':None},CostSnapshot())
    assert '字' in render_json(r)


@pytest.mark.parametrize('status,code',[('reduced',0),('invalid-configuration',2),('initial-not-failing',3),
    ('oracle-error',4),('stochastic-error',5),('internal-error',6),('output-error',7),('cancelled',130)])
def test_every_status_has_consistent_result_rule_and_schema(status,code):
    report=RunReport('prompt',status,code,None,{} if code==0 else None,
        {'agent':None,'stochastic':None,'repository_visibility':None},CostSnapshot())
    mapping=json.loads(render_json(report))
    assert mapping['exit_code']==code and (mapping['result'] is not None)==(code==0)
