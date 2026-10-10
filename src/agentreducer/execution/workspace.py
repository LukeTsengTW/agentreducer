"""Fresh disposable copies of a trusted, stable source directory."""

import shutil
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

DEFAULT_EXCLUSIONS = frozenset({
    ".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "node_modules",
})


def _reject_link(path: Path) -> None:
    """Reject symlinks and Windows reparse points, including junctions."""
    info = path.lstat()
    reparse = getattr(info, "st_file_attributes", 0) & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
    )
    if stat.S_ISLNK(info.st_mode) or reparse:
        raise ValueError(f"Symbolic link or reparse point is not supported: {path}")


@dataclass(frozen=True)
class Workspace:
    """A directory valid only within its template's create() context."""

    path: Path


@dataclass(frozen=True)
class WorkspaceTemplate:
    """Copy source anew per trial; callers must keep that source unchanged.

    Exclusions replace the defaults and match entry basenames at every depth.
    No gitignore interpretation or blanket dotfile exclusion is performed.
    """

    source: Path
    exclusions: frozenset[str] = DEFAULT_EXCLUSIONS

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", Path(self.source).absolute())
        object.__setattr__(self, "exclusions", frozenset(self.exclusions))

    @contextmanager
    def create(self) -> Iterator[Workspace]:
        """Validate, copy, yield, and clean up on normal and exceptional exits."""
        _reject_link(self.source)
        if not self.source.is_dir():
            raise NotADirectoryError(f"Source is not a directory: {self.source}")
        source = self.source.resolve(strict=True)
        temporary = tempfile.TemporaryDirectory(prefix="agentreducer-")
        cancellation: BaseException | None = None
        try:
            root = Path(temporary.name).resolve()
            if root.is_relative_to(source):
                raise ValueError("Temporary workspace must be outside the source tree.")
            destination = root / "workspace"
            shutil.copytree(
                source, destination, symlinks=True, ignore=self._ignore_entries
            )
            yield Workspace(destination)
        except BaseException as exc:
            if not isinstance(exc, Exception):
                cancellation = exc
            raise
        finally:
            try:
                temporary.cleanup()
            except BaseException as cleanup_error:
                if cancellation is None:
                    raise
                cancellation.add_note(
                    "Workspace cleanup failed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )

    def _ignore_entries(self, directory: str, names: list[str]) -> set[str]:
        ignored = set(names) & self.exclusions
        for name in sorted(set(names) - ignored):
            _reject_link(Path(directory) / name)
        return ignored
