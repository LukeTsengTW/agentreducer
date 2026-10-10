"""Stable schema1 JSON; no dataclass or repr fallback."""

from collections.abc import Mapping
import json

from agentreducer._version import __version__
from .models import RunReport


def _thaw(value):
    if isinstance(value,Mapping):
        return {k:_thaw(v) for k,v in value.items()}
    if isinstance(value,tuple):
        return [_thaw(x) for x in value]
    return value


def _count(value):
    return {"value":value.value,"lower_bound":value.lower_bound,"basis":value.basis}


def report_mapping(report):
    if not isinstance(report,RunReport):
        raise TypeError("RunReport required")
    cost=report.cost
    return {"schema_version":1,"tool":{"name":"agentreducer","version":__version__},
        "domain":report.domain,"status":report.status,"exit_code":report.exit_code,
        "experiment":_thaw(report.experiment),"result":_thaw(report.result),"context":_thaw(report.context),
        "cost":{"logical_evaluations":cost.logical_evaluations,"underlying_trial_attempts":cost.underlying_trial_attempts,
            "agent_launches":_count(cost.agent_launches),"verifier_launches":_count(cost.verifier_launches),
            "budget":_thaw(cost.budget),"stochastic_counters":_thaw(cost.stochastic_counters),
            "projection_attempts":cost.projection_attempts,"completed_projection_checks":cost.completed_projection_checks,
            "elapsed_seconds":cost.elapsed_seconds},
        "limitations":_thaw(report.limitations),"error":_thaw(report.error)}


def render_json(report):
    return json.dumps(report_mapping(report),ensure_ascii=False,allow_nan=False,indent=2)+'\n'
