from dataclasses import FrozenInstanceError
from pathlib import Path
from fractions import Fraction
import pytest

from agentreducer.reporting.models import CountEvidence, CostSnapshot, RunReport


def test_unknown_launch_is_not_zero():
    total=CountEvidence(1,1,'returned-command-results')+CountEvidence(None,0,'unavailable')
    assert total.value is None and total.lower_bound == 1
    with pytest.raises(FrozenInstanceError): total.value=1


@pytest.mark.parametrize('value,bound',[(True,0),(-1,0),(0,1),(None,-1),(1,True),(1.0,0)])
def test_invalid_count_evidence_rejected(value,bound):
    with pytest.raises((TypeError,ValueError)): CountEvidence(value,bound,'unavailable')


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),-1,True])
def test_elapsed_rejects_nonfinite_negative_bool(bad):
    with pytest.raises((TypeError,ValueError)): CostSnapshot(elapsed_seconds=bad)


def test_reports_copy_nested_inputs_and_reject_arbitrary_objects():
    data={'values':[1,2]}
    r=RunReport('prompt','reduced',0,data,{}, {'agent':None,'stochastic':None,'repository_visibility':None},CostSnapshot())
    data['values'].append(3)
    assert r.experiment['values'] == (1,2)
    for value in [Path('x'),Fraction(1,2),object(),float('nan')]:
        with pytest.raises((TypeError,ValueError)):
            RunReport(None,'internal-error',6,{'x':value},None,{'agent':None,'stochastic':None,'repository_visibility':None},CostSnapshot())


@pytest.mark.parametrize('budget',[{'limit':5,'used':6,'remaining':-1},
    {'limit':5,'used':True,'remaining':4},{'limit':5,'used':1,'remaining':5},
    {'limit':5,'used':0,'remaining':5,'private':'extra'}])
def test_budget_presentation_rejects_inconsistent_counts(budget):
    with pytest.raises((TypeError,ValueError)): CostSnapshot(budget=budget)


def test_completed_projection_cannot_exceed_attempts():
    with pytest.raises(ValueError): CostSnapshot(projection_attempts=1,completed_projection_checks=2)
    with pytest.raises(ValueError): CostSnapshot(stochastic_counters={'observed_pass_trials':-1})
