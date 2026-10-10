"""Physical omissions occur in owned copies, never in the anchored source."""
from pathlib import Path
import hashlib
import sys
import pytest

from agentreducer import OracleResult
from agentreducer.repository import build_repository_manifest
from agentreducer.repository.projection import RepositoryProjectionMaterializer
from agentreducer.execution import CommandOracle, CommandSpec

def prepared(tmp_path):
    source=tmp_path/"source";source.mkdir()
    for name,data in {"keep":b"kept\0\xff","omit":b"omitted","checker":b"checker"}.items():
        (source/name).write_bytes(data)
    (source/"empty"/"deep").mkdir(parents=True)
    manifest=build_repository_manifest(source,pinned_paths=("checker",))
    projection=RepositoryProjectionMaterializer(manifest,verifier_paths=("checker",))
    return source,manifest,projection

def contents(root):
    return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob("*") if p.is_file()}

def test_newline_overlay_rejected_before_workspace_or_oracle(tmp_path, monkeypatch):
    from agentreducer.repository import FixedTextOverlay

    source, manifest, _ = prepared(tmp_path)
    before = contents(source)
    calls = []

    def forbidden_workspace(*args, **kwargs):
        calls.append("workspace")
        raise AssertionError("Invalid configuration reached workspace preparation.")

    def forbidden_evaluation(*args, **kwargs):
        calls.append("oracle")
        raise AssertionError("Invalid configuration reached oracle evaluation.")

    monkeypatch.setattr(type(manifest), "workspace_template", forbidden_workspace)
    monkeypatch.setattr(CommandOracle, "evaluate_detailed", forbidden_evaluation)
    with pytest.raises(ValueError):
        projection = RepositoryProjectionMaterializer(
            manifest, verifier_paths=("checker",),
            overlays=(FixedTextOverlay("a\nb", "fixed"),),
        )
        oracle = CommandOracle(
            manifest.workspace_template(),
            CommandSpec((sys.executable, "-c", "raise SystemExit(99)")), projection,
        )
        oracle.evaluate_detailed(manifest.candidate(("keep",)))
    assert calls == []
    assert contents(source) == before

def test_projection_physically_omits_keeps_bytes_and_cleans_empty_dirs(tmp_path):
    source,manifest,projection=prepared(tmp_path);before=contents(source)
    with manifest.workspace_template().create() as workspace:
        projection.materialize(manifest.candidate(("keep",)),workspace.path)
        assert contents(workspace.path)=={"checker":b"checker","keep":b"kept\0\xff"}
        assert not (workspace.path/"omit").exists()
        assert not (workspace.path/"empty").exists()
        copy=workspace.path
    assert contents(source)==before
    assert not copy.exists()
    assert (projection.projection_attempts,projection.completed_projection_checks)==(1,1)

def test_empty_reducible_candidate_retains_pins_and_root(tmp_path):
    source,manifest,projection=prepared(tmp_path)
    with manifest.workspace_template().create() as workspace:
        projection.materialize(manifest.candidate(()),workspace.path)
        assert workspace.path.is_dir()
        assert contents(workspace.path)=={"checker":b"checker"}

def test_truly_empty_repository_projection_valid(tmp_path):
    source=tmp_path/"source";source.mkdir();(source/"empty").mkdir()
    manifest=build_repository_manifest(source)
    projection=RepositoryProjectionMaterializer(manifest,verifier_paths=())
    with manifest.workspace_template().create() as workspace:
        projection.materialize(manifest.candidate(),workspace.path)
        assert list(workspace.path.iterdir())==[]

@pytest.mark.parametrize("which",["same","descendant","ancestor"])
def test_original_source_overlap_rejected_before_mutation(tmp_path,which):
    source,manifest,projection=prepared(tmp_path);before=contents(source)
    if which=="same": destination=source
    elif which=="descendant": destination=source/"child";destination.mkdir()
    else:destination=tmp_path
    with pytest.raises((ValueError,RuntimeError)):
        projection.materialize(manifest.candidate(()),destination)
    assert contents(source)==before
    assert projection.completed_projection_checks==0

@pytest.mark.parametrize("change",["new","missing","digest","excluded"])
def test_mismatched_copy_aborts_before_any_deletion(tmp_path,change):
    source,manifest,projection=prepared(tmp_path)
    with manifest.workspace_template().create() as workspace:
        if change=="new":(workspace.path/"extra").write_bytes(b"extra")
        elif change=="missing":(workspace.path/"keep").unlink()
        elif change=="digest":(workspace.path/"keep").write_bytes(b"wrong")
        elif change=="excluded":(workspace.path/".git").mkdir()
        before=contents(workspace.path)
        with pytest.raises(RuntimeError):
            projection.materialize(manifest.candidate(()),workspace.path)
        assert contents(workspace.path)==before
    assert projection.completed_projection_checks==0

