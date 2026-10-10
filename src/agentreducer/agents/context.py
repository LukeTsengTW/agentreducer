"""Context qualifications; native controlled isolation is unverified in M4."""

from dataclasses import dataclass
from enum import Enum
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import field
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from ._diagnostics import DiagnosticRedactor


class CodexContextMode(Enum):
    CONTROLLED = "controlled"
    AMBIENT = "ambient"


class ContextAssurance(Enum):
    UNVERIFIED = "unverified"
    AMBIENT = "ambient"
    FAKE_FIXTURE = "fake-fixture"


NATIVE_UNKNOWN_SOURCES = (
    "global instructions", "skills", "plugins", "hooks", "managed injection",
)


def _strings(values, name):
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{name} must be a collection of strings.")
    snapshot = tuple(values)
    if any(not isinstance(value, str) or not value or "\0" in value for value in snapshot):
        raise ValueError(f"{name} must contain nonempty strings without NUL.")
    return snapshot


@dataclass(frozen=True)
class ContextCapabilities:
    """Model-free compatibility observations, never a native isolation proof."""

    invocation: tuple[str, ...]
    version: str | None
    supported_flags: frozenset[str]
    unknown_sources: tuple[str, ...] = NATIVE_UNKNOWN_SOURCES
    errors: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "invocation", _strings(self.invocation, "invocation"))
        object.__setattr__(self, "supported_flags", frozenset(_strings(self.supported_flags, "flags")))
        object.__setattr__(self, "unknown_sources", _strings(self.unknown_sources, "unknown_sources"))
        object.__setattr__(self, "errors", _strings(self.errors, "errors"))
        if self.version is not None and (not isinstance(self.version, str) or not self.version):
            raise ValueError("version must be a nonempty string or None.")


@dataclass(frozen=True)
class AgentContextEvidence:
    """Immutable qualification. Caller metadata cannot enable native execution.

    M4 deliberately has no controlled-verified constructor or assurance value.
    A future native provider needs complete version-qualified capability proof.
    Fake fixtures can describe their own contract without certifying Codex.
    """

    mode: CodexContextMode = CodexContextMode.CONTROLLED
    assurance: ContextAssurance = ContextAssurance.UNVERIFIED
    version: str | None = None
    unknown_sources: tuple[str, ...] = NATIVE_UNKNOWN_SOURCES

    def __post_init__(self):
        if not isinstance(self.mode, CodexContextMode) or not isinstance(self.assurance, ContextAssurance):
            raise TypeError("mode and assurance must be their enum values.")
        if (self.mode is CodexContextMode.AMBIENT) != (self.assurance is ContextAssurance.AMBIENT):
            raise ValueError("AMBIENT mode requires AMBIENT assurance and vice versa.")
        if self.version is not None and (not isinstance(self.version, str) or not self.version):
            raise ValueError("version must be a nonempty string or None.")
        if self.assurance is ContextAssurance.FAKE_FIXTURE and not self.version:
            raise ValueError("Fake evidence must identify its fixture version.")
        object.__setattr__(self, "unknown_sources", _strings(self.unknown_sources, "unknown_sources"))

    @classmethod
    def ambient(cls, *, version=None):
        return cls(CodexContextMode.AMBIENT, ContextAssurance.AMBIENT, version)

    @property
    def native_controlled_verified(self) -> bool:
        return False

    @property
    def qualification(self) -> str:
        if self.mode is CodexContextMode.AMBIENT:
            return ("AMBIENT: context is not controlled and not frozen; correctness is "
                    "conditional on ambient context remaining stable during reduction.")
        if self.assurance is ContextAssurance.FAKE_FIXTURE:
            return "CONTROLLED fake-fixture contract only; native Codex isolation is unverified."
        return "Native CONTROLLED isolation is unavailable/unverified; model execution must fail closed."


_BASE_ENV_KEYS = frozenset({"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL", "LC_CTYPE"})
_PROTECTED_ENV_KEYS = frozenset({
    "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "CODEX_HOME", "APPDATA", "LOCALAPPDATA",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
})
_AUTH_NAMES = frozenset({"OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"})


def _snapshot_env(environment):
    if environment is None:
        return None
    if not isinstance(environment, Mapping):
        raise TypeError("Environment must be a string mapping.")
    snapshot = dict(environment)
    for key, value in snapshot.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError("Environment keys and values must be strings.")
        if not key or "=" in key or "\0" in key or "\0" in value:
            raise ValueError("Invalid environment key or value.")
    return MappingProxyType(snapshot)


@dataclass(frozen=True)
class OwnedAgentEnvironment:
    root: Path
    env: Mapping[str, str] = field(repr=False)

    def __post_init__(self):
        object.__setattr__(self, "env", _snapshot_env(self.env))


