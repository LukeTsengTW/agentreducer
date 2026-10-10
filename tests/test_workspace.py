import shutil
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest


def make_symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Host cannot create symbolic links: {exc}")


def test_fresh_copies_preserve_source_and_cleanup(tmp_path: Path) -> None:
    from agentreducer.execution import WorkspaceTemplate

    source = tmp_path / "source"
    source.mkdir()
    (source / "state.txt").write_bytes(b"original")
    template = WorkspaceTemplate(source)
    with template.create() as first:
        assert first.path != source
        (first.path / "state.txt").write_bytes(b"changed")
        (first.path / "new.txt").write_bytes(b"new")
    assert not first.path.exists()
    with template.create() as second:
        assert second.path != first.path
        assert (second.path / "state.txt").read_bytes() == b"original"
        assert not (second.path / "new.txt").exists()
    assert not second.path.exists()
    assert (source / "state.txt").read_bytes() == b"original"
    assert sorted(p.name for p in source.iterdir()) == ["state.txt"]


def test_workspace_cleanup_when_body_raises(tmp_path: Path) -> None:
    from agentreducer.execution import WorkspaceTemplate

    failure = RuntimeError("trial failed")
    with pytest.raises(RuntimeError) as caught:
        with WorkspaceTemplate(tmp_path).create() as workspace:
            raise failure
    assert caught.value is failure
    assert not workspace.path.exists()
    assert tmp_path.is_dir()


@pytest.mark.parametrize("regular_file", [False, True])
def test_invalid_source_fails_explicitly(tmp_path: Path, regular_file: bool) -> None:
    from agentreducer.execution import WorkspaceTemplate

    source = tmp_path / "invalid"
    if regular_file:
        source.write_bytes(b"not a directory")
    with pytest.raises((FileNotFoundError, NotADirectoryError)):
        with WorkspaceTemplate(source).create():
            pytest.fail("Invalid source was accepted")


def test_source_is_revalidated_when_used(tmp_path: Path) -> None:
    from agentreducer.execution import WorkspaceTemplate

    source = tmp_path / "source"
    source.mkdir()
    template = WorkspaceTemplate(source)
    source.rmdir()
    with pytest.raises(FileNotFoundError):
        with template.create():
            pytest.fail("Deleted source was accepted")


def test_nested_exclusions_preserve_legitimate_dotfiles(tmp_path: Path) -> None:
    from agentreducer.execution import WorkspaceTemplate

    for base in [tmp_path, tmp_path / "nested"]:
        base.mkdir(exist_ok=True)
        for name in [".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"]:
            (base / name).mkdir()
            (base / name / "generated").write_bytes(b"skip")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "ci.yml").write_bytes(b"ci")
    (tmp_path / ".env.example").write_bytes(b"example")
    (tmp_path / "AGENTS.md").write_bytes(b"rules")
    with WorkspaceTemplate(tmp_path).create() as workspace:
        files = sorted(p.relative_to(workspace.path).as_posix() for p in workspace.path.rglob("*") if p.is_file())
        assert files == [".env.example", ".github/ci.yml", "AGENTS.md"]
    assert (tmp_path / "nested" / "node_modules" / "generated").exists()


def test_exclusions_are_configurable_snapshots(tmp_path: Path) -> None:
    from agentreducer.execution import WorkspaceTemplate

    (tmp_path / "skip.txt").write_bytes(b"skip")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "marker").write_bytes(b"keep with replacement exclusions")
    excluded = {"skip.txt"}
    template = WorkspaceTemplate(tmp_path, exclusions=excluded)
    excluded.add(".git")
    with template.create() as workspace:
        assert not (workspace.path / "skip.txt").exists()
        assert (workspace.path / ".git" / "marker").exists()


@pytest.mark.parametrize("directory", [False, True])
def test_nonexcluded_symlinks_are_rejected(tmp_path: Path, directory: bool) -> None:
    from agentreducer.execution import WorkspaceTemplate

    source, target = tmp_path / "source", tmp_path / "outside"
    source.mkdir()
    if directory:
        target.mkdir()
    else:
        target.write_bytes(b"outside")
    make_symlink(source / "linked", target)
    with pytest.raises(ValueError, match="link|reparse"):
        with WorkspaceTemplate(source).create():
            pytest.fail("Source link was copied")


def test_source_itself_cannot_be_a_symlink(tmp_path: Path) -> None:
    from agentreducer.execution import WorkspaceTemplate

    target = tmp_path / "target"
    target.mkdir()
    make_symlink(tmp_path / "source", target)
    with pytest.raises(ValueError, match="link|reparse"):
        with WorkspaceTemplate(tmp_path / "source").create():
            pytest.fail("Linked source was accepted")


def test_excluded_links_are_not_traversed(tmp_path: Path) -> None:
    from agentreducer.execution import WorkspaceTemplate

    source, outside = tmp_path / "source", tmp_path / "outside"
    source.mkdir()
    outside.mkdir()
    (outside / "private.txt").write_bytes(b"untouched")
    make_symlink(source / ".venv", outside)
    with WorkspaceTemplate(source).create() as workspace:
        assert list(workspace.path.iterdir()) == []
    assert (outside / "private.txt").read_bytes() == b"untouched"


def test_windows_reparse_attribute_is_rejected_without_following(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentreducer.execution import WorkspaceTemplate

    original_lstat = Path.lstat

    def lstat(path: Path):
        if path == tmp_path:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ValueError, match="link|reparse"):
        with WorkspaceTemplate(tmp_path).create():
            pytest.fail("Reparse point was accepted")


def test_copy_failure_removes_partial_temporary_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentreducer.execution import WorkspaceTemplate

    source, pool = tmp_path / "source", tmp_path / "pool"
    source.mkdir()
    pool.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(pool))

    def failed_copy(source, destination, **kwargs):
        Path(destination).mkdir()
        (Path(destination) / "partial").write_bytes(b"partial")
        raise OSError("copy unavailable")

    monkeypatch.setattr(shutil, "copytree", failed_copy)
    with pytest.raises(OSError, match="copy unavailable"):
        with WorkspaceTemplate(source).create():
            pytest.fail("Copy error was ignored")
    assert list(pool.iterdir()) == []
    assert list(source.iterdir()) == []


def test_temporary_workspace_inside_source_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentreducer.execution import WorkspaceTemplate

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with pytest.raises(ValueError, match="outside.*source"):
        with WorkspaceTemplate(tmp_path).create():
            pytest.fail("Recursive self-copy was allowed")
    assert list(tmp_path.iterdir()) == []
