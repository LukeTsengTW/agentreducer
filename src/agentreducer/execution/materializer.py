"""Typed candidate materialization with portable paths and atomic text writes."""

import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Generic, Protocol, TypeVar

from .workspace import _reject_link

T = TypeVar("T")
T_contra = TypeVar("T_contra", contravariant=True)
_WINDOWS_DEVICES = frozenset({
    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
    *(f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "123456789¹²³"),
})


class CandidateMaterializer(Protocol[T_contra]):
    """Apply a candidate only inside the supplied disposable workspace."""

    def materialize(self, candidate: T_contra, workspace: Path) -> None:
        ...


def _relative_file_path(value: str | Path) -> Path:
    raw = os.fspath(value)
    if not isinstance(raw, str):
        raise TypeError("relative_path must be a text path.")
    windows = PureWindowsPath(raw)
    normalized = raw.replace("\\", "/")
    if not raw or "\0" in raw or windows.drive or windows.root or normalized.startswith("/"):
        raise ValueError("Destination must be a nonempty relative file path.")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if not parts:
        raise ValueError("Destination must name a file.")
    for part in parts:
        if (part == ".." or part.endswith((" ", "."))
                or any(character in part for character in ':<>"|?*')
                or part.split(".")[0].rstrip(" ").upper() in _WINDOWS_DEVICES):
            raise ValueError("Unsafe or nonportable destination path.")
    return Path(*parts)


@dataclass(frozen=True)
class TextFileMaterializer(Generic[T]):
    """Render UTF-8/LF text and replace a file inside a trusted workspace.

    Both separator styles are accepted; absolute, traversal, link, reparse,
    stream, and Windows device/alias destinations are rejected on every host.
    Replacement breaks preexisting hard links rather than editing their targets.
    """

    relative_path: Path
    render: Callable[[T], str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "relative_path", _relative_file_path(self.relative_path))

    def _destination(self, root: Path) -> Path:
        destination = root
        for part in self.relative_path.parts:
            destination = destination / part
            try:
                _reject_link(destination)
            except FileNotFoundError:
                pass
        if not destination.resolve().is_relative_to(root):
            raise ValueError("Destination escapes the disposable workspace.")
        return destination

    def materialize(self, candidate: T, workspace: Path) -> None:
        root = Path(workspace)
        _reject_link(root)
        if not root.is_dir():
            raise NotADirectoryError(f"Workspace is not a directory: {root}")
        root = root.resolve(strict=True)
        destination = self._destination(root)
        text = self.render(candidate)
        if not isinstance(text, str):
            raise TypeError("Text renderer must return a string.")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._destination(root)
        staging: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=destination.parent,
                prefix=".agentreducer-", delete=False,
            ) as stream:
                staging = Path(stream.name)
                stream.write(text)
            os.replace(staging, destination)
        finally:
            if staging is not None:
                staging.unlink(missing_ok=True)
