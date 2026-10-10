"""Owned-source raw snapshots: no source execution or implicit file ownership."""
import hashlib
import os
from pathlib import Path
import subprocess
import pytest

from agentreducer.repository.manifest import build_repository_manifest
from agentreducer.execution.workspace import DEFAULT_EXCLUSIONS

def files(manifest):
    return tuple(f.relative_path for f in manifest.files)

def test_raw_binary_dotfiles_sorted_explicit_pins(tmp_path):
    (tmp_path/".editorconfig").write_bytes(b"root=true")
    (tmp_path/"blob.bin").write_bytes(b"\0\xff\x80")
    (tmp_path/"pyproject.toml").write_bytes(b"not automatically pinned")
    (tmp_path/".git").mkdir();(tmp_path/".git"/"ignore").write_bytes(b"ignored")
    manifest=build_repository_manifest(tmp_path,pinned_paths=("blob.bin",))
    assert files(manifest)==(".editorconfig","blob.bin","pyproject.toml")
    assert manifest.files[1].digest==hashlib.sha256(b"\0\xff\x80").hexdigest()
    assert manifest.files[1].size==3
    assert manifest.reducible_paths==(".editorconfig","pyproject.toml")
    assert manifest.pinned_paths==frozenset({"blob.bin"})
    assert manifest.exclusions==DEFAULT_EXCLUSIONS

def test_all_depth_exclusions_and_empty_directories(tmp_path):
    (tmp_path/"src").mkdir();(tmp_path/"src"/"empty").mkdir()
    (tmp_path/"src"/"a.py").write_bytes(b"print('not executed')")
    (tmp_path/".github").mkdir();(tmp_path/".github"/"config").write_bytes(b"x")
    for name in DEFAULT_EXCLUSIONS:
        (tmp_path/"src"/name).mkdir()
        (tmp_path/"src"/name/"ignore").write_bytes(b"ignored")
    manifest=build_repository_manifest(tmp_path)
    assert files(manifest)==(".github/config","src/a.py")
    assert manifest.directories==(".github","src","src/empty")

def test_custom_exclusions_replace_defaults(tmp_path):
    (tmp_path/".git").mkdir();(tmp_path/".git"/"keep").write_bytes(b"x")
    (tmp_path/"ignore").write_bytes(b"x")
    manifest=build_repository_manifest(tmp_path,exclusions=("ignore",))
    assert files(manifest)==(".git/keep",)
    assert manifest.exclusions==frozenset({"ignore"})

@pytest.mark.parametrize("pins",[("missing",),("dir",),(".git/ignored",),("a","a"),("A",)])
def test_invalid_exact_pin_configuration(tmp_path,pins):
    (tmp_path/"a").write_bytes(b"a");(tmp_path/"dir").mkdir()
    (tmp_path/".git").mkdir();(tmp_path/".git"/"ignored").write_bytes(b"x")
    with pytest.raises((ValueError,TypeError)):
        build_repository_manifest(tmp_path,pinned_paths=pins)

def test_empty_and_stable_repeated_snapshot(tmp_path):
    empty=build_repository_manifest(tmp_path)
    assert files(empty)==()
    assert empty.candidate().files==()
    (tmp_path/"a").write_bytes(b"a")
    first=build_repository_manifest(tmp_path)
    second=build_repository_manifest(tmp_path)
    assert first==second
    assert first.snapshot_id==second.snapshot_id

def test_large_file_raw_digest_and_no_source_execution(tmp_path,monkeypatch):
    data=b"\0\xffpayload"*300000
    (tmp_path/"large.bin").write_bytes(data)
    (tmp_path/"poison.py").write_text("raise RuntimeError('must never import')",encoding="utf-8")
    def forbidden(*args,**kwargs): raise AssertionError("scanner must never execute")
    monkeypatch.setattr(subprocess,"Popen",forbidden)
    manifest=build_repository_manifest(tmp_path)
    assert manifest.files[0].size==len(data)
    assert manifest.files[0].digest==hashlib.sha256(data).hexdigest()

@pytest.mark.parametrize("name",["NUL.txt","bad.",r"a\b","a:stream"])
def test_nonportable_source_names_rejected_where_creatable(tmp_path,name):
    path=tmp_path/name
    try:
        path.write_bytes(b"x")
        if not path.is_file() or name not in {p.name for p in tmp_path.iterdir()}:
            pytest.skip("filesystem does not represent this spelling")
    except OSError:
        pytest.skip("filesystem rejects spelling")
    with pytest.raises((ValueError,RuntimeError)):
        build_repository_manifest(tmp_path)

def test_included_symlink_rejected_excluded_symlink_skipped(tmp_path):
    source=tmp_path/"source";source.mkdir()
    target=tmp_path/"target";target.write_bytes(b"external")
    link=source/"linked"
    try: link.symlink_to(target)
    except OSError as exc: pytest.skip(f"symlink privilege unavailable: {exc.winerror}")
    with pytest.raises((ValueError,RuntimeError)):
        build_repository_manifest(source)
    link.rename(source/".git")
    assert files(build_repository_manifest(source))==()

def test_hard_links_are_independent_manifest_entries(tmp_path):
    a=tmp_path/"a";a.write_bytes(b"same")
    try: os.link(a,tmp_path/"b")
    except OSError: pytest.skip("hard links unavailable")
    manifest=build_repository_manifest(tmp_path)
    assert files(manifest)==("a","b")
    assert [f.size for f in manifest.files]==[4,4]

