"""Single-destination preflight and atomic publication."""

from dataclasses import dataclass, field
from pathlib import Path
import os
import stat
import tempfile


class OutputFailure(Exception):
    def __init__(self, code="OUTPUT_FAILED", *, published=False):
        super().__init__(code)
        self.code, self.published = code, published


@dataclass(frozen=True)
class OutputPlan:
    path: Path = field(repr=False)
    force: bool
    kind: str
    source: Path = field(repr=False)
    protected_inputs: tuple[Path, ...] = field(repr=False)


def _links(path):
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OutputFailure("OUTPUT_PATH_UNSAFE")


def _validate(plan):
    path = plan.path
    _links(path)
    if not path.parent.is_dir() or path.is_dir():
        raise OutputFailure("OUTPUT_PATH_UNSAFE")
    resolved = path.resolve()
    if resolved == plan.source or resolved.is_relative_to(plan.source):
        raise OutputFailure("OUTPUT_PROTECTED")
    for protected in plan.protected_inputs:
        if resolved == protected.resolve():
            raise OutputFailure("OUTPUT_PROTECTED")
        if path.exists() and protected.exists() and os.path.samefile(path, protected):
            raise OutputFailure("OUTPUT_PROTECTED")
    if path.exists() and not plan.force:
        raise OutputFailure("OUTPUT_EXISTS")
    if path.exists() and not stat.S_ISREG(path.lstat().st_mode):
        raise OutputFailure("OUTPUT_PATH_UNSAFE")


def prepare_output(path, *, force, kind, source, protected_inputs=()):
    try:
        if kind not in ("report", "reduced") or type(force) is not bool:
            raise OutputFailure("OUTPUT_PLAN_INVALID")
        raw = Path(path).absolute()
        _links(raw)
        plan = OutputPlan(raw, force, kind, Path(source).resolve(strict=True),
                          tuple(Path(p).absolute() for p in protected_inputs))
        _validate(plan)
        return plan
    except (OSError, ValueError, TypeError):
        raise OutputFailure("OUTPUT_PLAN_INVALID") from None


def publish(plan, data):
    """Publish complete bytes after close; default link is atomic no-clobber.

    Only our randomly named staging file is removed. Failure after publication
    leaves the complete destination in place. Primary cancellation is retained.
    """
    staging = None
    descriptor = None
    published = False
    primary = None
    try:
        if not isinstance(plan, OutputPlan) or type(data) is not bytes:
            raise OutputFailure("OUTPUT_PLAN_INVALID")
        _validate(plan)
        descriptor, name = tempfile.mkstemp(dir=plan.path.parent,prefix=".agentreducer-output-")
        staging=Path(name)
        stream=os.fdopen(descriptor,"wb")
        descriptor=None
        body_error=None
        try:
            if stream.write(data) != len(data):
                raise OSError("Incomplete output write")
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException as exc:
            body_error=exc
            raise
        finally:
            try:
                stream.close()
            except BaseException:
                if body_error is None:
                    raise
        _validate(plan)
        if plan.force:
            os.replace(staging,plan.path)
        else:
            os.link(staging,plan.path,follow_symlinks=False)
        published=True
    except BaseException as exc:
        primary=exc
        if isinstance(exc,OutputFailure):
            raise
        if isinstance(exc,Exception):
            raise OutputFailure("OUTPUT_PUBLISH_FAILED",published=published) from None
        raise
    finally:
        try:
            if descriptor is not None:
                os.close(descriptor)
            if staging is not None:
                staging.unlink(missing_ok=True)
        except BaseException as cleanup:
            if primary is None:
                if isinstance(cleanup,Exception):
                    raise OutputFailure("OUTPUT_CLEANUP_FAILED",published=published) from None
                raise
