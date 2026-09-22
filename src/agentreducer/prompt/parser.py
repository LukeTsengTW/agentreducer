"""Deterministic segmentation with explicit newline and blank-line handling."""

from .models import PromptBlock, PromptCandidate, PromptSplitMode


def split_prompt(
    text: str, mode: PromptSplitMode = PromptSplitMode.PARAGRAPH
) -> PromptCandidate:
    """Split on LF, CRLF, or CR; a blank line contains only spaces and/or tabs.

    Nonblank lines retain all whitespace. Blank lines are discarded in LINE mode
    and delimit paragraphs in PARAGRAPH mode. Leading, trailing, and repeated
    blank lines create no empty blocks. Internal paragraph newlines become LF;
    other characters, including Unicode whitespace, are preserved.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if mode is PromptSplitMode.LINE:
        return PromptCandidate(
            tuple(PromptBlock(line) for line in lines if line.strip(" \t")), mode
        )
    if mode is not PromptSplitMode.PARAGRAPH:
        raise TypeError("mode must be a PromptSplitMode.")

    blocks: list[PromptBlock] = []
    paragraph: list[str] = []
    for line in lines:
        if line.strip(" \t"):
            paragraph.append(line)
        elif paragraph:
            blocks.append(PromptBlock("\n".join(paragraph)))
            paragraph.clear()
    if paragraph:
        blocks.append(PromptBlock("\n".join(paragraph)))
    return PromptCandidate(tuple(blocks), mode)
