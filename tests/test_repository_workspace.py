"""Safe M3 delegation must sanitize infrastructure and preserve body identity."""
from contextlib import contextmanager
from pathlib import Path
import sys
import pytest

from agentreducer import OracleResult
from agentreducer.repository import build_repository_manifest, RepositoryManifest
from agentreducer.execution import CommandOracle, CommandSpec
from agentreducer.execution.workspace import WorkspaceTemplate
from agentreducer.agents import AgentExecutionOracle, AgentContextEvidence, AgentStage

class CustomBodyError(Exception):
    pass

def manifest_at(tmp_path):
    source=tmp_path/"secret-source-canary";source.mkdir()
    (source/"a").write_bytes(b"a")
    return build_repository_manifest(source)

@pytest.mark.parametrize("error",[
    ValueError("body failure"),RuntimeError("body failure"),
    CustomBodyError("body failure"),OSError("body failure"),
    KeyboardInterrupt("cancel"),SystemExit(7),
])
def test_body_exception_identity_preserved(tmp_path,error):
    manifest=manifest_at(tmp_path)
    with pytest.raises(type(error)) as caught:
        with manifest.workspace_template().create() as workspace:
            copy=workspace.path
            assert (copy/"a").read_bytes()==b"a"
            raise error
    assert caught.value is error
    assert not copy.exists()

def failing_cleanup(monkeypatch,source):
    import agentreducer.execution.workspace as module
    actual=module.tempfile.TemporaryDirectory
    events=[]
    class Temporary:
        def __init__(self,*args,**kwargs):
            self.inner=actual(*args,**kwargs);self.name=self.inner.name
        def cleanup(self):
            self.inner.cleanup();events.append(self.name)
            raise OSError(f"cleanup denied: {source}")
    monkeypatch.setattr(module.tempfile,"TemporaryDirectory",Temporary)
    return events

def test_safe_setup_failure_hides_source_anchor_and_cleans(tmp_path,monkeypatch):
    import agentreducer.execution.workspace as module
    manifest=manifest_at(tmp_path);copies=[]
    def broken(source,destination,**kwargs):
        copies.append(destination);raise OSError(f"copy denied: {source}")
    monkeypatch.setattr(module.shutil,"copytree",broken)
    with pytest.raises(RuntimeError) as caught:
        with manifest.workspace_template().create():
            pytest.fail("setup must fail")
    assert str(manifest._source) not in str(caught.value)
    assert len(str(caught.value))<128
    assert len(copies)==1 and not copies[0].parent.exists()

def test_safe_cleanup_failure_hides_anchor_and_is_not_success(tmp_path,monkeypatch):
    manifest=manifest_at(tmp_path)
    events=failing_cleanup(monkeypatch,manifest._source)
    with pytest.raises(RuntimeError) as caught:
        with manifest.workspace_template().create() as workspace:
            copy=workspace.path
    assert str(manifest._source) not in str(caught.value)
    assert len(str(caught.value))<128
    assert len(events)==1 and not copy.exists()

def test_ordinary_cleanup_failure_keeps_existing_precedence(tmp_path,monkeypatch):
    manifest=manifest_at(tmp_path)
    events=failing_cleanup(monkeypatch,manifest._source)
    primary=ValueError("body ordinary")
    with pytest.raises(RuntimeError) as caught:
        with manifest.workspace_template().create():
            raise primary
    assert caught.value is not primary
    assert str(manifest._source) not in str(caught.value)
    assert len(events)==1

@pytest.mark.parametrize("primary",[KeyboardInterrupt("stop"),SystemExit(9)])
def test_cancellation_plus_cleanup_failure_preserves_m3_primary(tmp_path,monkeypatch,primary):
    manifest=manifest_at(tmp_path)
    events=failing_cleanup(monkeypatch,manifest._source)
    with pytest.raises(type(primary)) as caught:
        with manifest.workspace_template().create():
            raise primary
    assert caught.value is primary
    assert len(events)==1
    assert len([note for note in primary.__notes__ if note.startswith("Workspace cleanup failed:")])==1

def test_exact_one_m3_lifecycle_hidden_repr_and_one_shot_guard(tmp_path,monkeypatch):
    manifest=manifest_at(tmp_path);events=[]
    original=WorkspaceTemplate.create
    @contextmanager
    def recorded(template):
        events.append(("enter",template.exclusions))
        try:
            with original(template) as workspace:
                yield workspace
        finally:events.append(("exit",None))
    monkeypatch.setattr(WorkspaceTemplate,"create",recorded)
    delegate=manifest.workspace_template();context=delegate.create()
    assert events==[]
    assert str(manifest._source) not in repr(delegate)+repr(context)
    with context as workspace:copy=workspace.path
    assert events==[("enter",manifest.exclusions),("exit",None)]
    assert not copy.exists()
    with pytest.raises(RuntimeError):
        with context:pass
    assert len(events)==2

def test_provenance_free_metadata_cannot_create_workspace():
    with pytest.raises(ValueError):
        RepositoryManifest().workspace_template()

class BrokenMaterializer:
    def materialize(self,candidate,workspace):
        raise ValueError("intended materializer contract failure")

class NoAgent:
    context=AgentContextEvidence.ambient()
    def run(self,*args,**kwargs):
        raise AssertionError("materialization must prevent agent")

def test_command_oracle_observes_intended_body_diagnostic(tmp_path):
    manifest=manifest_at(tmp_path)
    oracle=CommandOracle(manifest.workspace_template(),
        CommandSpec((sys.executable,"-c","raise SystemExit(0)")),BrokenMaterializer())
    result=oracle.evaluate_detailed(manifest.candidate())
    assert result.outcome is OracleResult.ERROR
    assert result.command is None
    assert "intended materializer contract failure" in result.error
    assert "Repository workspace operation failed" not in result.error

def test_m4_materialization_stage_not_relabelled_as_workspace(tmp_path):
    manifest=manifest_at(tmp_path)
    oracle=AgentExecutionOracle(manifest.workspace_template(),NoAgent(),
        CommandSpec((sys.executable,"-c","raise SystemExit(0)")),lambda c:"fixed",
        materializer=BrokenMaterializer())
    result=oracle.evaluate_detailed(manifest.candidate())
    assert result.outcome is OracleResult.ERROR
    assert result.error_stage is AgentStage.MATERIALIZATION
    assert result.agent_result is result.verifier_result is None
    assert "intended materializer contract failure" in result.error
    assert "Repository workspace operation failed" not in result.error

def test_actual_m4_cleanup_failure_remains_cleanup_error(tmp_path,monkeypatch):
    manifest=manifest_at(tmp_path)
    events=failing_cleanup(monkeypatch,manifest._source)
    oracle=AgentExecutionOracle(manifest.workspace_template(),NoAgent(),
        CommandSpec((sys.executable,"-c","raise SystemExit(0)")),lambda c:"fixed",
        materializer=BrokenMaterializer())
    result=oracle.evaluate_detailed(manifest.candidate())
    assert result.outcome is OracleResult.ERROR
    assert result.error_stage is AgentStage.CLEANUP
    assert str(manifest._source) not in result.error
    assert len(events)==1

