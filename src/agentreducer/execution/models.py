"""Immutable command configuration, diagnostics, and exit-code classification."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..models import OracleResult


@dataclass(frozen=True)
class CommandSpec:
    """Explicit argv and child-only overrides; never a shell command string."""

    argv: tuple[str, ...]
    timeout_seconds: float = 60.0
    env: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if isinstance(self.argv, (str, bytes)):
            raise TypeError("argv must be a sequence of strings, not a command string.")
        argv = tuple(self.argv)
        if not argv or argv[0] == "":
            raise ValueError("argv must contain a nonempty executable.")
        if not all(isinstance(arg, str) for arg in argv):
            raise TypeError("Every argv item must be a string.")
        if any("\0" in arg for arg in argv):
            raise ValueError("argv cannot contain NUL characters.")
        timeout = self.timeout_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("timeout_seconds must be a number.")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout_seconds must be positive and finite.")
        if self.env is not None:
            if not isinstance(self.env, Mapping):
                raise TypeError("env must be a mapping of strings.")
            env = dict(self.env)
            for key, value in env.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    raise TypeError("Environment keys and values must be strings.")
                if not key or "=" in key or "\0" in key or "\0" in value:
                    raise ValueError("Invalid environment key or value.")
            object.__setattr__(self, "env", MappingProxyType(env))
        object.__setattr__(self, "argv", argv)


@dataclass(frozen=True)
class CommandResult:
    """One process result, including partial output if the command timed out."""

    argv: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "argv", tuple(self.argv))


@dataclass(frozen=True)
class CommandEvaluation:
    """An oracle outcome with optional process diagnostics and error details."""

    outcome: OracleResult
    command: CommandResult | None = None
    error: str | None = None


@dataclass(frozen=True)
class ExitCodePolicy:
    """Default nonzero FAIL, or explicit disjoint sets with unknown codes ERROR."""

    pass_exit_codes: frozenset[int] = frozenset({0})
    fail_exit_codes: frozenset[int] | None = None

    def __post_init__(self) -> None:
        passes = frozenset(self.pass_exit_codes)
        failures = (
            None if self.fail_exit_codes is None else frozenset(self.fail_exit_codes)
        )
        if any(type(code) is not int for code in passes | (failures or frozenset())):
            raise TypeError("Exit codes must be integers.")
        if failures is not None and passes & failures:
            raise ValueError("PASS and FAIL exit-code sets must be disjoint.")
        object.__setattr__(self, "pass_exit_codes", passes)
        object.__setattr__(self, "fail_exit_codes", failures)

    def classify(self, exit_code: int | None) -> OracleResult:
        if type(exit_code) is not int:
            return OracleResult.ERROR
        if exit_code in self.pass_exit_codes:
            return OracleResult.PASS
        if self.fail_exit_codes is None or exit_code in self.fail_exit_codes:
            return OracleResult.FAIL
        return OracleResult.ERROR
