"""Explicit native CLI invocation. Construction never starts a process."""

import math
import os
import shutil
import subprocess
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from types import MappingProxyType

from .context import AgentContextEvidence, CodexContextMode, ContextCapabilities, _strings
from .models import AgentRunResult
from ._diagnostics import DiagnosticRedactor, normalize_output
from ..execution.models import CommandResult

_WINDOWS = os.name == "nt"
DEFAULT_SENSITIVE_ENV_NAMES = frozenset({"OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"})
_SHELLS = {"cmd", "powershell", "pwsh", "bash", "sh", "zsh", "fish", "wsl", "dash"}
REQUIRED_FLAGS = frozenset({
    "--ask-for-approval", "--model", "--sandbox", "--skip-git-repo-check",
    "--ephemeral", "--ignore-user-config", "--color",
})


@dataclass(frozen=True)
class CodexSpec:
    model: str
    argv_prefix: tuple[str, ...] | None = None
    timeout_seconds: float = 120.0
    env: Mapping[str, str] | None = field(default=None, repr=False)
    context_mode: CodexContextMode = CodexContextMode.CONTROLLED
    sensitive_env_names: frozenset[str] = DEFAULT_SENSITIVE_ENV_NAMES

    def __post_init__(self):
        if not isinstance(self.model, str):
            raise TypeError("model must be a string.")
        if not self.model or "\0" in self.model or any(char.isspace() for char in self.model):
            raise ValueError("model must be a nonempty identifier without whitespace or NUL.")
        if self.argv_prefix is not None:
            prefix = _strings(self.argv_prefix, "argv_prefix")
            if not prefix:
                raise ValueError("argv_prefix cannot be empty.")
            object.__setattr__(self, "argv_prefix", prefix)
        timeout = self.timeout_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("timeout_seconds must be a number.")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout_seconds must be positive and finite.")
        if not isinstance(self.context_mode, CodexContextMode):
            raise TypeError("context_mode must be a CodexContextMode.")
        names = _strings(self.sensitive_env_names, "sensitive_env_names")
        object.__setattr__(self, "sensitive_env_names", frozenset(names))
        if self.env is not None:
            if not isinstance(self.env, Mapping):
                raise TypeError("env must be a string mapping.")
            env = dict(self.env)
            for key, value in env.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    raise TypeError("Environment keys and values must be strings.")
                if not key or "=" in key or "\0" in key or "\0" in value:
                    raise ValueError("Invalid environment key or value.")
            object.__setattr__(self, "env", MappingProxyType(env))


def _reject_launcher_name(executable):
    name = PureWindowsPath(executable).name.lower()
    if PureWindowsPath(name).suffix in {".bat", ".cmd"}:
        raise ValueError("Direct batch launchers are unsupported.")
    if name.removesuffix(".exe") in _SHELLS:
        raise ValueError("Explicit shell launchers are unsupported.")


def _resolve_invocation(prefix):
    if prefix is None:
        executable = shutil.which("codex.exe" if _WINDOWS else "codex")
        if executable is None:
            if _WINDOWS and (shutil.which("codex.cmd") or shutil.which("codex.bat")):
                raise ValueError("A native Codex executable is required; batch launchers are unsupported.")
            raise FileNotFoundError("Native Codex executable not found; supply an absolute trusted argv_prefix.")
        prefix = (executable,)
    executable = Path(prefix[0])
    _reject_launcher_name(prefix[0])
    if not executable.is_absolute():
        raise ValueError("Explicit native executable must be an absolute path.")
    executable = executable.resolve(strict=True)
    _reject_launcher_name(str(executable))
    if not executable.is_file():
        raise ValueError("Native executable must be a file.")
    if not _WINDOWS and not os.access(executable, os.X_OK):
        raise ValueError("Native executable is not executable.")
    return (str(executable), *prefix[1:])


