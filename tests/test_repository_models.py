"""Canonical paths and evidence must not silently certify unsupported context."""
from dataclasses import FrozenInstanceError
import hashlib
import pytest

from agentreducer.repository.models import (
    RepositoryFile, FixedTextOverlay, ExternalReadIsolation,
    RepositoryVisibilityEvidence, _validate_namespace,
)

@pytest.mark.parametrize("path", [
    "", ".", "..", "../x", "a/../b", "C:x", "C:/x", "/x", "//server/share/x",
    r"a\b", "a//b", "a/./b", "a/", "NUL", "aux.txt", "COM1", "LPT9.txt",
    "a:ads", "a.", "a ", "a/<b", "a/?", "a/\0b",
])
def test_unsafe_or_aliased_path_rejected(path):
    with pytest.raises((ValueError, TypeError)):
        RepositoryFile(path, "0"*64, 0, 0o644)

@pytest.mark.parametrize("code", range(1, 32))
@pytest.mark.parametrize("kind", ["file", "overlay"])
def test_ascii_control_paths_rejected_during_preparation(code, kind):
    path = "a" + chr(code) + "b"
    with pytest.raises(ValueError):
        if kind == "file":
            RepositoryFile(path, "0" * 64, 0, 0o644)
        else:
            FixedTextOverlay(path, "fixed")

@pytest.mark.parametrize("digest,size,mode", [
    ("x"*64,0,0o644), ("A"*64,0,0o644), ("0"*63,0,0o644),
    ("0"*64,True,0o644), ("0"*64,-1,0o644), ("0"*64,1.0,0o644),
    ("0"*64,0,True), ("0"*64,0,-1), ("0"*64,0,0o10000),
])
def test_invalid_identity_metadata_rejected(digest, size, mode):
    with pytest.raises((TypeError, ValueError)):
        RepositoryFile("nested/a.bin",digest,size,mode)

def test_file_is_frozen_and_nested_dot_spelling_preserved():
    file=RepositoryFile(".github/workflows/check.yml","0"*64,3,0o644)
    assert file.relative_path == ".github/workflows/check.yml"
    assert hash(file)
    with pytest.raises(FrozenInstanceError):
        file.size=4

@pytest.mark.parametrize("files,dirs", [
    (["A","a"],[]), (["caf\u00e9","cafe\u0301"],[]),
    (["a","a/b"],["a"]), (["A/x","a/y"],["A","a"]),
    (["d/x"],[]), (["d"],["d"]), ([],["A","a"]),
])
def test_namespace_collision_or_missing_parent_rejected(files,dirs):
    with pytest.raises(ValueError):
        _validate_namespace(files,dirs)

def test_overlay_normalized_bytes_hidden_from_repr():
    overlay=FixedTextOverlay("AGENTS.md","secret\r\nnext\rlast")
    assert overlay.text == "secret\nnext\nlast"
    assert overlay.digest == hashlib.sha256(b"secret\nnext\nlast").hexdigest()
    assert overlay.size == 16
    assert "secret" not in repr(overlay)
    with pytest.raises(FrozenInstanceError):
        overlay.text="other"

def test_default_visibility_does_not_claim_projection_or_read_isolation():
    evidence=RepositoryVisibilityEvidence()
    assert not evidence.workspace_projection_verified
    assert evidence.external_read_isolation is ExternalReadIsolation.UNKNOWN
    assert "no applicable" in evidence.qualification.lower()

def test_projected_workspace_is_not_global_read_isolation():
    evidence=RepositoryVisibilityEvidence(True,ExternalReadIsolation.NOT_GUARANTEED)
    assert evidence.workspace_projection_verified
    assert "does not guarantee" in evidence.qualification.lower()
    assert "observed" in evidence.qualification.lower()
    with pytest.raises(FrozenInstanceError):
        evidence.workspace_projection_verified=False

@pytest.mark.parametrize("verified,isolation", [
    (1,ExternalReadIsolation.UNKNOWN), (False,"unknown"),
    (True,ExternalReadIsolation.VERIFIED_RESTRICTED),
])
def test_visibility_requires_strict_types_and_cannot_certify_restricted(verified,isolation):
    with pytest.raises((ValueError,TypeError)):
        RepositoryVisibilityEvidence(verified,isolation)

from agentreducer.repository.models import (
    RepositoryManifest, RepositoryCandidate, RepositoryReductionResult, _snapshot_payload,
)

def sample_manifest(**kwargs):
    return RepositoryManifest((
        RepositoryFile("auth","0"*64,2,0o644),
        RepositoryFile("cache","1"*64,3,0o644),
        RepositoryFile("checker","2"*64,7,0o644),
    ), **kwargs)

def test_manifest_candidate_semantic_identity_ignores_source(tmp_path):
    left=sample_manifest(_source=tmp_path/"private-source-one")
    right=sample_manifest(_source=tmp_path/"private-source-two")
    assert left.snapshot_id == right.snapshot_id
    assert left.candidate() == right.candidate()
    assert hash(left.candidate()) == hash(right.candidate())
    assert str(tmp_path) not in repr(left)
    assert str(tmp_path) not in repr(left.candidate())
    assert str(tmp_path) not in str(_snapshot_payload(left))
    assert left.candidate().identity == (left.snapshot_id,("auth","cache","checker"))