def test_source_read_error_diagnostic_hides_absolute_anchor(tmp_path,monkeypatch):
    source=tmp_path/"secret-source-canary";source.mkdir()
    target=source/"a";target.write_bytes(b"a")
    original=Path.open
    def broken(path,*args,**kwargs):
        if path==target: raise OSError(f"denied: {source}")
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,"open",broken)
    with pytest.raises(RuntimeError) as caught:
        build_repository_manifest(source)
    assert str(source) not in str(caught.value)

def test_stat_apis_may_have_distinct_stable_ctime_but_real_drift_still_checked(tmp_path,monkeypatch):
    """Cross-API ctime differs on this Windows runtime; compare each API to itself."""
    from types import SimpleNamespace
    import agentreducer.repository.manifest as module
    (tmp_path/"a").write_bytes(b"a")
    real_fstat=module.os.fstat
    def fstat_with_distinct_ctime(fd):
        info=real_fstat(fd)
        values={name:getattr(info,name) for name in dir(info) if name.startswith("st_")}
        values["st_ctime_ns"]+=1000000
        return SimpleNamespace(**values)
    monkeypatch.setattr(module.os,"fstat",fstat_with_distinct_ctime)
    assert files(build_repository_manifest(tmp_path))==("a",)

from agentreducer.repository.manifest import _validate_source, _validate_copy, _scan

@pytest.mark.parametrize("change",["content","replace","add","remove","type","mode"])
def test_source_included_drift_rejected(tmp_path,change):
    source=tmp_path/"source";source.mkdir()
    target=source/"a";target.write_bytes(b"same")
    manifest=build_repository_manifest(source)
    mode=target.stat().st_mode
    if change=="content": target.write_bytes(b"changed")
    elif change=="replace":
        replacement=source/"replacement";replacement.write_bytes(b"same");os.replace(replacement,target)
    elif change=="add": (source/"extra").write_bytes(b"new")
    elif change=="remove": target.unlink()
    elif change=="type": target.unlink();target.mkdir()
    elif change=="mode":
        target.chmod(mode & ~0o222)
        if target.stat().st_mode==mode: pytest.skip("mode mutation unavailable")
    try:
        with pytest.raises(RuntimeError):
            _validate_source(manifest)
    finally:
        if change=="mode": target.chmod(mode)

def test_excluded_tree_changes_do_not_invalidate_parent_timestamps(tmp_path):
    (tmp_path/"a").write_bytes(b"a")
    (tmp_path/"nested").mkdir()
    manifest=build_repository_manifest(tmp_path)
    for parent in [tmp_path,tmp_path/"nested"]:
        (parent/".git").mkdir();(parent/".git"/"new").write_bytes(b"ignored")
    _validate_source(manifest)

@pytest.mark.parametrize("change",["extra-file","missing-file","digest","directory","excluded-extra"])
def test_copy_complete_namespace_and_bytes_rejected_on_mismatch(tmp_path,change):
    import shutil
    source=tmp_path/"source";source.mkdir()
    (source/"a").write_bytes(b"a");(source/"empty").mkdir()
    manifest=build_repository_manifest(source)
    copy=tmp_path/"copy";shutil.copytree(source,copy)
    if change=="extra-file": (copy/"extra").write_bytes(b"x")
    elif change=="missing-file": (copy/"a").unlink()
    elif change=="digest": (copy/"a").write_bytes(b"b")
    elif change=="directory": (copy/"extra").mkdir()
    elif change=="excluded-extra": (copy/".git").mkdir()
    with pytest.raises(RuntimeError):
        _validate_copy(manifest,copy)

def test_copy_identity_is_not_original_inode_or_ctime(tmp_path):
    import shutil
    source=tmp_path/"source";source.mkdir();(source/"a").write_bytes(b"a")
    manifest=build_repository_manifest(source)
    copy=tmp_path/"copy";shutil.copytree(source,copy)
    assert (source/"a").stat().st_ino != (copy/"a").stat().st_ino
    _validate_copy(manifest,copy)

def test_observable_mutation_during_build_rejected(tmp_path,monkeypatch):
    import agentreducer.repository.manifest as module
    target=tmp_path/"a";target.write_bytes(b"a")
    original=module._read_file; once=False
    def changing(path,relative):
        nonlocal once
        result=original(path,relative)
        if not once:
            once=True;target.write_bytes(b"changed")
        return result
    monkeypatch.setattr(module,"_read_file",changing)
    with pytest.raises(RuntimeError):
        build_repository_manifest(tmp_path)

@pytest.mark.parametrize("root_reparse",[True,False])
def test_mocked_windows_reparse_rejected_without_following(tmp_path,monkeypatch,root_reparse):
    from types import SimpleNamespace
    (tmp_path/"a").write_bytes(b"a")
    original=Path.lstat
    blocked=tmp_path if root_reparse else tmp_path/"a"
    def reparse(path,*args,**kwargs):
        info=original(path,*args,**kwargs)
        if path != blocked: return info
        values={name:getattr(info,name) for name in dir(info) if name.startswith("st_")}
        values["st_file_attributes"]=getattr(info,"st_file_attributes",0)|0x400
        return SimpleNamespace(**values)
    monkeypatch.setattr(Path,"lstat",reparse)
    with pytest.raises(RuntimeError):
        build_repository_manifest(tmp_path)

def test_nonregular_included_object_rejected_without_read(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import stat
    target=tmp_path/"fifo";target.write_bytes(b"x")
    original=Path.lstat
    def nonregular(path,*args,**kwargs):
        info=original(path,*args,**kwargs)
        if path!=target:return info
        values={name:getattr(info,name) for name in dir(info) if name.startswith("st_")}
        values["st_mode"]=stat.S_IFIFO|0o600
        return SimpleNamespace(**values)
    monkeypatch.setattr(Path,"lstat",nonregular)
    with pytest.raises(RuntimeError):
        build_repository_manifest(tmp_path)
