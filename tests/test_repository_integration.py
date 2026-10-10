"""Actual local processes prove cwd projection; they do not prove global read isolation."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import os
import sys
import pytest

from agentreducer import OracleResult, OracleEvaluationError
from agentreducer.repository import (
    build_repository_manifest, RepositoryProjectionMaterializer, RepositoryReducer,
    FixedTextOverlay, ExternalReadIsolation, reduce_repository_agent,
    reduce_repository_stochastic,
)
from agentreducer.execution import CommandOracle, CommandSpec, ExitCodePolicy

CHECKER = r"""import hashlib,json,sys
from pathlib import Path
root=Path.cwd()
kind=sys.argv[2]
paths=sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file())
record={'cwd':str(root),'files':paths,'digests':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}}
if kind=='deterministic':
    bad=(Path('src/auth.py').exists() and Path('src/cache.py').exists())
else:
    try:
        state=json.loads(Path('.trial-result.json').read_text(encoding='utf-8'))
        valid=(Path('.generated.txt').read_bytes()==b'created'
               and (not state['auth'] or Path('src/auth.py').read_bytes()==b'mutated')
               and (not state['cache'] or not Path('src/cache.py').exists()))
        if not valid: raise ValueError('unexpected trial effects')
        bad=state['bad']
        record['input_files']=state['files']
    except Exception:
        raise SystemExit(7)
record['bad']=bool(bad)
with Path(sys.argv[1]).open('a',encoding='utf-8') as stream:
    stream.write(json.dumps(record)+'\n')