def test_manifest_copies_mutable_inputs_and_separates_explicit_pins():
    files=[RepositoryFile("a","0"*64,1,0o644)]
    pins=["a"]; dirs=[]; exclusions=[".git"]
    manifest=RepositoryManifest(files,pins,dirs,exclusions)
    files.clear();pins.clear();dirs.append("other");exclusions.clear()
    assert manifest.files[0].relative_path == "a"
    assert manifest.pinned_paths == frozenset({"a"})
    assert manifest.reducible_paths == ()
    assert manifest.exclusions == frozenset({".git"})
    with pytest.raises(FrozenInstanceError):
        manifest.files=()

@pytest.mark.parametrize("paths", [
    ("cache","auth"),("auth","auth"),("missing",),("checker",),("AUTH",),("auth/.",),
])
def test_candidate_membership_and_order_strict(paths):
    manifest=sample_manifest(pinned_paths=("checker",))
    with pytest.raises((ValueError,TypeError)):
        manifest.candidate(paths)

def test_candidate_snapshots_sequence_and_empty_valid():
    manifest=sample_manifest()
    paths=["auth","cache"]
    candidate=RepositoryCandidate(manifest,paths)
    paths.clear()
    assert candidate.files == ("auth","cache")
    assert manifest.candidate(()).files == ()

@pytest.mark.parametrize("kwargs", [
    {"pinned_paths":("missing",)}, {"pinned_paths":("auth","auth")},
    {"directories":("auth",)}, {"exclusions":(".",)}, {"exclusions":("a/b",)},
    {"files":(RepositoryFile("b","0"*64,1,0o644),RepositoryFile("a","0"*64,1,0o644))},
])
def test_invalid_manifest_configuration_rejected(kwargs):
    with pytest.raises((ValueError,TypeError)):
        if "files" in kwargs:
            RepositoryManifest(**kwargs)
        else:
            sample_manifest(**kwargs)

def test_nested_manifest_requires_complete_directories():
    file=RepositoryFile("a/b/c","0"*64,4,0o644)
    manifest=RepositoryManifest((file,),directories=("a","a/b"))
    assert manifest.candidate().files == ("a/b/c",)
    with pytest.raises(ValueError):
        RepositoryManifest((file,),directories=("a",))

def test_snapshot_identity_changes_when_experiment_changes():
    ordinary=sample_manifest()
    assert ordinary.snapshot_id != sample_manifest(pinned_paths=("checker",)).snapshot_id
    assert ordinary.snapshot_id != sample_manifest(exclusions=()).snapshot_id
    different=RepositoryManifest((RepositoryFile("auth","3"*64,2,0o644),))
    assert ordinary.snapshot_id != different.snapshot_id

def test_statistics_distinguish_candidate_pinned_and_complete_namespace():
    manifest=sample_manifest(pinned_paths=("checker",))
    result=RepositoryReductionResult(manifest.candidate(),manifest.candidate(("auth",)),4)
    assert (result.original_file_count,result.reduced_file_count)==(2,1)
    assert (result.original_bytes,result.reduced_bytes,result.pinned_bytes)==(5,2,7)
    assert (result.total_managed_file_count,result.total_managed_bytes)==(3,12)
    assert (result.pinned_file_count,result.retained_bytes)==(1,9)
    assert result.file_reduction_percentage == 50
    assert result.byte_reduction_percentage == 60
    assert not result.visibility.workspace_projection_verified
    assert "projected initial workspace" in result.qualification
    assert "configured oracle" in result.qualification
    assert "unreadable elsewhere" in result.qualification
    assert "causal" in result.qualification
    with pytest.raises(FrozenInstanceError):
        result.evaluations=8

def test_original_subset_and_zero_denominators():
    manifest=sample_manifest(pinned_paths=("checker",))
    subset=manifest.candidate(("cache",))
    result=RepositoryReductionResult(subset,manifest.candidate(()),2)
    assert result.original_bytes == 3
    assert result.retained_bytes == 7
    empty=RepositoryReductionResult(manifest.candidate(()),manifest.candidate(()),1)
    assert empty.file_reduction_percentage == empty.byte_reduction_percentage == 0

@pytest.mark.parametrize("count",[-1,True,1.0])
def test_invalid_evaluation_count_rejected(count):
    manifest=sample_manifest()
    with pytest.raises((ValueError,TypeError)):
        RepositoryReductionResult(manifest.candidate(),manifest.candidate(()),count)

def test_reduced_must_be_ordered_subset_of_original_same_snapshot():
    manifest=sample_manifest()
    with pytest.raises(ValueError):
        RepositoryReductionResult(manifest.candidate(("auth",)),manifest.candidate(("cache",)),2)
    with pytest.raises(ValueError):
        RepositoryReductionResult(manifest.candidate(),RepositoryManifest(()).candidate(),2)
