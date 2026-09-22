from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from agentreducer.prompt import PromptBlock, PromptCandidate, PromptSplitMode, split_prompt


@pytest.mark.parametrize(
    ("mode", "source", "expected", "rendered"),
    [
        (
            PromptSplitMode.PARAGRAPH,
            "Inspect auth.\nRead README.\n\nRun tests.",
            ("Inspect auth.\nRead README.", "Run tests."),
            "Inspect auth.\nRead README.\n\nRun tests.",
        ),
        (
            PromptSplitMode.LINE,
            "Inspect auth.\nRead README.\nRun tests.",
            ("Inspect auth.", "Read README.", "Run tests."),
            "Inspect auth.\nRead README.\nRun tests.",
        ),
        (
            PromptSplitMode.PARAGRAPH,
            "\n \t\n  Inspect auth.  \n\tRead README.\t\n\n \n\nRun tests. \n\n",
            ("  Inspect auth.  \n\tRead README.\t", "Run tests. "),
            "  Inspect auth.  \n\tRead README.\t\n\nRun tests. ",
        ),
        (
            PromptSplitMode.LINE,
            "\n \t\n  Inspect auth.  \n\tRead README.\t\n\n \n\nRun tests. \n\n",
            ("  Inspect auth.  ", "\tRead README.\t", "Run tests. "),
            "  Inspect auth.  \n\tRead README.\t\nRun tests. ",
        ),
        (
            PromptSplitMode.PARAGRAPH,
            "A\r\nB\r\n\r\nC\rD\r\rE\n",
            ("A\nB", "C\nD", "E"),
            "A\nB\n\nC\nD\n\nE",
        ),
        (
            PromptSplitMode.LINE,
            "A\r\nB\r\n\r\nC\rD\r\rE\n",
            ("A", "B", "C", "D", "E"),
            "A\nB\nC\nD\nE",
        ),
        (
            PromptSplitMode.PARAGRAPH,
            "DUPLICATE\n\nMiddle\n\nDUPLICATE",
            ("DUPLICATE", "Middle", "DUPLICATE"),
            "DUPLICATE\n\nMiddle\n\nDUPLICATE",
        ),
        (
            PromptSplitMode.LINE,
            "DUPLICATE\nMiddle\nDUPLICATE",
            ("DUPLICATE", "Middle", "DUPLICATE"),
            "DUPLICATE\nMiddle\nDUPLICATE",
        ),
    ],
)
def test_split_and_render(
    mode: PromptSplitMode, source: str, expected: tuple[str, ...], rendered: str
) -> None:
    candidate = split_prompt(source, mode)

    assert candidate.mode is mode
    assert tuple(block.text for block in candidate.blocks) == expected
    assert candidate.render() == rendered
    assert candidate.render() == candidate.render()
    assert candidate == split_prompt(source, mode)
    assert split_prompt(candidate.render(), mode) == candidate


def test_paragraph_is_the_default_mode() -> None:
    candidate = split_prompt("First line\nSecond line\n\nNext paragraph")

    assert candidate.mode is PromptSplitMode.PARAGRAPH
    assert candidate.blocks == (
        PromptBlock("First line\nSecond line"), PromptBlock("Next paragraph")
    )


@pytest.mark.parametrize("mode", list(PromptSplitMode))
@pytest.mark.parametrize("source", ["", " ", "\t", "\n\n", " \t\r\n\t\r\n"])
def test_empty_and_blank_only_prompts(source: str, mode: PromptSplitMode) -> None:
    candidate = split_prompt(source, mode)

    assert candidate.blocks == ()
    assert candidate.render() == ""
    assert candidate.mode is mode


@pytest.mark.parametrize("mode", list(PromptSplitMode))
def test_unicode_whitespace_and_other_separator_characters_are_preserved(
    mode: PromptSplitMode,
) -> None:
    source = "\u00a0\n\u2003\nX\u2028Y\u2029Z\v\f\x85"
    candidate = split_prompt(source, mode)

    assert candidate.render() == source
    assert len(candidate.blocks) == (3 if mode is PromptSplitMode.LINE else 1)


@pytest.mark.parametrize(
    ("mode", "separator"),
    [(PromptSplitMode.LINE, "\n"), (PromptSplitMode.PARAGRAPH, "\n\n")],
)
def test_manually_constructed_blocks_render_verbatim(
    mode: PromptSplitMode, separator: str
) -> None:
    blocks = (PromptBlock("  A\r\nB  "), PromptBlock(""), PromptBlock("  A\r\nB  "))
    candidate = PromptCandidate(blocks, mode)

    assert candidate.blocks == blocks
    assert candidate.render() == separator.join(("  A\r\nB  ", "", "  A\r\nB  "))


def test_prompt_models_are_immutable_hashable_snapshots() -> None:
    first = PromptBlock("Same")
    second = PromptBlock("Same")
    supplied_blocks = [first, second]
    # Exercise the constructor's runtime snapshot even for an untyped list caller.
    candidate = PromptCandidate(cast(tuple[PromptBlock, ...], supplied_blocks))
    lookup = {candidate: "original"}
    supplied_blocks.clear()

    assert candidate.blocks == (first, second)
    assert candidate.blocks[0] is first
    assert candidate.blocks[1] is second
    assert lookup[PromptCandidate((first, second))] == "original"
    with pytest.raises(FrozenInstanceError):
        setattr(first, "text", "changed")
    with pytest.raises(FrozenInstanceError):
        setattr(candidate, "blocks", ())
    with pytest.raises(FrozenInstanceError):
        setattr(candidate, "mode", PromptSplitMode.LINE)


def test_mode_is_part_of_candidate_identity() -> None:
    blocks = (PromptBlock("A"), PromptBlock("B"))
    line = PromptCandidate(blocks, PromptSplitMode.LINE)
    paragraph = PromptCandidate(blocks, PromptSplitMode.PARAGRAPH)

    assert line != paragraph
    assert len({line, paragraph}) == 2
    assert line.render() != paragraph.render()


def test_invalid_prompt_models_and_split_mode_are_rejected() -> None:
    with pytest.raises(TypeError, match="must be a string"):
        PromptBlock(cast(str, ["mutable"]))
    with pytest.raises(TypeError, match="must contain PromptBlock"):
        PromptCandidate(cast(tuple[PromptBlock, ...], ("unstructured",)))
    with pytest.raises(TypeError, match="must be a PromptSplitMode"):
        PromptCandidate((), cast(PromptSplitMode, "line"))
    with pytest.raises(TypeError, match="must be a PromptSplitMode"):
        split_prompt("text", cast(PromptSplitMode, "line"))
