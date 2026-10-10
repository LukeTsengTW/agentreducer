"""Explicit projection of accepted results; plaintext is opt-in export only."""

import hashlib

from agentreducer.prompt import PromptReductionResult
from agentreducer.instructions import InstructionReductionResult
from agentreducer.repository import RepositoryReductionResult
from agentreducer.agents import AgentReductionResult
from agentreducer.stochastic import StochasticReductionResult
from agentreducer.stochastic.models import qualification
from .models import RunReport


def agent_context(context):
    if context is None:
        return None
    return {"mode":context.mode.value,"assurance":context.assurance.value,
        "version":context.version,"unknown_sources":list(context.unknown_sources),
        "native_controlled_verified":context.native_controlled_verified,
        "qualification":context.qualification}


def policy_mapping(policy):
    if policy is None:
        return None
    threshold=policy.failure_threshold
    return {"min_trials":policy.min_trials,"max_trials":policy.max_trials,
        "failure_threshold":{"numerator":threshold.numerator,"denominator":threshold.denominator},
        "target_failures":policy.target_failures}


def _unwrap(result):
    context = policy = None
    if isinstance(result,StochasticReductionResult):
        context,policy=result.context,result.policy
        result=result.reduction
    elif isinstance(result,AgentReductionResult):
        context=result.context
        result=result.reduction
    return result,context,policy


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stochastic_context(policy,cost,context,decision=None):
    if policy is None:
        return None
    return {"policy":policy_mapping(policy),"budget":cost.budget,
        "counters":cost.stochastic_counters,"qualification":qualification(context),
        "last_decision":decision}


def _limitations(messages):
    codes=("OBSERVED_MINIMALITY","INCOMPLETE_REPLAY_METADATA","METADATA_PRIVACY")
    return [{"code":codes[i] if i<len(codes) else "EXPERIMENT_QUALIFICATION","message":s}
            for i,s in enumerate(messages)]


def _text_result(core):
    original,reduced=core.original,core.reduced
    positions={id(block):i for i,block in enumerate(original.blocks)}
    if len(positions)!=len(original.blocks):
        raise ValueError("Ambiguous original block occurrences")
    retained=[]
    previous=-1
    for block in reduced.blocks:
        index=positions.get(id(block),-1)
        if index<=previous:
            raise ValueError("Reduced blocks must be original ordered occurrences")
        record={"original_index":index,"sha256":_digest(block.text)}
        if isinstance(core,InstructionReductionResult):
            record["kind"]=block.kind.value
            record["heading_path"]=[{"level":h.level,"occurrence":h.occurrence,
                "title_sha256":_digest(h.title)} for h in block.heading_path]
        retained.append(record); previous=index
    original_text,reduced_text=original.render(),reduced.render()
    data={"original_block_count":core.original_block_count,"reduced_block_count":core.reduced_block_count,
        "original_character_count":len(original_text),"reduced_character_count":len(reduced_text),
        "original_bytes":len(original_text.encode("utf-8")),"reduced_bytes":len(reduced_text.encode("utf-8")),
        "character_reduction_percentage":100.0*(len(original_text)-len(reduced_text))/len(original_text) if original_text else 0.0,
        "original_sha256":_digest(original_text),"reduced_sha256":_digest(reduced_text),
        "retained":retained}
    if isinstance(core,PromptReductionResult):
        data["split"]=original.mode.value
    else:
        data["block_reduction_percentage"]=core.block_reduction_percentage
    return data


def _repository_result(core):
    manifest=core.original.manifest
    files={f.relative_path:f for f in manifest.files}
    data={"snapshot_id":manifest.snapshot_id,"snapshot_semantics":"initial managed file presence; contents fixed",
        "original_file_count":core.original_file_count,"reduced_file_count":core.reduced_file_count,
        "original_bytes":core.original_bytes,"reduced_bytes":core.reduced_bytes,
        "total_managed_file_count":core.total_managed_file_count,"total_managed_bytes":core.total_managed_bytes,
        "pinned_file_count":core.pinned_file_count,"pinned_bytes":core.pinned_bytes,
        "retained_bytes":core.retained_bytes,"file_reduction_percentage":core.file_reduction_percentage,
        "byte_reduction_percentage":core.byte_reduction_percentage,
        "retained":[{"path":p,"sha256":files[p].digest,"bytes":files[p].size,"mode":files[p].mode} for p in core.reduced.files],
        "retained_paths":list(core.reduced.files),"pinned_paths":sorted(manifest.pinned_paths),
        "original_identity":{"snapshot_id":manifest.snapshot_id,"paths":list(core.original.files)},
        "reduced_identity":{"snapshot_id":manifest.snapshot_id,"paths":list(core.reduced.files)}}
    return data


def success_report(domain,result,*,experiment,cost,stochastic_decision=None):
    core,context,policy=_unwrap(result)
    expected={"prompt":PromptReductionResult,"instructions":InstructionReductionResult,"repository":RepositoryReductionResult}
    if domain not in expected or not isinstance(core,expected[domain]):
        raise TypeError("Result does not match domain")
    data=_repository_result(core) if domain=="repository" else _text_result(core)
    data["minimality"]="policy-relative observed 1-minimality" if policy is not None else "configured-oracle observed 1-minimality"
    contexts={"agent":agent_context(context),"stochastic":stochastic_context(policy,cost,context,stochastic_decision),"repository_visibility":None}
    limits=["Observed 1-minimality does not imply a global minimum.",
        "Reports omit raw inputs and argv; experiment metadata is not a complete replay recipe.",
        "Digests and relative paths can disclose structure or low-entropy content."]
    if context is not None:
        limits.extend((context.qualification,"Agent process launches do not bound backend model requests.",
            "Global agent-readable filesystem isolation: NOT PROVEN."))
    if policy is not None:
        limits.append(qualification(context))
    if domain=="repository":
        visibility=core.visibility
        contexts["repository_visibility"]={"workspace_projection_verified":visibility.workspace_projection_verified,
            "external_read_isolation":visibility.external_read_isolation.value,"qualification":visibility.qualification}
        limits.append(core.qualification)
    return RunReport(domain,"reduced",0,experiment,data,contexts,cost,_limitations(limits))


def failure_report(domain,status,exit_code,error,*,experiment,cost,contexts):
    value={"agent":contexts.get("agent"),"stochastic":contexts.get("stochastic"),"repository_visibility":None}
    limits=[]
    if value["agent"] is not None:
        limits.extend((value["agent"]["qualification"],"Agent process launches do not bound backend model requests.",
            "Global agent-readable filesystem isolation: NOT PROVEN."))
    if value["stochastic"] is not None:
        limits.append("Empirical finite-horizon policy; no confidence, iid or deterministic guarantee.")
    if domain=="repository":
        value["repository_visibility"]={"workspace_projection_verified":False,
            "external_read_isolation":"not-guaranteed" if value["agent"] else "unknown",
            "qualification":"A failed run does not establish whole-reduction workspace visibility."}
    return RunReport(domain,status,exit_code,experiment,None,value,cost,
        [{"code":"EXPERIMENT_QUALIFICATION","message":s} for s in limits],error)


def reduced_bytes(domain,result):
    core,_,_=_unwrap(result)
    expected={"prompt":PromptReductionResult,"instructions":InstructionReductionResult,"repository":RepositoryReductionResult}
    if domain not in expected or not isinstance(core,expected[domain]):
        raise TypeError("Completed domain reduction required")
    if domain=="repository":
        return ''.join(p+'\n' for p in core.reduced.files).encode("utf-8")
    return core.reduced.render().encode("utf-8")
