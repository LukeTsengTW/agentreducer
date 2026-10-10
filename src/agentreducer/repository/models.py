"""Immutable repository identities; workspace projection is not read isolation."""
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import unicodedata

from ..execution.materializer import _relative_file_path


def _canonical_path(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("Repository paths must be strings.")
    if any(ord(character) < 32 for character in value):
        raise ValueError("Repository paths must not contain ASCII control characters.")
    if "\\" in value or _relative_file_path(value).as_posix() != value:
        raise ValueError("Repository path must use its canonical relative spelling.")
    return value


def _collision_key(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def _parents(path: str) -> set[str]:
    parts = path.split("/")
    return {"/".join(parts[:i]) for i in range(1, len(parts))}


def _validate_namespace(file_paths, directories) -> None:
    files, dirs = tuple(file_paths), tuple(directories)
    all_paths = files + dirs
    keys = set()
    for path in all_paths:
        _canonical_path(path)
        key = _collision_key(path)
        if key in keys:
            raise ValueError("Repository namespace contains a duplicate or alias.")
        keys.add(key)
    directory_set = set(dirs)
    for path in all_paths:
        if not _parents(path) <= directory_set:
            raise ValueError("Repository namespace has a file prefix or missing parent.")


def _integer(value, name, maximum=None):
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        raise ValueError(f"{name} must be a nonnegative strict integer in range.")


@dataclass(frozen=True)
class RepositoryFile:
    relative_path: str
    digest: str
    size: int
    mode: int

    def __post_init__(self):
        _canonical_path(self.relative_path)
        if (not isinstance(self.digest, str) or len(self.digest) != 64
                or any(c not in "0123456789abcdef" for c in self.digest)):
            raise ValueError("File digest must be lowercase SHA-256 hexadecimal.")
        _integer(self.size, "File size")
        _integer(self.mode, "File mode", 0o7777)


@dataclass(frozen=True)
class FixedTextOverlay:
    relative_path: str
    text: str = field(repr=False)

    def __post_init__(self):
        _canonical_path(self.relative_path)
        if not isinstance(self.text, str):
            raise TypeError("Overlay text must be a string.")
        text = self.text.replace("\r\n", "\n").replace("\r", "\n")
        text.encode("utf-8")
        object.__setattr__(self, "text", text)

    @property
    def digest(self):
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def size(self):
        return len(self.text.encode("utf-8"))


class ExternalReadIsolation(Enum):
    UNKNOWN = "unknown"
    NOT_GUARANTEED = "not-guaranteed"
    VERIFIED_RESTRICTED = "verified-restricted"


@dataclass(frozen=True)
class RepositoryVisibilityEvidence:
    workspace_projection_verified: bool = False
    external_read_isolation: ExternalReadIsolation = ExternalReadIsolation.UNKNOWN

    def __post_init__(self):
        if type(self.workspace_projection_verified) is not bool:
            raise TypeError("Projection verification must be bool.")
        if not isinstance(self.external_read_isolation, ExternalReadIsolation):
            raise TypeError("External read isolation must be its enum.")
        if self.external_read_isolation is ExternalReadIsolation.VERIFIED_RESTRICTED:
            raise ValueError("Restricted read assurance is unavailable in M6 v0.1.")

    @property
    def qualification(self):
        projection = ("Initial workspace projection was observed and verified."
                      if self.workspace_projection_verified else
                      "Initial workspace projection was not established by observation.")
        external = (
            "No applicable external read isolation assurance was established."
            if self.external_read_isolation is ExternalReadIsolation.UNKNOWN else
            "This execution contract does not guarantee external read isolation; "
            "this does not imply external reads were observed."
        )
        return projection + " " + external


import json
from pathlib import Path

from ..execution.workspace import DEFAULT_EXCLUSIONS


def _values(values, name):
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{name} must be a collection, not a string.")
    return tuple(values)


def _exclusions(values):
    names = _values(values, "Exclusions")
    for name in names:
        _canonical_path(name)
        if "/" in name:
            raise ValueError("Exclusions must be exact basenames.")
    return frozenset(names)


def _snapshot_payload(manifest):
    return {
        "version": 1,
        "files": [[f.relative_path, f.digest, f.size, f.mode] for f in manifest.files],
        "directories": list(manifest.directories),
        "pinned_paths": sorted(manifest.pinned_paths),
        "exclusions": sorted(manifest.exclusions),
    }


@dataclass(frozen=True)
class RepositoryManifest:
    files: tuple[RepositoryFile, ...] = ()
    pinned_paths: frozenset[str] = frozenset()
    directories: tuple[str, ...] = ()
    exclusions: frozenset[str] = DEFAULT_EXCLUSIONS
    _source: Path | None = field(default=None, repr=False, compare=False, hash=False)
    _root_identity: tuple = field(default=(), repr=False, compare=False, hash=False)
    _identities: tuple = field(default=(), repr=False, compare=False, hash=False)
    snapshot_id: str = field(init=False)

    def __post_init__(self):
        files = _values(self.files, "Files")
        if not all(isinstance(f, RepositoryFile) for f in files):
            raise TypeError("Manifest files must be RepositoryFile objects.")
        paths = tuple(f.relative_path for f in files)
        dirs = _values(self.directories, "Directories")
        if paths != tuple(sorted(paths)) or dirs != tuple(sorted(dirs)):
            raise ValueError("Manifest entries must be sorted by canonical path.")
        _validate_namespace(paths, dirs)
        pins = _values(self.pinned_paths, "Pins")
        if len(set(pins)) != len(pins):
            raise ValueError("Pinned paths must be unique.")
        for pin in pins:
            _canonical_path(pin)
        if not set(pins) <= set(paths):
            raise ValueError("Pinned paths must name included regular files.")
        exclusions = _exclusions(self.exclusions)
        if any(set(path.split("/")) & exclusions for path in paths + dirs):
            raise ValueError("Managed entries cannot have excluded basenames.")
        object.__setattr__(self, "files", files)
        object.__setattr__(self, "directories", dirs)
        object.__setattr__(self, "pinned_paths", frozenset(pins))
        object.__setattr__(self, "exclusions", exclusions)
        object.__setattr__(self, "_source",
                           None if self._source is None else Path(self._source))
        object.__setattr__(self, "_root_identity", tuple(self._root_identity))
        object.__setattr__(self, "_identities",
                           tuple((path, tuple(identity)) for path, identity in self._identities))
        payload = json.dumps(_snapshot_payload(self), ensure_ascii=True,
                             sort_keys=True, separators=(",", ":")).encode("ascii")
        object.__setattr__(self, "snapshot_id", hashlib.sha256(payload).hexdigest())

    @property
    def reducible_paths(self):
        return tuple(f.relative_path for f in self.files if f.relative_path not in self.pinned_paths)

    def candidate(self, paths=None):
        return RepositoryCandidate(self, self.reducible_paths if paths is None else paths)

    def workspace_template(self):
        from .manifest import _workspace_template
        return _workspace_template(self)


@dataclass(frozen=True)
class RepositoryCandidate:
    manifest: RepositoryManifest
    files: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.manifest, RepositoryManifest):
            raise TypeError("Candidate requires RepositoryManifest.")
        files = _values(self.files, "Candidate files")
        order = {path: i for i, path in enumerate(self.manifest.reducible_paths)}
        previous = -1
        for path in files:
            _canonical_path(path)
            if path not in order or order[path] <= previous:
                raise ValueError("Candidate paths must be a unique ordered reducible subset.")
            previous = order[path]
        object.__setattr__(self, "files", files)

    @property
    def identity(self):
        return self.manifest.snapshot_id, self.files


_REPOSITORY_QUALIFICATION = (
    "A 1-minimal retained set of managed files in the projected initial workspace "
    "under the configured oracle, conditional on that oracle's determinism; "
    "failure identity is exactly what that oracle recognizes, not causal root-bug equivalence. "
    "Fixed pins, overlays, exclusions and canonical parent directories are outside "
    "file-presence minimality. Retained source bytes are unchanged before explicit overlays. "
    "This does not prove omitted source files were unreadable elsewhere on the host. "
    "With repeated trials minimality is policy-relative and observation-relative."
)


@dataclass(frozen=True)
class RepositoryReductionResult:
    original: RepositoryCandidate
    reduced: RepositoryCandidate
    evaluations: int
    visibility: RepositoryVisibilityEvidence = field(default_factory=RepositoryVisibilityEvidence)

    def __post_init__(self):
        if not isinstance(self.original, RepositoryCandidate) or not isinstance(self.reduced, RepositoryCandidate):
            raise TypeError("Reduction result requires repository candidates.")
        _integer(self.evaluations, "Evaluations")
        if not isinstance(self.visibility, RepositoryVisibilityEvidence):
            raise TypeError("Result visibility must be RepositoryVisibilityEvidence.")
        if self.original.manifest.snapshot_id != self.reduced.manifest.snapshot_id:
            raise ValueError("Result candidates must have the same snapshot.")
        if not set(self.reduced.files) <= set(self.original.files):
            raise ValueError("Reduced candidate must be a subset of original.")

    def _bytes(self, paths):
        selected = set(paths)
        return sum(f.size for f in self.original.manifest.files if f.relative_path in selected)

    @property
    def original_file_count(self):
        return len(self.original.files)

    @property
    def reduced_file_count(self):
        return len(self.reduced.files)

    @property
    def original_bytes(self):
        return self._bytes(self.original.files)

    @property
    def reduced_bytes(self):
        return self._bytes(self.reduced.files)

    @property
    def total_managed_file_count(self):
        return len(self.original.manifest.files)

    @property
    def total_managed_bytes(self):
        return sum(f.size for f in self.original.manifest.files)

    @property
    def pinned_file_count(self):
        return len(self.original.manifest.pinned_paths)

    @property
    def pinned_bytes(self):
        return self._bytes(self.original.manifest.pinned_paths)

    @property
    def retained_bytes(self):
        return self.pinned_bytes + self.reduced_bytes

    @property
    def file_reduction_percentage(self):
        count = self.original_file_count
        return 100.0 * (count-self.reduced_file_count)/count if count else 0.0

    @property
    def byte_reduction_percentage(self):
        count = self.original_bytes
        return 100.0 * (count-self.reduced_bytes)/count if count else 0.0

    @property
    def qualification(self):
        return _REPOSITORY_QUALIFICATION + " " + self.visibility.qualification
