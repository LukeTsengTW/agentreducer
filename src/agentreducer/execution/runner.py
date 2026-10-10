"""One explicit local process; classification belongs to the oracle policy."""

import os
import subprocess
import time
from pathlib import Path, PureWindowsPath

from .models import CommandResult, CommandSpec


def _partial_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return (value or "").replace("\r\n", "\n").replace("\r", "\n")


def run_command(spec: CommandSpec, *, cwd: Path) -> CommandResult:
    """Capture one command; timeout kills/reaps the direct child, not its tree.

    Launch errors propagate for the oracle to classify. Use an absolute
    executable path for predictable cross-platform executable lookup.
    """
    if os.name == "nt" and PureWindowsPath(spec.argv[0]).suffix.lower() in {".bat", ".cmd"}:
        raise ValueError("Direct Windows batch executables are not supported.")
    environment = os.environ.copy()
    if spec.env is not None:
        environment.update(spec.env)
    started = time.perf_counter()
    try:
        result = subprocess.run(
            spec.argv, cwd=cwd, env=environment, shell=False,
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=spec.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            spec.argv, None, _partial_text(exc.stdout), _partial_text(exc.stderr),
            time.perf_counter() - started, True,
        )
    return CommandResult(
        spec.argv, result.returncode, result.stdout, result.stderr,
        time.perf_counter() - started, False,
    )
