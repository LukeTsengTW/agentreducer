"""Immutable prompt units, rendering configuration, and reduction statistics."""

from dataclasses import dataclass
from enum import Enum


class PromptSplitMode(Enum):
    """Choose blank-line-delimited paragraphs or individual nonblank lines."""

    PARAGRAPH = "paragraph"
    LINE = "line"


@dataclass(frozen=True)
class PromptBlock:
    """One retained unit of text, stored verbatim, including any whitespace."""

    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("PromptBlock.text must be a string.")


@dataclass(frozen=True)
class PromptCandidate:
    """An ordered, hashable snapshot of blocks and their rendering mode."""

    blocks: tuple[PromptBlock, ...] = ()
    mode: PromptSplitMode = PromptSplitMode.PARAGRAPH

    def __post_init__(self) -> None:
        blocks = tuple(self.blocks)
        if not all(isinstance(block, PromptBlock) for block in blocks):
            raise TypeError("PromptCandidate.blocks must contain PromptBlock objects.")
        if not isinstance(self.mode, PromptSplitMode):
            raise TypeError("PromptCandidate.mode must be a PromptSplitMode.")
        object.__setattr__(self, "blocks", blocks)

    def render(self) -> str:
        """Join blocks with LF or two LFs, without an added terminal newline."""
        separator = "\n\n" if self.mode is PromptSplitMode.PARAGRAPH else "\n"
        return separator.join(block.text for block in self.blocks)


@dataclass(frozen=True)
class PromptReductionResult:
    """A completed reduction; character counts measure rendered Python strings."""

    original: PromptCandidate
    reduced: PromptCandidate
    evaluations: int

    @property
    def original_block_count(self) -> int:
        return len(self.original.blocks)

    @property
    def reduced_block_count(self) -> int:
        return len(self.reduced.blocks)

    @property
    def original_character_count(self) -> int:
        return len(self.original.render())

    @property
    def reduced_character_count(self) -> int:
        return len(self.reduced.render())

    @property
    def reduction_percentage(self) -> float:
        """Percentage of rendered characters removed; zero for an empty original."""
        original_count = self.original_character_count
        if original_count == 0:
            return 0.0
        return 100.0 * (original_count - self.reduced_character_count) / original_count
