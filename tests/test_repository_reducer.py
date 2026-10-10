"""Thin M0 composition and observation evidence, with no hidden validation calls."""
from pathlib import Path
import pytest

from agentreducer import OracleResult, InitialCandidateNotFailingError, OracleEvaluationError
from agentreducer.repository import RepositoryManifest, RepositoryFile, build_repository_manifest
from agentreducer.repository.models import ExternalReadIsolation
from agentreducer.repository.projection import RepositoryProjectionMaterializer
from agentreducer.repository.reducer import RepositoryReducer

def metadata():
    return RepositoryManifest(tuple(RepositoryFile(p,"0"*64,size,0o644)
        for p,size in [("auth",2),("cache",3),("irrelevant",9)]))

class Recording:
    def __init__(self,predicate=None):
        self.calls=[];self.predicate=predicate or (lambda paths:{"auth","cache"}<=set(paths))
    def evaluate(self,candidate):
        outcome=OracleResult.FAIL if self.predicate(candidate.files) else OracleResult.PASS
        self.calls.append((candidate.files,outcome))
        return outcome

def test_pure_thin_ddmin_has_actual_in_run_single_deletion_witnesses():
    candidate=metadata().candidate();oracle=Recording()
    result=RepositoryReducer().reduce(candidate,oracle)
    assert result.reduced.files==("auth","cache")
    assert result.evaluations==len(oracle.calls)
    assert (("auth","cache"),OracleResult.FAIL) in oracle.calls
    assert (("auth",),OracleResult.PASS) in oracle.calls
    assert (("cache",),OracleResult.PASS) in oracle.calls
    assert result.reduced_bytes==5 and result.original_bytes==14
    assert not result.visibility.workspace_projection_verified
    assert result.visibility.external_read_isolation is ExternalReadIsolation.UNKNOWN

@pytest.mark.parametrize("paths",[(),("auth",)])
def test_failing_empty_and_singleton_are_valid(paths):
    candidate=metadata().candidate(paths)
    oracle=Recording(lambda p:bool(p) if paths else True)
    result=RepositoryReducer().reduce(candidate,oracle)
    assert result.reduced.files==paths
    assert result.evaluations==len(oracle.calls)

def test_initial_pass_and_oracle_error_unchanged():
    candidate=metadata().candidate()
    with pytest.raises(InitialCandidateNotFailingError):
        RepositoryReducer().reduce(candidate,Recording(lambda p:False))
    class Error:
        def evaluate(self,candidate):return OracleResult.ERROR
    with pytest.raises(OracleEvaluationError):
        RepositoryReducer().reduce(candidate,Error())

@pytest.mark.parametrize("primary",[ValueError("custom"),KeyboardInterrupt("stop"),SystemExit(4)])
def test_primary_exception_identity_preserved(primary):
    class Raise:
        def evaluate(self,candidate):raise primary
    with pytest.raises(type(primary)) as caught:
        RepositoryReducer().reduce(metadata().candidate(),Raise())
    assert caught.value is primary

def prepared(tmp_path):
    source=tmp_path/"source";source.mkdir()
    for name in ["auth","cache","irrelevant"]:(source/name).write_bytes(name.encode())
    manifest=build_repository_manifest(source)
    projection=RepositoryProjectionMaterializer(manifest,verifier_paths=())
    return manifest,projection

class Physical(Recording):
    def __init__(self,manifest,projection):
        super().__init__();self.manifest=manifest;self.projection=projection
    def evaluate(self,candidate):
        with self.manifest.workspace_template().create() as workspace:
            self.projection.materialize(candidate,workspace.path)
            actual={p.name for p in workspace.path.iterdir() if p.is_file()}
            assert actual==set(candidate.files)
        return super().evaluate(candidate)

def test_each_classification_projected_yields_verified_not_read_isolated(tmp_path):
    manifest,projection=prepared(tmp_path);oracle=Physical(manifest,projection)
    result=RepositoryReducer().reduce(manifest.candidate(),oracle,projection=projection)
    assert result.reduced.files==("auth","cache")
    assert result.evaluations==projection.projection_attempts==projection.completed_projection_checks
    assert result.visibility.workspace_projection_verified
    assert result.visibility.external_read_isolation is ExternalReadIsolation.NOT_GUARANTEED

def test_success_in_one_classification_does_not_cover_other_classifications(tmp_path):
    manifest,projection=prepared(tmp_path)
    class FirstOnly:
        calls=0
        def evaluate(self,candidate):
            self.calls+=1
            if self.calls==1:
                with manifest.workspace_template().create() as workspace:
                    projection.materialize(candidate,workspace.path)
            return OracleResult.FAIL
    oracle=FirstOnly()
    result=RepositoryReducer().reduce(manifest.candidate(),oracle,projection=projection)
    assert result.reduced.files==()
    assert oracle.calls>1 and projection.completed_projection_checks==1
    assert not result.visibility.workspace_projection_verified

def test_unused_and_stale_projector_never_certify(tmp_path):
    manifest,projection=prepared(tmp_path)
    with manifest.workspace_template().create() as workspace:
        projection.materialize(manifest.candidate(),workspace.path)
    result=RepositoryReducer().reduce(manifest.candidate(),Recording(),projection=projection)
    assert not result.visibility.workspace_projection_verified
    assert projection.completed_projection_checks==1

def test_mismatched_projection_is_contract_error_not_just_false_metadata(tmp_path):
    manifest,projection=prepared(tmp_path)
    class Wrong:
        def evaluate(self,candidate):
            with manifest.workspace_template().create() as workspace:
                try:projection.materialize(manifest.candidate(()),workspace.path)
                except RuntimeError:pass
            return OracleResult.FAIL
    with pytest.raises(RuntimeError):
        RepositoryReducer().reduce(manifest.candidate(),Wrong(),projection=projection)

def test_invalid_projection_snapshot_type_preflight_adds_no_calls(tmp_path):
    manifest,projection=prepared(tmp_path);oracle=Recording()
    for bad in [object(),projection]:
        with pytest.raises((TypeError,ValueError)):
            RepositoryReducer().reduce(metadata().candidate(),oracle,projection=bad)
    assert oracle.calls==[]

def test_broad_oracle_minimizes_its_failure_identity_not_causal_bug():
    strict=RepositoryReducer().reduce(metadata().candidate(),Recording())
    # The intentionally broad checker also calls missing-import outcomes FAIL.
    broad=RepositoryReducer().reduce(metadata().candidate(),Recording(lambda p:True))
    assert strict.reduced.files==("auth","cache")
    assert broad.reduced.files==()
    assert "failure identity is exactly what that oracle recognizes" in broad.qualification
    assert "not causal root-bug equivalence" in broad.qualification

