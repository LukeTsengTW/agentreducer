"""Generic runner interface independent of Codex and candidate domains."""

from pathlib import Path
from typing import Protocol

from .context import AgentContextEvidence
from .models import AgentRunResult


class AgentRunner(Protocol):
    @property
    def context(self) -> AgentContextEvidence: ...

    def run(self, *, workspace: Path, prompt: str) -> AgentRunResult: ...
