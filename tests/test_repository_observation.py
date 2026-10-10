"""Projection evidence belongs to one classification, never a previous success."""
from pathlib import Path
from threading import Thread
import pytest

from agentreducer.repository import build_repository_manifest
from agentreducer.repository.projection import RepositoryProjectionMaterializer

def prepared(tmp_path):
    source=tmp_path/"source";source.mkdir()
    (source/"a").write_bytes(b"a");(source/"b").write_bytes(b"b")
    manifest=build_repository_manifest(source)
    return manifest,RepositoryProjectionMaterializer(manifest,verifier_paths=())

def run_projection(manifest,projection,candidate):
    with manifest.workspace_template().create() as workspace:
        projection.materialize(candidate,workspace.path)

def test_stale_success_not_reused_in_next_empty_scope(tmp_path):
    manifest,projection=prepared(tmp_path);candidate=manifest.candidate()
    run_projection(manifest,projection,candidate)
    with projection._observe(candidate) as observed:pass
    assert not observed.verified
    assert (observed.attempts,observed.completed)==(0,0)

def test_each_scope_collects_all_matching_trials_and_resets(tmp_path):
    manifest,projection=prepared(tmp_path);candidate=manifest.candidate()
    with projection._observe(candidate) as first:
        run_projection(manifest,projection,candidate)
        run_projection(manifest,projection,candidate)
    assert first.verified and (first.attempts,first.completed)==(2,2)
    with projection._observe(manifest.candidate(())) as second:
        run_projection(manifest,projection,manifest.candidate(()))
    assert second.verified and (second.attempts,second.completed)==(1,1)
    assert (first.attempts,first.completed)==(2,2)
    assert projection.projection_attempts==projection.completed_projection_checks==3

def test_matching_then_failed_attempt_cannot_yield_verified(tmp_path):
    manifest,projection=prepared(tmp_path);candidate=manifest.candidate()
    with projection._observe(candidate) as observed:
        run_projection(manifest,projection,candidate)
        (manifest._source/"a").write_bytes(b"drift")
        with pytest.raises(RuntimeError):
            run_projection(manifest,projection,candidate)
    assert not observed.verified
    assert (observed.attempts,observed.completed)==(2,1)

def test_mismatched_candidate_is_contract_error_even_when_caller_swallowed(tmp_path):
    manifest,projection=prepared(tmp_path)
    with pytest.raises(RuntimeError):
        with projection._observe(manifest.candidate(("a",))) as observed:
            with pytest.raises(RuntimeError):
                run_projection(manifest,projection,manifest.candidate(("b",)))
    assert not observed.verified and observed.completed==0

def test_matching_first_then_wrong_candidate_also_aborts(tmp_path):
    manifest,projection=prepared(tmp_path);candidate=manifest.candidate()
    with pytest.raises(RuntimeError):
        with projection._observe(candidate) as observed:
            run_projection(manifest,projection,candidate)
            run_projection(manifest,projection,manifest.candidate(()))
    assert not observed.verified

def test_scope_rejects_nested_reentry_without_stalling(tmp_path):
    manifest,projection=prepared(tmp_path);candidate=manifest.candidate()
    with pytest.raises(RuntimeError):
        with projection._observe(candidate):
            with projection._observe(candidate):pass

def test_foreign_thread_materialization_rejected_without_positive_evidence(tmp_path):
    manifest,projection=prepared(tmp_path);candidate=manifest.candidate();errors=[]
    def other_thread():
        try:run_projection(manifest,projection,candidate)
        except RuntimeError as exc:errors.append(exc)
    with pytest.raises(RuntimeError):
        with projection._observe(candidate) as observed:
            thread=Thread(target=other_thread);thread.start();thread.join(timeout=5)
            assert not thread.is_alive()
    assert len(errors)==1
    assert not observed.verified

@pytest.mark.parametrize("primary",[KeyboardInterrupt("stop"),SystemExit(7)])
def test_scope_preserves_primary_exception_and_releases_for_later_use(tmp_path,primary):
    manifest,projection=prepared(tmp_path);candidate=manifest.candidate()
    with pytest.raises(type(primary)) as caught:
        with projection._observe(candidate):raise primary
    assert caught.value is primary
    with projection._observe(candidate) as observed:
        run_projection(manifest,projection,candidate)
    assert observed.verified

def test_observation_metadata_performs_no_filesystem_or_process_work(tmp_path,monkeypatch):
    manifest,projection=prepared(tmp_path);candidate=manifest.candidate()
    def forbidden(*args,**kwargs):raise AssertionError("metadata must not read filesystem")
    monkeypatch.setattr(Path,"lstat",forbidden)
    monkeypatch.setattr(Path,"open",forbidden)
    with projection._observe(candidate) as observed:pass
    assert not observed.verified
    assert projection.projection_attempts==projection.completed_projection_checks==0

def test_recursive_materialization_rejected_and_lock_released(tmp_path,monkeypatch):
    manifest,projection=prepared(tmp_path);candidate=manifest.candidate()
    original=projection._materialize
    def recursive(candidate,workspace):projection.materialize(candidate,workspace)
    with monkeypatch.context() as context:
        context.setattr(projection,"_materialize",recursive)
        with pytest.raises(RuntimeError):
            with projection._observe(candidate) as observed:
                run_projection(manifest,projection,candidate)
    assert not observed.verified and observed.completed==0
    with projection._observe(candidate) as later:
        run_projection(manifest,projection,candidate)
    assert later.verified

def test_contract_failure_does_not_replace_primary_cancellation(tmp_path):
    manifest,projection=prepared(tmp_path);primary=KeyboardInterrupt("primary")
    with pytest.raises(KeyboardInterrupt) as caught:
        with projection._observe(manifest.candidate()) as observed:
            with pytest.raises(RuntimeError):
                run_projection(manifest,projection,manifest.candidate(()))
            raise primary
    assert caught.value is primary and not observed.verified

