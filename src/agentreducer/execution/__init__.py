"""Deterministic command evaluation in fresh disposable workspace copies."""

from .materializer import CandidateMaterializer, TextFileMaterializer
from .models import CommandEvaluation, CommandResult, CommandSpec, ExitCodePolicy
from .oracle import CommandOracle
from .runner import run_command
from .workspace import DEFAULT_EXCLUSIONS, Workspace, WorkspaceTemplate

__all__ = [
    "CandidateMaterializer", "CommandEvaluation", "CommandOracle", "CommandResult", "CommandSpec",
    "DEFAULT_EXCLUSIONS", "ExitCodePolicy", "TextFileMaterializer", "Workspace",
    "WorkspaceTemplate", "run_command",
]