raise SystemExit(1 if bad else 0)
"""

def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

def contents(root):
    return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob("*") if p.is_file()}

def make_source(tmp_path, *, agents=False):
    source=tmp_path/"secret-source-canary";source.mkdir()
    data={
        ".editorconfig":b"root=true", "data.bin":b"\0\xff",
        "docs/noise.md":b"irrelevant", "src/auth.py":b"AUTH-original",
        "src/cache.py":b"CACHE-original", "src/noise.py":b"noise",
        "tests/checker.py":CHECKER.encode("utf-8"),
    }
    if agents:data["AGENTS.md"]=b"original instructions"
    for path,value in data.items():
        target=source/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(value)
    (source/"empty"/"deep").mkdir(parents=True)
    pins=("tests/checker.py","AGENTS.md") if agents else ("tests/checker.py",)
    manifest=build_repository_manifest(source,pinned_paths=pins)
    return source,manifest,contents(source)

class RecordingTemplate:
    def __init__(self,manifest):
        self.delegate=manifest.workspace_template();self.paths=[]
    @contextmanager
    def create(self):
        with self.delegate.create() as workspace:
            self.paths.append(workspace.path)
            yield workspace

class RecordingOracle:
    def __init__(self,core):
        self.core=core;self.trace=[]
    @property
    def context(self):
        return self.core.context
    def evaluate(self,candidate):
        outcome=self.core.evaluate(candidate)
        self.trace.append((candidate.files,outcome))
        return outcome

def witnesses(result,trace):
    assert (result.reduced.files,OracleResult.FAIL) in trace
    for removed in result.reduced.files:
        subset=tuple(path for path in result.reduced.files if path!=removed)
        assert (subset,OracleResult.PASS) in trace

def lifecycle(source,before,template):
    assert contents(source)==before
    assert len(set(template.paths))==len(template.paths)
    assert all(not path.exists() for path in template.paths)

def test_actual_m3_projection_checker_and_in_run_minimality(tmp_path):
    source,manifest,before=make_source(tmp_path)
    template=RecordingTemplate(manifest)
    projection=RepositoryProjectionMaterializer(manifest,verifier_paths=("tests/checker.py",))
    log=tmp_path/"verifiers.jsonl"
    core=CommandOracle(template,CommandSpec((sys.executable,"tests/checker.py",str(log),"deterministic"),5),
                       projection,ExitCodePolicy({0},{1}))
    oracle=RecordingOracle(core)
    result=RepositoryReducer().reduce(manifest.candidate(),oracle,projection=projection)
    records=rows(log);C=result.evaluations
    assert result.reduced.files==("src/auth.py","src/cache.py")
    assert C==len(oracle.trace)==len(records)==len(template.paths)
    assert projection.projection_attempts==projection.completed_projection_checks==C
    expected_digests={f.relative_path:f.digest for f in manifest.files}
    for (candidate,outcome),record in zip(oracle.trace,records):
        assert set(record["files"])==set(candidate)|manifest.pinned_paths
        assert record["digests"]=={p:expected_digests[p] for p in record["files"]}
    assert result.visibility.workspace_projection_verified
    assert result.visibility.external_read_isolation is ExternalReadIsolation.NOT_GUARANTEED
    witnesses(result,oracle.trace);lifecycle(source,before,template)
    print(f"M6 M3: logical={C}; trials=not-applicable; agent=0; verifier={len(records)}; "
          f"projection={projection.projection_attempts}/{projection.completed_projection_checks}; "
          "workspace_verified=True; external=NOT_GUARANTEED; global isolation NOT PROVEN; in-run witnesses; extra0")

def test_m3_source_drift_is_error_with_zero_checker_launches(tmp_path):
    source,manifest,before=make_source(tmp_path);template=RecordingTemplate(manifest)
    projection=RepositoryProjectionMaterializer(manifest,verifier_paths=("tests/checker.py",))
    log=tmp_path/"verifiers.jsonl"
    oracle=CommandOracle(template,CommandSpec((sys.executable,"tests/checker.py",str(log),"deterministic"),5),
                         projection,ExitCodePolicy({0},{1}))
    (source/"src/cache.py").write_bytes(b"drift")
    result=oracle.evaluate_detailed(manifest.candidate())
    assert result.outcome is OracleResult.ERROR and result.command is None
    assert rows(log)==[]
    assert (projection.projection_attempts,projection.completed_projection_checks)==(1,0)
    assert all(not p.exists() for p in template.paths)
    assert str(source) not in result.error
    print("M6 M3 drift: logical1; verifier0; projection1/0; ERROR; source deliberately changed by owned test only")

from agentreducer.agents import (
    AgentExecutionOracle, CodexRunner, CodexSpec, CodexContextMode, AgentStage,
)
from agentreducer.instructions import parse_instructions

FIXTURE=Path(__file__).parent/"fixtures"/"fake_repository_agent.py"

def prepare_agent(tmp_path,monkeypatch, *, mode="fixed",
                  context_mode=CodexContextMode.AMBIENT, fixed_instructions=True):
    # The runner merges ambient env; replace only this test process's mapping
    # with an explicit non-secret fake base before the actual subprocess call.
    safe={"SYSTEMROOT":os.environ.get("SYSTEMROOT",r"C:\Windows"),
          "WINDIR":os.environ.get("WINDIR",r"C:\Windows"),
          "PATH":str(Path(sys.executable).parent),"TEMP":str(tmp_path),"TMP":str(tmp_path)}
    monkeypatch.setattr(os,"environ",safe.copy())
    source,manifest,before=make_source(tmp_path,agents=True)
    if not fixed_instructions:
        manifest=build_repository_manifest(source,pinned_paths=("tests/checker.py",))
    fixed=parse_instructions("# Agent rules\n\nUse scoped edits.").render()
    overlay=FixedTextOverlay("AGENTS.md",fixed)
    projection=RepositoryProjectionMaterializer(manifest,verifier_paths=("tests/checker.py",),
        overlays=(overlay,) if fixed_instructions else (),
        replace_pinned_paths=("AGENTS.md",) if fixed_instructions else ())
    template=RecordingTemplate(manifest)
    agent_log=tmp_path/"agents.jsonl";verifier_log=tmp_path/"verifiers.jsonl"
    runner=CodexRunner(CodexSpec("offline-fixture",
        (sys.executable,str(FIXTURE.resolve()),"--trace",str(agent_log),"--behavior",mode),
        5,{},context_mode))
    verifier=CommandSpec((sys.executable,"tests/checker.py",str(verifier_log),"agent"),5,env=safe)
    core=AgentExecutionOracle(template,runner,verifier,lambda _:"fixed coding task",
        materializer=projection,exit_code_policy=ExitCodePolicy({0},{1}))
    return source,manifest,before,projection,template,core,agent_log,verifier_log,overlay

def assert_agent_records(records,trace,manifest,overlay=None):
    expected={f.relative_path:f.digest for f in manifest.files}
    if overlay:expected[overlay.relative_path]=overlay.digest
    for (candidate,outcome),record in zip(trace,records):
        paths=set(candidate)|manifest.pinned_paths
        if overlay:paths.add(overlay.relative_path)
        assert set(record["files"])==paths
        assert record["digests"]=={p:expected[p] for p in paths}
        assert record["pristine"]

def test_actual_m4_fake_projection_before_agent_and_pristine_each_trial(tmp_path,monkeypatch):
    source,manifest,before,projection,template,core,a_log,v_log,overlay=prepare_agent(tmp_path,monkeypatch)
    oracle=RecordingOracle(core)
    qualified=reduce_repository_agent(manifest.candidate(),oracle,projection=projection)
    result=qualified.reduction;agents=rows(a_log);verifiers=rows(v_log);C=result.evaluations
    assert result.reduced.files==("src/auth.py","src/cache.py")
    assert C==len(agents)==len(verifiers)==len(template.paths)==len(oracle.trace)
    assert projection.projection_attempts==projection.completed_projection_checks==C
    assert [row["cwd"] for row in agents]==[row["cwd"] for row in verifiers]
    assert [Path(row["cwd"]) for row in agents]==template.paths
    assert_agent_records(agents,oracle.trace,manifest,overlay)
    assert result.visibility.workspace_projection_verified
    assert result.visibility.external_read_isolation is ExternalReadIsolation.NOT_GUARANTEED
    assert qualified.context is core.context
    assert "not controlled and not frozen" in qualified.context.qualification
    witnesses(result,oracle.trace);lifecycle(source,before,template)
    print(f"M6 M4: logical={C}; trial={C}; agent={len(agents)}; verifier={len(verifiers)}; "
          f"projection={projection.projection_attempts}/{projection.completed_projection_checks}; "
          "workspace_verified=True; external=NOT_GUARANTEED; global isolation NOT PROVEN; fresh/pristine; extra0")

def test_repository_owned_agents_file_is_reducible_without_overlay(tmp_path,monkeypatch):
    source,manifest,before,projection,template,core,a_log,v_log,overlay=prepare_agent(
        tmp_path,monkeypatch,fixed_instructions=False)
    oracle=RecordingOracle(core)
    result=reduce_repository_agent(manifest.candidate(),oracle,projection=projection).reduction
    assert "AGENTS.md" in manifest.reducible_paths
    assert result.reduced.files==("src/auth.py","src/cache.py")
    assert_agent_records(rows(a_log),oracle.trace,manifest)
    lifecycle(source,before,template)

from agentreducer.stochastic import AgentTrials, LaunchCounts, RepeatedOracle, StochasticPolicy, StopReason

class RecordingSession(RepeatedOracle):
    def evaluate(self,candidate):
        outcome=super().evaluate(candidate)
        self.trace.append((candidate.files,outcome))
        return outcome

def session_for(core,a_log,v_log, *, budget=500,policy=None):
    session=RecordingSession.from_trials(AgentTrials(core),policy or StochasticPolicy(),
        max_total_trials=budget,
        launch_observer=lambda:LaunchCounts(len(rows(a_log)),len(rows(v_log))))
    session.trace=[]
    return session

def test_actual_m5_fixed_five_repository_reduction_exact_counts_and_witnesses(tmp_path,monkeypatch):
    source,manifest,before,projection,template,core,a_log,v_log,overlay=prepare_agent(
        tmp_path,monkeypatch,mode="pattern")
    session=session_for(core,a_log,v_log)
    qualified=reduce_repository_stochastic(manifest.candidate(),session,projection=projection)
    result=qualified.reduction;C=result.evaluations;counters=qualified.counters
    agents=rows(a_log);verifiers=rows(v_log)
    assert result.reduced.files==("src/auth.py","src/cache.py")
    assert C==counters.logical_candidate_evaluations==len(session.trace)
    assert counters.underlying_trial_attempts==qualified.budget.used==5*C
    assert len(agents)==len(verifiers)==len(template.paths)==5*C
    assert counters.launch_counts==LaunchCounts(5*C,5*C)
    assert projection.projection_attempts==projection.completed_projection_checks==5*C
    assert [row["bad"] for row in verifiers[:5]]==[True,True,False,True,True]
    failures=sum(row["bad"] for row in verifiers);passes=len(verifiers)-failures
    assert counters.observed_fail_trials==counters.committed_fail_trials==failures
    assert counters.observed_pass_trials==counters.committed_pass_trials==passes
    assert counters.observed_error_trials==counters.committed_error_trials==0
    assert counters.unreturned_attempts==counters.raised_trial_attempts==counters.invalid_trial_attempts==0
    assert counters.postprocessing_failed_attempts==counters.aborted_candidate_evaluations==0
    assert qualified.context is core.context and qualified.qualification.startswith(session.qualification)
    assert result.visibility.workspace_projection_verified
    assert result.visibility.external_read_isolation is ExternalReadIsolation.NOT_GUARANTEED
    trial_trace=[item for item in session.trace for _ in range(5)]
    assert_agent_records(agents,trial_trace,manifest,overlay)
    assert [row["cwd"] for row in agents]==[row["cwd"] for row in verifiers]
    witnesses(result,session.trace);lifecycle(source,before,template)
    print(f"M6 M5: logical={C}; trials={5*C}; agent={len(agents)}; verifier={len(verifiers)}; "
          f"budget={qualified.budget.used}/500; projection={projection.projection_attempts}/{projection.completed_projection_checks}; "
          f"observed/committed P/F/E={passes}/{failures}/0; workspace_verified=True; external=NOT_GUARANTEED; "
          "global isolation NOT PROVEN; in-run witnesses; extra0")

def test_duplicate_stochastic_candidates_still_use_ten_fresh_projected_copies(tmp_path,monkeypatch):
    source,manifest,before,projection,template,core,a_log,v_log,overlay=prepare_agent(
        tmp_path,monkeypatch,mode="pattern")
    session=session_for(core,a_log,v_log,budget=10)
    for _ in range(2):
        result=session.evaluate_detailed(manifest.candidate())
        assert result.outcome is OracleResult.FAIL
        assert tuple(record.outcome for record in result.records)==(
            OracleResult.FAIL,OracleResult.FAIL,OracleResult.PASS,OracleResult.FAIL,OracleResult.FAIL)
    assert session.budget.used==len(rows(a_log))==len(rows(v_log))==10
    assert projection.projection_attempts==projection.completed_projection_checks==10
    assert all(row["pristine"] for row in rows(a_log))
    lifecycle(source,before,template)

def test_midbudget_error_then_precheck_has_zero_extra_projection_or_process(tmp_path,monkeypatch):
    source,manifest,before,projection,template,core,a_log,v_log,overlay=prepare_agent(
        tmp_path,monkeypatch,mode="pattern")
    session=session_for(core,a_log,v_log,budget=2,policy=StochasticPolicy(2,5,"4/5"))
    first=session.evaluate_detailed(manifest.candidate())
    second=session.evaluate_detailed(manifest.candidate())
    assert first.outcome is second.outcome is OracleResult.ERROR
    assert first.stop_reason is second.stop_reason is StopReason.BUDGET_EXHAUSTED
    assert (first.trials_executed,first.fail_trials,first.error_trials)==(2,2,0)
    assert second.trials_executed==0 and second.local_launch_counts==LaunchCounts(0,0)
    assert session.budget.used==len(rows(a_log))==len(rows(v_log))==2
    assert projection.projection_attempts==projection.completed_projection_checks==2
    lifecycle(source,before,template)
    print("M6 M5 budget: logical2/trial2/agent2/verifier2/projection2/2; next precheck0/0/0; ERROR")

def test_budget_error_during_reduction_returns_no_partial_result_or_free_samples(tmp_path,monkeypatch):
    source,manifest,before,projection,template,core,a_log,v_log,overlay=prepare_agent(
        tmp_path,monkeypatch,mode="pattern")
    session=session_for(core,a_log,v_log,budget=5)
    with pytest.raises(OracleEvaluationError):
        reduce_repository_stochastic(manifest.candidate(),session,projection=projection)
    assert session.snapshot().logical_candidate_evaluations==2
    assert session.budget.used==len(rows(a_log))==len(rows(v_log))==5
    assert projection.projection_attempts==projection.completed_projection_checks==5
    with pytest.raises(ValueError):
        reduce_repository_stochastic(manifest.candidate(),session,projection=projection)
    assert session.budget.used==5
    lifecycle(source,before,template)

def test_drift_between_repeated_trials_is_charged_error_with_no_second_process(tmp_path,monkeypatch):
    source,manifest,before,projection,template,core,a_log,v_log,overlay=prepare_agent(
        tmp_path,monkeypatch,mode="pattern")
    actual=core.agent
    class DriftAfterFirst:
        context=actual.context
        fired=False
        def run(self,**kwargs):
            result=actual.run(**kwargs)
            if not self.fired:
                self.fired=True;(source/"src/cache.py").write_bytes(b"owned test drift")
            return result
    core=replace(core,agent=DriftAfterFirst())
    session=session_for(core,a_log,v_log,budget=5)
    with projection._observe(manifest.candidate()) as observed:
        result=session.evaluate_detailed(manifest.candidate())
    assert result.outcome is OracleResult.ERROR
    assert result.trials_executed==2 and (result.fail_trials,result.error_trials)==(1,1)
    assert result.local_launch_counts==LaunchCounts(1,1)
    assert session.budget.used==2 and len(rows(a_log))==len(rows(v_log))==1
    assert (projection.projection_attempts,projection.completed_projection_checks)==(2,1)
    assert not observed.verified
    assert all(not path.exists() for path in template.paths)
    after=contents(source);before["src/cache.py"]=b"owned test drift"
    assert after==before
    print("M6 M5 drift: logical1/trial2/agent1/verifier1/projection2/1; observed F1/E1; ERROR; workspace_verified=False")


@pytest.mark.parametrize("case",["drift","controlled","agent-error"])
def test_m4_error_launches_and_completed_projection_boundary(tmp_path,monkeypatch,case):
    source,manifest,before,projection,template,core,a_log,v_log,overlay=prepare_agent(
        tmp_path,monkeypatch,mode="agent-error" if case=="agent-error" else "fixed",
        context_mode=CodexContextMode.CONTROLLED if case=="controlled" else CodexContextMode.AMBIENT)
    if case=="drift":(source/"src/cache.py").write_bytes(b"drift")
    result=core.evaluate_detailed(manifest.candidate())
    assert result.outcome is OracleResult.ERROR
    assert result.error_stage is (AgentStage.MATERIALIZATION if case=="drift" else AgentStage.AGENT)
    assert len(rows(a_log))==(1 if case=="agent-error" else 0)
    assert rows(v_log)==[]
    assert (projection.projection_attempts,projection.completed_projection_checks)==(1,0 if case=="drift" else 1)
    assert all(not path.exists() for path in template.paths)
    if case!="drift":assert contents(source)==before
    print(f"M6 M4 {case}: logical1; agent={len(rows(a_log))}; verifier0; "
          f"projection={projection.projection_attempts}/{projection.completed_projection_checks}; ERROR")

from dataclasses import replace
from agentreducer.repository.models import _snapshot_payload

def string_leaves(value):
    if isinstance(value,str):yield value
    elif isinstance(value,dict):
        for key,child in value.items():
            yield str(key);yield from string_leaves(child)
    elif isinstance(value,(list,tuple)):
        for child in value:yield from string_leaves(child)

def test_source_canary_not_generated_into_actual_fake_agent_channels(tmp_path,monkeypatch):
    source,manifest,before,projection,template,core,a_log,v_log,overlay=prepare_agent(tmp_path,monkeypatch)
    oracle=RecordingOracle(core)
    qualified=reduce_repository_agent(manifest.candidate(),oracle,projection=projection)
    anchor=source.as_posix().casefold()
    records=rows(a_log)
    channels=[records,repr(manifest),repr(manifest.candidate()),repr(projection),
              repr(manifest.workspace_template()),repr(qualified),_snapshot_payload(manifest)]
    assert all(anchor not in text.replace("\\","/").casefold()
               for channel in channels for text in string_leaves(channel))
    assert all(str(source).encode("utf-8") not in data for data in before.values())
    assert all(row["stdin"]=="fixed coding task" for row in records)
    assert all(row["agents_text"]==overlay.text for row in records)
    # Exact original/overlay digests and namespace also exclude an undeclared
    # M6-generated location file; no inherited credential values were captured.
    assert_agent_records(records,oracle.trace,manifest,overlay)
    lifecycle(source,before,template)
    print("M6 source canary: stdin/argv/explicit-safe-env/cwd/namespace/overlay/repr/snapshot safe; "
          "no generated anchor; global agent-readable filesystem isolation NOT PROVEN")

def test_user_fixed_prompt_can_intentionally_contain_source_anchor(tmp_path,monkeypatch):
    source,manifest,before,projection,template,core,a_log,v_log,overlay=prepare_agent(tmp_path,monkeypatch)
    prompt=f"User explicitly provided location: {source}"
    core=replace(core,render_prompt=lambda _:prompt)
    result=core.evaluate_detailed(manifest.candidate())
    assert result.outcome is OracleResult.FAIL
    assert rows(a_log)[0]["stdin"]==prompt
    lifecycle(source,before,template)

def test_projection_read_error_with_canary_is_safe_m4_materialization_error(tmp_path,monkeypatch):
    source,manifest,before,projection,template,core,a_log,v_log,overlay=prepare_agent(tmp_path,monkeypatch)
    original=Path.open
    def denied(path,*args,**kwargs):
        if path==source/"src/auth.py":raise OSError(f"denied source: {source}")
        return original(path,*args,**kwargs)
    with monkeypatch.context() as context:
        context.setattr(Path,"open",denied)
        result=core.evaluate_detailed(manifest.candidate())
    assert result.outcome is OracleResult.ERROR and result.error_stage is AgentStage.MATERIALIZATION
    assert rows(a_log)==rows(v_log)==[]
    assert str(source) not in result.error
    assert (projection.projection_attempts,projection.completed_projection_checks)==(1,0)
    lifecycle(source,before,template)