@dataclass(frozen=True)
class CodexRunner:
    spec: CodexSpec
    invocation: tuple[str, ...] = field(init=False)
    _context: AgentContextEvidence = field(init=False, repr=False)

    def __post_init__(self):
        if not isinstance(self.spec, CodexSpec):
            raise TypeError("spec must be CodexSpec.")
        object.__setattr__(self, "invocation", _resolve_invocation(self.spec.argv_prefix))
        context = (AgentContextEvidence.ambient() if self.spec.context_mode is CodexContextMode.AMBIENT
                   else AgentContextEvidence())
        object.__setattr__(self, "_context", context)

    @property
    def context(self):
        return self._context

    def _redactor(self):
        return DiagnosticRedactor.from_environments(
            DEFAULT_SENSITIVE_ENV_NAMES | self.spec.sensitive_env_names, os.environ, self.spec.env,
        )

    def sanitize_diagnostic(self, text: str) -> str:
        """Optional generic-oracle hook, including errors after this runner exits."""
        return self._redactor()(text)

    def _environment(self):
        environment = os.environ.copy()
        environment.update(self.spec.env or {})
        return environment

    def preflight(self, *, cwd: Path) -> ContextCapabilities:
        """Explicit bounded version/help probes; never authenticates or runs a task.

        Flag availability does not prove context isolation, model access or auth.
        No preflight report, including caller-built reports, enables CONTROLLED.
        """
        redact = self._redactor()
        outputs = {}
        errors = []
        for args in (("--version",), ("--help",), ("exec", "--help")):
            try:
                process = subprocess.run(
                    (*self.invocation, *args), cwd=cwd, env=self._environment(),
                    shell=False, stdin=subprocess.DEVNULL, capture_output=True,
                    text=True, encoding="utf-8", errors="replace",
                    timeout=min(self.spec.timeout_seconds, 10.0), check=False,
                )
                if process.returncode != 0:
                    errors.append(f"Preflight {args}: exit {process.returncode}: {process.stderr}")
                else:
                    outputs[args] = normalize_output(process.stdout)
            except Exception as exc:
                errors.append(f"Preflight {args}: {type(exc).__name__}: {exc}")
        global_help = outputs.get(("--help",), "")
        exec_help = outputs.get(("exec", "--help"), "")
        flags = frozenset(flag for flag in REQUIRED_FLAGS if re.search(
            re.escape(flag) + r"(?=\s|[=,\]]|$)",
            global_help if flag == "--ask-for-approval" else exec_help,
        ))
        missing = REQUIRED_FLAGS - flags
        if missing:
            errors.append("Missing required flags: " + ", ".join(sorted(missing)))
        version = outputs.get(("--version",), "").strip() or None
        return ContextCapabilities(
            tuple(redact(arg) for arg in self.invocation),
            redact(version) if version else None, flags,
            errors=tuple(redact(error) for error in errors),
        )

    def run(self, *, workspace: Path, prompt: str) -> AgentRunResult:
        if self.spec.context_mode is CodexContextMode.CONTROLLED:
            return AgentRunResult(None, "Native CONTROLLED context isolation is unavailable; no task was launched.", self.context)
        redact = self._redactor()
        argv = (*self.invocation, "--ask-for-approval", "never", "exec", "--model", self.spec.model,
                "--sandbox", "workspace-write", "--skip-git-repo-check", "--ephemeral",
                "--ignore-user-config", "--color", "never", "-")
        started = time.perf_counter()
        try:
            if not isinstance(prompt, str):
                raise TypeError("prompt must be a string.")
            process = subprocess.run(
                argv, cwd=workspace, env=self._environment(), shell=False, input=prompt.encode("utf-8"),
                capture_output=True,
                timeout=self.spec.timeout_seconds, check=False,
            )
            command = CommandResult(argv, process.returncode, normalize_output(process.stdout),
                                    normalize_output(process.stderr), time.perf_counter() - started, False)
            error = None if process.returncode == 0 else f"Agent exit code: {process.returncode}"
        except subprocess.TimeoutExpired as exc:
            command = CommandResult(argv, None, normalize_output(exc.stdout), normalize_output(exc.stderr),
                                    time.perf_counter() - started, True)
            error = f"Agent timeout after {self.spec.timeout_seconds} seconds."
        except Exception as exc:
            return AgentRunResult(None, redact(f"{type(exc).__name__}: {exc}"), self.context)
        except BaseException as exc:
            redact.notes(exc)
            raise
        return AgentRunResult(redact.command(command), redact(error) if error else None, self.context)