def test_changed_source_aborts_before_projecting_existing_copy(tmp_path):
    source,manifest,projection=prepared(tmp_path)
    with manifest.workspace_template().create() as workspace:
        before=contents(workspace.path)
        (source/"omit").write_bytes(b"changed")
        with pytest.raises(RuntimeError):
            projection.materialize(manifest.candidate(()),workspace.path)
        assert contents(workspace.path)==before

def test_unlink_failure_is_error_no_command_and_source_not_touched(tmp_path,monkeypatch):
    source,manifest,projection=prepared(tmp_path);before=contents(source)
    original=Path.unlink
    def broken(path,*args,**kwargs):
        if path.name=="omit" and not path.is_relative_to(source):
            raise OSError(f"failed: {source}")
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,"unlink",broken)
    oracle=CommandOracle(manifest.workspace_template(),
        CommandSpec((sys.executable,"-c","raise SystemExit(99)")),projection)
    result=oracle.evaluate_detailed(manifest.candidate(()))
    assert result.outcome is OracleResult.ERROR
    assert result.command is None
    assert str(source) not in result.error
    assert contents(source)==before
    assert projection.completed_projection_checks==0

def test_snapshot_mismatch_cannot_project(tmp_path):
    source,manifest,projection=prepared(tmp_path)
    other=build_repository_manifest(source,pinned_paths=())
    with manifest.workspace_template().create() as workspace:
        before=contents(workspace.path)
        with pytest.raises((ValueError,RuntimeError)):
            projection.materialize(other.candidate(),workspace.path)
        assert contents(workspace.path)==before

def test_matching_candidate_uses_projectors_own_source_anchor(tmp_path):
    import shutil
    source,manifest,projection=prepared(tmp_path)
    other_source=tmp_path/"other";shutil.copytree(source,other_source)
    other=build_repository_manifest(other_source,pinned_paths=("checker",))
    assert manifest.snapshot_id==other.snapshot_id
    (other_source/"omit").write_bytes(b"changed elsewhere")
    with manifest.workspace_template().create() as workspace:
        projection.materialize(other.candidate(("keep",)),workspace.path)
        assert contents(workspace.path)=={"checker":b"checker","keep":b"kept\0\xff"}

def test_projector_repr_and_counters_are_readonly(tmp_path):
    source,manifest,projection=prepared(tmp_path)
    assert str(source) not in repr(projection)
    with pytest.raises(AttributeError):
        projection.projection_attempts=9

from agentreducer.repository import FixedTextOverlay

def test_reducible_overlay_is_preparation_error(tmp_path):
    source,manifest,projection=prepared(tmp_path)
    with pytest.raises(ValueError):
        RepositoryProjectionMaterializer(manifest,verifier_paths=(),
            overlays=(FixedTextOverlay("keep","fixed"),))

def test_pinned_agents_requires_exact_explicit_replacement_permission(tmp_path):
    source=tmp_path/"source";source.mkdir()
    (source/"AGENTS.md").write_bytes(b"original")
    manifest=build_repository_manifest(source,pinned_paths=("AGENTS.md",))
    overlay=FixedTextOverlay("AGENTS.md","fixed\r\ninstructions")
    with pytest.raises(ValueError):
        RepositoryProjectionMaterializer(manifest,verifier_paths=(),overlays=(overlay,))
    projection=RepositoryProjectionMaterializer(manifest,verifier_paths=(),overlays=(overlay,),
        replace_pinned_paths=("AGENTS.md",))
    with manifest.workspace_template().create() as workspace:
        projection.materialize(manifest.candidate(),workspace.path)
        assert contents(workspace.path)=={"AGENTS.md":b"fixed\ninstructions"}
    assert (source/"AGENTS.md").read_bytes()==b"original"

def test_absent_fixed_overlay_creates_explicit_canonical_parents(tmp_path):
    source,manifest,projection=prepared(tmp_path)
    projection=RepositoryProjectionMaterializer(manifest,verifier_paths=("checker",),
        overlays=(FixedTextOverlay("fixed/AGENTS.md","fixed"),))
    with manifest.workspace_template().create() as workspace:
        projection.materialize(manifest.candidate(()),workspace.path)
        assert contents(workspace.path)=={"checker":b"checker","fixed/AGENTS.md":b"fixed"}
        assert {p.relative_to(workspace.path).as_posix() for p in workspace.path.rglob("*") if p.is_dir()}=={"fixed"}

@pytest.mark.parametrize("paths",[
    ("keep",),("missing",),("empty",),("checker","checker"),
])
def test_verifier_dependencies_must_be_unique_exact_pinned_files(tmp_path,paths):
    source,manifest,projection=prepared(tmp_path)
    with pytest.raises((TypeError,ValueError)):
        RepositoryProjectionMaterializer(manifest,verifier_paths=paths)

def test_verifier_paths_explicit_required_and_external_empty_allowed(tmp_path):
    source,manifest,projection=prepared(tmp_path)
    with pytest.raises(TypeError):
        RepositoryProjectionMaterializer(manifest)
    RepositoryProjectionMaterializer(manifest,verifier_paths=())