@dataclass(frozen=True)
class ControlledEnvironment:
    """Owned auxiliary homes for trusted runners, not native isolation proof.

    No credentials are read, copied or bridged. Candidate instructions must be
    declared AGENTS.md paths; override files and project context selectors are
    rejected. The workspace is already disposable and is never copied here.
    """

    workspace: Path
    declared_instructions: frozenset[str] = frozenset({"AGENTS.md"})
    env: Mapping[str, str] | None = field(default=None, repr=False)
    base_env: Mapping[str, str] | None = field(default=None, repr=False)
    sensitive_env_names: frozenset[str] = _AUTH_NAMES

    def __post_init__(self):
        object.__setattr__(self, "workspace", Path(self.workspace).absolute())
        declarations = _strings(self.declared_instructions, "declared_instructions")
        for name in declarations:
            path = PurePosixPath(name)
            if (path.is_absolute() or "\\" in name or ":" in name
                    or any(part in {"", ".", ".."} for part in name.split("/"))
                    or path.name != "AGENTS.md"):
                raise ValueError("Instruction declarations must be portable relative AGENTS.md paths.")
        object.__setattr__(self, "declared_instructions", frozenset(declarations))
        object.__setattr__(self, "sensitive_env_names", frozenset(_strings(self.sensitive_env_names, "sensitive_env_names")))
        object.__setattr__(self, "env", _snapshot_env(self.env))
        object.__setattr__(self, "base_env", _snapshot_env(self.base_env))
        for key in self.env or {}:
            upper = key.upper()
            if upper in _PROTECTED_ENV_KEYS:
                continue
            if (upper.startswith(("CODEX_", "OPENAI_", "XDG_"))
                    or upper in {"PYTHONPATH", "PYTHONHOME", "BASH_ENV", "ENV", "NODE_OPTIONS", "RUBYOPT"}
                    or re.search(r"(^|_)(CONFIG|INSTRUCTIONS?|SKILLS?|AGENTS|PLUGINS?|HOOKS?)(_|$)", upper)):
                raise ValueError(f"Unexpected context/configuration or authentication override: {key}")

    def _validate_workspace(self):
        if not self.workspace.is_dir():
            raise NotADirectoryError("Controlled workspace must be a directory.")
        for path in (self.workspace, *self.workspace.rglob("*")):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Linked context paths are unsupported.")
            name = path.name.lower()
            if name in {".codex", ".agents", "agents.override.md", "claude.md"}:
                raise ValueError(f"Undeclared context/instruction source: {path.name}")
            if name == "agents.md" and path.relative_to(self.workspace).as_posix() not in self.declared_instructions:
                raise ValueError(f"Undeclared instruction source: {path.name}")
        if (self.workspace / ".agentreducer-root").exists():
            raise ValueError("Owned root marker must not shadow an existing file.")

    @contextmanager
    def create(self):
        self._validate_workspace()
        base = os.environ if self.base_env is None else self.base_env
        redact = DiagnosticRedactor.from_environments(_AUTH_NAMES | self.sensitive_env_names, base, self.env)
        temporary = tempfile.TemporaryDirectory(prefix="agentreducer-home-")
        marker = self.workspace / ".agentreducer-root"
        marker_created = False
        primary = None
        try:
            root = Path(temporary.name).resolve()
            if root.is_relative_to(self.workspace.resolve()):
                raise ValueError("Auxiliary homes must be outside the project workspace.")
            locations = {
                "HOME": root / "home", "USERPROFILE": root / "home",
                "CODEX_HOME": root / "codex", "APPDATA": root / "config",
                "LOCALAPPDATA": root / "local", "XDG_CONFIG_HOME": root / "config",
                "XDG_DATA_HOME": root / "data", "XDG_CACHE_HOME": root / "cache",
                "XDG_STATE_HOME": root / "state",
            }
            for path in set(locations.values()):
                path.mkdir(parents=True, exist_ok=True)
            (locations["CODEX_HOME"] / "config.toml").write_text(
                'project_root_markers = [".agentreducer-root"]\ncli_auth_credentials_store = "ephemeral"\n',
                encoding="utf-8", newline="\n",
            )
            marker.touch(exist_ok=False)
            marker_created = True
            environment = {key: value for key, value in base.items() if key.upper() in _BASE_ENV_KEYS}
            environment.update(self.env or {})
            environment = {key: value for key, value in environment.items() if key.upper() not in _PROTECTED_ENV_KEYS}
            environment.update({key: str(value) for key, value in locations.items()})
            home = str(locations["HOME"])
            drive = locations["HOME"].drive
            environment.update(HOMEDRIVE=drive, HOMEPATH=home[len(drive):])
            yield OwnedAgentEnvironment(root, environment)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            failures = []
            if marker_created:
                try:
                    marker.unlink(missing_ok=True)
                except BaseException as exc:
                    failures.append(exc)
            try:
                temporary.cleanup()
            except BaseException as exc:
                failures.append(exc)
            for failure in failures:
                if primary is not None:
                    primary.add_note(redact(f"Auxiliary cleanup failed: {type(failure).__name__}: {failure}"))
                else:
                    primary = failure
            if failures and primary is failures[0]:
                if isinstance(primary, Exception):
                    raise RuntimeError(redact(f"Auxiliary cleanup failed: {type(primary).__name__}: {primary}")) from None
                redact.notes(primary)
                raise primary
            if primary is not None:
                redact.notes(primary)
