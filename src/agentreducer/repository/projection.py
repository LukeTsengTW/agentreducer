"""Validate and project only an owned disposable copy; original bytes are read-only."""
from pathlib import Path
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock, get_ident

from ..execution.materializer import TextFileMaterializer
from .manifest import _lstat, _provenance, _scan, _validate_copy, _validate_source
from .models import (FixedTextOverlay, RepositoryCandidate, _canonical_path,
                     _parents, _validate_namespace, _values)


def _target(root, relative):
    _canonical_path(relative)
    if not stat.S_ISDIR(_lstat(root).st_mode):
        raise RuntimeError("Projection root must remain a directory.")
    current = root
    parts = relative.split("/")
    for index, part in enumerate(parts):
        current = current / part
        info = _lstat(current)
        if index < len(parts)-1 and not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("Projection ancestor must be a directory.")
        resolved = current.resolve(strict=True)
        if not resolved.is_relative_to(root) or resolved == root:
            raise RuntimeError("Projection target escaped its disposable root.")
    return current


@dataclass
class _Observation:
    identity: tuple
    attempts: int = 0
    completed: int = 0
    contract_failed: bool = False

    @property
    def verified(self):
        return self.attempts > 0 and self.attempts == self.completed and not self.contract_failed


class RepositoryProjectionMaterializer:
    """Serial projection configuration with honest cumulative attempt/check counts."""
    def __init__(self, manifest, *, verifier_paths, overlays=(), replace_pinned_paths=()):
        _provenance(manifest)
        verifier_paths = _values(verifier_paths, "Verifier paths")
        replacements = _values(replace_pinned_paths, "Replacement paths")
        overlays = _values(overlays, "Overlays")
        for paths in (verifier_paths, replacements):
            for path in paths:
                _canonical_path(path)
            if len(set(paths)) != len(paths):
                raise ValueError("Infrastructure path declarations must be unique.")
        if not set(verifier_paths) <= manifest.pinned_paths:
            raise ValueError("Verifier dependencies must be included pinned files.")
        if not all(isinstance(overlay, FixedTextOverlay) for overlay in overlays):
            raise TypeError("Overlays must be FixedTextOverlay objects.")
        paths = tuple(overlay.relative_path for overlay in overlays)
        if len(set(paths)) != len(paths):
            raise ValueError("Overlay paths must be unique.")
        original = {f.relative_path for f in manifest.files}
        for path in paths:
            if path in original and path not in manifest.pinned_paths:
                raise ValueError("An overlay cannot own a reducible path.")
            if set(path.split("/")) & manifest.exclusions:
                raise ValueError("An overlay cannot use excluded basenames.")
        pinned_replacements = set(paths) & manifest.pinned_paths
        if set(replacements) != pinned_replacements:
            raise ValueError("Pinned overlay replacements require exact explicit permission.")
        if set(paths) & set(verifier_paths):
            raise ValueError("Verifier dependencies cannot be overlaid.")
        new_paths = set(paths) - original
        directories = set(manifest.directories)
        for path in new_paths:
            directories.update(_parents(path))
        _validate_namespace(sorted(original | new_paths), sorted(directories))
        self._manifest = manifest
        self._verifier_paths = frozenset(verifier_paths)
        self._overlays = overlays
        self._attempts = 0
        self._completed = 0
        self._scope_lock = Lock()
        self._materialize_lock = Lock()
        self._scope = None
        self._scope_owner = None

    @property
    def manifest(self):
        return self._manifest

    @property
    def projection_attempts(self):
        return self._attempts

    @property
    def completed_projection_checks(self):
        return self._completed

    def _candidate(self, candidate):
        if (not isinstance(candidate, RepositoryCandidate)
                or candidate.manifest.snapshot_id != self._manifest.snapshot_id):
            raise ValueError("Projection candidate requires the configured snapshot.")
        return self._manifest.candidate(candidate.files)

    @contextmanager
    def _observe(self, candidate):
        candidate = self._candidate(candidate)
        if not self._scope_lock.acquire(blocking=False):
            if self._scope is not None:
                self._scope.contract_failed = True
            raise RuntimeError("Concurrent or nested projection observation is unsupported.")
        if not self._materialize_lock.acquire(blocking=False):
            self._scope_lock.release()
            raise RuntimeError("Projection is already materializing.")
        scope = _Observation(candidate.identity)
        self._scope, self._scope_owner = scope, get_ident()
        self._materialize_lock.release()
        try:
            yield scope
        finally:
            self._scope, self._scope_owner = None, None
            self._scope_lock.release()
        if scope.contract_failed:
            raise RuntimeError("Observed projection violated the candidate or serial contract.")

    def materialize(self, candidate, workspace):
        if not self._materialize_lock.acquire(blocking=False):
            if self._scope is not None:
                self._scope.contract_failed = True
            raise RuntimeError("Concurrent or recursive materialization is unsupported.")
        try:
            self._attempts += 1
            scope = self._scope
            if scope is not None:
                scope.attempts += 1
                if self._scope_owner != get_ident():
                    scope.contract_failed = True
                    raise RuntimeError("Projection observation belongs to another thread.")
            try:
                candidate = self._candidate(candidate)
            except Exception:
                if scope is not None:
                    scope.contract_failed = True
                raise
            if scope is not None and candidate.identity != scope.identity:
                scope.contract_failed = True
                raise RuntimeError("Observed projection candidate does not match classification.")
            self._materialize(candidate, workspace)
            self._completed += 1
            if scope is not None:
                scope.completed += 1
        finally:
            self._materialize_lock.release()

    def _materialize(self, candidate, workspace):
        try:
            root = Path(workspace)
            if not stat.S_ISDIR(_lstat(root).st_mode):
                raise RuntimeError("Projection root must be a directory.")
            root = root.resolve(strict=True)
            source = self._manifest._source
            if root == source or root.is_relative_to(source) or source.is_relative_to(root):
                raise RuntimeError("Projection destination overlaps original source.")
            _validate_source(self._manifest)
            _validate_copy(self._manifest, root)
            keep = set(candidate.files) | self._manifest.pinned_paths
            overlay_by_path = {overlay.relative_path: overlay for overlay in self._overlays}
            expected_paths = keep | set(overlay_by_path)
            wanted_dirs = set().union(*(_parents(path) for path in expected_paths)) if expected_paths else set()
            omitted = [f.relative_path for f in self._manifest.files if f.relative_path not in keep]
            for relative in omitted:
                if not stat.S_ISREG(_lstat(_target(root, relative)).st_mode):
                    raise RuntimeError("Omitted projection target must remain a regular file.")
            for relative in omitted:
                _target(root, relative).unlink()
            for directory in sorted(self._manifest.directories, key=lambda p: (-p.count("/"), p)):
                if directory not in wanted_dirs:
                    _target(root, directory).rmdir()
            for overlay in self._overlays:
                TextFileMaterializer(overlay.relative_path, lambda _: overlay.text).materialize(candidate, root)
            final = _scan(root, frozenset())
            expected = {f.relative_path: f for f in self._manifest.files if f.relative_path in keep}
            if ({f.relative_path for f in final.files} != expected_paths
                    or final.directories != tuple(sorted(wanted_dirs))):
                raise RuntimeError("Final projected namespace or bytes are incorrect.")
            for record in final.files:
                overlay = overlay_by_path.get(record.relative_path)
                valid = ((record.digest, record.size) == (overlay.digest, overlay.size)
                         if overlay else record == expected[record.relative_path])
                if not valid:
                    raise RuntimeError("Final projected file bytes or metadata are incorrect.")
        except OSError:
            raise RuntimeError("Repository projection operation failed.") from None