@pytest.mark.parametrize("overlays,replace",[
    ((FixedTextOverlay("checker","wrong"),),("checker",)),
    ((),("checker",)),
    ((FixedTextOverlay("new","x"),),("missing",)),
    ((FixedTextOverlay("new","x"),FixedTextOverlay("NEW","y")),()),
    ((FixedTextOverlay("new","x"),FixedTextOverlay("new/child","y")),()),
    ((FixedTextOverlay("empty","x"),),()),
    ((FixedTextOverlay("keep/child","x"),),()),
    ((FixedTextOverlay(".git/metadata","x"),),()),
    ((FixedTextOverlay("EMPTY/new","x"),),()),
])
def test_overlay_alias_prefix_exclusion_or_undeclared_permission_rejected(tmp_path,overlays,replace):
    source,manifest,projection=prepared(tmp_path)
    with pytest.raises((TypeError,ValueError)):
        RepositoryProjectionMaterializer(manifest,verifier_paths=("checker",),
            overlays=overlays,replace_pinned_paths=replace)

def test_atomic_overlay_failure_aborts_command_without_source_mutation(tmp_path,monkeypatch):
    from agentreducer.execution.materializer import TextFileMaterializer
    source,manifest,projection=prepared(tmp_path);before=contents(source)
    projection=RepositoryProjectionMaterializer(manifest,verifier_paths=("checker",),
        overlays=(FixedTextOverlay("AGENTS.md","fixed"),))
    def broken(*args,**kwargs):raise OSError(f"failed: {source}")
    monkeypatch.setattr(TextFileMaterializer,"materialize",broken)
    oracle=CommandOracle(manifest.workspace_template(),
        CommandSpec((sys.executable,"-c","raise SystemExit(99)")),projection)
    result=oracle.evaluate_detailed(manifest.candidate(()))
    assert result.outcome is OracleResult.ERROR and result.command is None
    assert str(source) not in result.error
    assert contents(source)==before
    assert projection.projection_attempts==1 and projection.completed_projection_checks==0

def test_hardlink_source_preserved_by_omission_and_atomic_overlay(tmp_path):
    import os
    source=tmp_path/"source";source.mkdir();(source/"AGENTS.md").write_bytes(b"same")
    try:os.link(source/"AGENTS.md",source/"omit")
    except OSError:pytest.skip("hardlinks unavailable")
    manifest=build_repository_manifest(source,pinned_paths=("AGENTS.md",))
    projection=RepositoryProjectionMaterializer(manifest,verifier_paths=(),
        overlays=(FixedTextOverlay("AGENTS.md","replacement"),),
        replace_pinned_paths=("AGENTS.md",))
    with manifest.workspace_template().create() as workspace:
        projection.materialize(manifest.candidate(()),workspace.path)
        assert contents(workspace.path)=={"AGENTS.md":b"replacement"}
    assert contents(source)=={"AGENTS.md":b"same","omit":b"same"}

def test_final_overlay_bytes_must_be_verified_before_completion(tmp_path,monkeypatch):
    from agentreducer.execution.materializer import TextFileMaterializer
    source,manifest,projection=prepared(tmp_path);before=contents(source)
    projection=RepositoryProjectionMaterializer(manifest,verifier_paths=(),
        overlays=(FixedTextOverlay("AGENTS.md","fixed"),))
    original=TextFileMaterializer.materialize
    def corrupt(materializer,candidate,workspace):
        original(materializer,candidate,workspace)
        (workspace/"AGENTS.md").write_bytes(b"corrupted")
    monkeypatch.setattr(TextFileMaterializer,"materialize",corrupt)
    oracle=CommandOracle(manifest.workspace_template(),
        CommandSpec((sys.executable,"-c","raise SystemExit(99)")),projection)
    result=oracle.evaluate_detailed(manifest.candidate(()))
    assert result.outcome is OracleResult.ERROR and result.command is None
    assert (projection.projection_attempts,projection.completed_projection_checks)==(1,0)
    assert contents(source)==before

def test_contained_target_rejects_reparse_ancestor_before_unlink(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from agentreducer.repository.projection import _target
    root=tmp_path/"copy";(root/"nested").mkdir(parents=True)
    target=root/"nested"/"a";target.write_bytes(b"safe")
    original=Path.lstat
    def reparse(path,*args,**kwargs):
        info=original(path,*args,**kwargs)
        if path!=root/"nested":return info
        values={name:getattr(info,name) for name in dir(info) if name.startswith("st_")}
        values["st_file_attributes"]=getattr(info,"st_file_attributes",0)|0x400
        return SimpleNamespace(**values)
    monkeypatch.setattr(Path,"lstat",reparse)
    with pytest.raises(RuntimeError):_target(root,"nested/a")
    assert target.read_bytes()==b"safe"
