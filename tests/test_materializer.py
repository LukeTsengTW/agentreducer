import os
from pathlib import Path

import pytest


def test_text_materialization_creates_parents_and_normalizes_utf8_lf(tmp_path: Path) -> None:
    from agentreducer.execution import TextFileMaterializer

    TextFileMaterializer("nested/input.txt", lambda text: text).materialize("嗨\r\nnext\rend", tmp_path)
    assert (tmp_path / "nested" / "input.txt").read_bytes() == "嗨\nnext\nend".encode("utf-8")
    assert sorted(p.name for p in (tmp_path / "nested").iterdir()) == ["input.txt"]


def test_candidate_overwrites_only_disposable_copy(tmp_path: Path) -> None:
    from agentreducer.execution import TextFileMaterializer, WorkspaceTemplate

    (tmp_path / "AGENTS.md").write_bytes(b"original")
    with WorkspaceTemplate(tmp_path).create() as workspace:
        TextFileMaterializer("AGENTS.md", str).materialize("candidate", workspace.path)
        assert (workspace.path / "AGENTS.md").read_bytes() == b"candidate"
    assert (tmp_path / "AGENTS.md").read_bytes() == b"original"
    assert not workspace.path.exists()


@pytest.mark.parametrize("relative_path", [
    "", ".", "../outside", "nested/../../outside", "/root/file", "//server/share/file",
    "C:\\outside", "C:outside", "\\outside", "\\\\server\\share\\file",
    "..\\outside", "nested\\..\\outside", "file:stream", "nested/file:stream", "\0",
    "nested/.. /outside", "nested/..../outside", "input.txt.", "input.txt ",
    "NUL", "con.txt", "nested/COM1.log",
])
def test_unsafe_portable_paths_are_rejected_before_writing(tmp_path: Path, relative_path: str) -> None:
    from agentreducer.execution import TextFileMaterializer

    with pytest.raises(ValueError):
        TextFileMaterializer(relative_path, str).materialize("candidate", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_windows_relative_separators_have_portable_meaning(tmp_path: Path) -> None:
    from agentreducer.execution import TextFileMaterializer

    TextFileMaterializer("nested\\input.txt", str).materialize("candidate", tmp_path)
    assert (tmp_path / "nested" / "input.txt").read_bytes() == b"candidate"


@pytest.mark.parametrize("directory", [False, True])
def test_existing_symlink_destination_cannot_change_outside_files(tmp_path: Path, directory: bool) -> None:
    from agentreducer.execution import TextFileMaterializer

    root, outside = tmp_path / "workspace", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    target = outside / "file.txt"
    target.write_bytes(b"untouched")
    link = root / "linked"
    try:
        link.symlink_to(outside if directory else target, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Host cannot create symbolic links: {exc}")
    destination = "linked/file.txt" if directory else "linked"
    with pytest.raises(ValueError, match="link|reparse"):
        TextFileMaterializer(destination, str).materialize("candidate", root)
    assert target.read_bytes() == b"untouched"


def test_atomic_replacement_does_not_modify_other_hardlink_names(tmp_path: Path) -> None:
    from agentreducer.execution import TextFileMaterializer

    original, root = tmp_path / "original.txt", tmp_path / "workspace"
    original.write_bytes(b"original")
    root.mkdir()
    try:
        os.link(original, root / "input.txt")
    except OSError as exc:
        pytest.skip(f"Host cannot create hard links: {exc}")
    TextFileMaterializer("input.txt", str).materialize("candidate", root)
    assert original.read_bytes() == b"original"
    assert (root / "input.txt").read_bytes() == b"candidate"
    assert sorted(p.name for p in root.iterdir()) == ["input.txt"]


def test_failed_replacement_removes_staging_and_preserves_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentreducer.execution import TextFileMaterializer

    (tmp_path / "input.txt").write_bytes(b"original")

    def fail_replace(source, destination):
        assert Path(source).parent == tmp_path
        assert Path(source).read_bytes() == b"candidate"
        raise OSError("replacement unavailable")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement unavailable"):
        TextFileMaterializer("input.txt", str).materialize("candidate", tmp_path)
    assert (tmp_path / "input.txt").read_bytes() == b"original"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["input.txt"]


@pytest.mark.parametrize("renderer", [lambda candidate: 1, lambda candidate: None])
def test_invalid_rendered_type_does_not_create_output(tmp_path: Path, renderer) -> None:
    from agentreducer.execution import TextFileMaterializer

    with pytest.raises(TypeError):
        TextFileMaterializer("nested/input.txt", renderer).materialize("candidate", tmp_path)
    assert list(tmp_path.iterdir()) == []
