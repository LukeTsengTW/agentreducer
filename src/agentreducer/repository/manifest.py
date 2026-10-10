"""Deterministic raw snapshots of a trusted quiescent source; never execute it."""
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import stat

from ..execution.workspace import DEFAULT_EXCLUSIONS
from .models import RepositoryFile, RepositoryManifest, _canonical_path, _exclusions, _validate_namespace


def _lstat(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise RuntimeError("Repository links and reparse points are unsupported.")
    return info


def _identity(info, *, directory=False):
    basic = (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), stat.S_IMODE(info.st_mode))
    return basic if directory else basic + (info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_file(path, relative):
    before = _lstat(path)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        # Windows runtimes can report different ctime semantics through these
        # APIs. Check times within each API; compare other identity across both.
        if _identity(before)[:-1] != _identity(opened)[:-1]:
            raise RuntimeError("Repository file changed while opening.")
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        finished = os.fstat(stream.fileno())
    after = _lstat(path)
    identity = _identity(before)
    if (identity != _identity(after) or _identity(opened) != _identity(finished)
            or size != before.st_size):
        raise RuntimeError("Repository file changed while hashing.")
    return RepositoryFile(relative, digest.hexdigest(), size, stat.S_IMODE(before.st_mode)), identity


@dataclass(frozen=True)
class _Tree:
    files: tuple
    directories: tuple
    identities: tuple = field(repr=False)
    root_identity: tuple = field(repr=False)


def _scan(root, exclusions):
    try:
        info = _lstat(root)
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("Repository root must be a directory.")
        files, directories, identities = [], [], []

        def walk(directory, prefix):
            for entry in sorted(directory.iterdir(), key=lambda p: p.name):
                if entry.name in exclusions:
                    continue
                relative = f"{prefix}/{entry.name}" if prefix else entry.name
                _canonical_path(relative)
                child = _lstat(entry)
                if stat.S_ISDIR(child.st_mode):
                    directories.append(relative)
                    identities.append((relative, _identity(child, directory=True)))
                    walk(entry, relative)
                elif stat.S_ISREG(child.st_mode):
                    record, fingerprint = _read_file(entry, relative)
                    files.append(record)
                    identities.append((relative, fingerprint))
                else:
                    raise RuntimeError("Repository entry must be a regular file or directory.")

        walk(root, "")
        files.sort(key=lambda f: f.relative_path)
        directories.sort()
        _validate_namespace((f.relative_path for f in files), directories)
        return _Tree(tuple(files), tuple(directories), tuple(sorted(identities)),
                     _identity(info, directory=True))
    except (OSError, ValueError):
        raise RuntimeError("Repository snapshot I/O or namespace validation failed.") from None


def build_repository_manifest(source, *, pinned_paths=(), exclusions=DEFAULT_EXCLUSIONS):
    """Snapshot raw bytes/namespace with observable drift checks, no source execution."""
    exclusions = _exclusions(exclusions)
    try:
        source = Path(source).absolute()
        _lstat(source)
        source = source.resolve(strict=True)
        tree = _scan(source, exclusions)
        if tree != _scan(source, exclusions):
            raise RuntimeError("Repository changed while building its snapshot.")
    except OSError:
        raise RuntimeError("Repository source could not be read.") from None
    return RepositoryManifest(tree.files, pinned_paths, tree.directories, exclusions,
                              source, tree.root_identity, tree.identities)


def _provenance(manifest):
    if (not isinstance(manifest, RepositoryManifest) or manifest._source is None
            or not manifest._root_identity):
        raise ValueError("Repository source provenance is required.")


def _validate_source(manifest):
    _provenance(manifest)
    current = _scan(manifest._source, manifest.exclusions)
    if (current.files != manifest.files or current.directories != manifest.directories
            or current.identities != manifest._identities
            or current.root_identity != manifest._root_identity):
        raise RuntimeError("Repository source no longer matches its snapshot.")


def _validate_copy(manifest, root):
    current = _scan(Path(root), frozenset())
    if current.files != manifest.files or current.directories != manifest.directories:
        raise RuntimeError("Disposable repository copy does not match its snapshot.")


class _SafeWorkspaceContext:
    """Only M3 enter/exit infrastructure crosses the sanitization boundary."""
    def __init__(self, template):
        self._template = template
        self._inner = None
        self._used = False

    def __enter__(self):
        if self._used:
            raise RuntimeError("Repository workspace context cannot be reused.")
        self._used = True
        try:
            self._inner = self._template.create()
            return self._inner.__enter__()
        except Exception:
            raise RuntimeError("Repository workspace operation failed.") from None

    def __exit__(self, exc_type, exc, traceback):
        try:
            return self._inner.__exit__(exc_type, exc, traceback)
        except Exception as boundary_error:
            if boundary_error is exc:
                raise
            raise RuntimeError("Repository workspace operation failed.") from None


@dataclass(frozen=True)
class _RepositoryWorkspace:
    _template: object = field(repr=False, compare=False, hash=False)

    def create(self):
        return _SafeWorkspaceContext(self._template)


def _workspace_template(manifest):
    from ..execution.workspace import WorkspaceTemplate
    _provenance(manifest)
    return _RepositoryWorkspace(WorkspaceTemplate(manifest._source, manifest.exclusions))
