"""Immutable instruction units with structural heading context."""

from dataclasses import dataclass
from enum import Enum


class InstructionBlockKind(Enum):
    PROSE = "prose"
    UNORDERED_LIST_ITEM = "unordered_list_item"
    ORDERED_LIST_ITEM = "ordered_list_item"
    FENCED_CODE = "fenced_code"


@dataclass(frozen=True)
class InstructionHeading:
    """A heading occurrence; equal titles in separate sections remain distinct."""

    level: int
    title: str
    occurrence: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.level <= 6:
            raise ValueError("Heading level must be between 1 and 6.")
        if not isinstance(self.title, str) or "\n" in self.title or "\r" in self.title:
            raise ValueError("Heading title must be a single-line string.")
        if self.occurrence < 0:
            raise ValueError("Heading occurrence must be nonnegative.")

    def render(self) -> str:
        return "#" * self.level + (" " + self.title if self.title else "")


@dataclass(frozen=True)
class InstructionBlock:
    """One atomic unit; text includes list markers and nested content verbatim."""

    text: str
    heading_path: tuple[InstructionHeading, ...] = ()
    kind: InstructionBlockKind = InstructionBlockKind.PROSE

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("InstructionBlock.text must be a string.")
        path = tuple(self.heading_path)
        if not all(isinstance(heading, InstructionHeading) for heading in path):
            raise TypeError("heading_path must contain InstructionHeading objects.")
        if any(left.level >= right.level for left, right in zip(path, path[1:])):
            raise ValueError("Heading levels in a path must strictly increase.")
        if not isinstance(self.kind, InstructionBlockKind):
            raise TypeError("kind must be an InstructionBlockKind.")
        object.__setattr__(self, "heading_path", path)


@dataclass(frozen=True)
class InstructionCandidate:
    """An ordered snapshot of reducible blocks; headings are context, not units."""

    blocks: tuple[InstructionBlock, ...] = ()

    def __post_init__(self) -> None:
        blocks = tuple(self.blocks)
        if not all(isinstance(block, InstructionBlock) for block in blocks):
            raise TypeError("blocks must contain InstructionBlock objects.")
        object.__setattr__(self, "blocks", blocks)

    def render(self) -> str:
        """Emit required headings and blocks, normalizing top-level indentation.

        Parsed candidates and their ordered subsequences retain section scope.
        Manually moving unsectioned text after a section cannot be represented
        without inventing a heading, and is rejected.
        """
        pieces: list[str] = []
        active: tuple[InstructionHeading, ...] = ()
        for block in self.blocks:
            path = block.heading_path
            if active and not path:
                raise ValueError("Unsectioned blocks must precede sectioned blocks.")
            shared = 0
            for previous, current in zip(active, path):
                if previous != current:
                    break
                shared += 1
            if path != active and shared == len(path) and path:
                # Re-emit a parent when a manually built candidate returns to it.
                shared -= 1
            pieces.extend(heading.render() for heading in path[shared:])
            pieces.append(_render_block_text(block.text))
            active = path
        return "\n\n".join(pieces)


def _render_block_text(text: str) -> str:
    """Remove a 1–3 space container margin so adjacent units cannot become nested."""
    lines = text.split("\n")
    margin = len(lines[0]) - len(lines[0].lstrip(" "))
    if not 1 <= margin <= 3:
        return text
    return "\n".join(
        line[min(margin, len(line) - len(line.lstrip(" "))):] for line in lines
    )


@dataclass(frozen=True)
class InstructionReductionResult:
    """A completed reduction, with counts derived from normalized rendered text."""

    original: InstructionCandidate
    reduced: InstructionCandidate
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
    def block_reduction_percentage(self) -> float:
        """Percentage of block occurrences removed; zero for zero original blocks."""
        count = self.original_block_count
        return 100.0 * (count - self.reduced_block_count) / count if count else 0.0

    @property
    def character_reduction_percentage(self) -> float:
        """Percentage of rendered characters removed; zero for zero characters."""
        count = self.original_character_count
        return 100.0 * (count - self.reduced_character_count) / count if count else 0.0
