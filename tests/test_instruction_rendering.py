from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from agentreducer.instructions import (
    InstructionBlock,
    InstructionBlockKind,
    InstructionCandidate,
    InstructionHeading,
    parse_instructions,
)


def semantic_blocks(candidate: InstructionCandidate) -> list[tuple[object, ...]]:
    # Occurrence numbers are local to each parse and may change when sections vanish.
    return [
        (block.text, block.kind, tuple((h.level, h.title) for h in block.heading_path))
        for block in candidate.blocks
    ]


def test_required_parent_headings_survive_and_orphan_sections_disappear() -> None:
    original = parse_instructions(
        "# Project\n## Python\n### Testing\n- Use typing.\n- Run pytest.\n"
        "## JavaScript\n- Run npm test.\n## Empty section"
    )
    retained = next(block for block in original.blocks if "Run pytest" in block.text)
    candidate = InstructionCandidate((retained,))

    assert candidate.render() == "# Project\n\n## Python\n\n### Testing\n\n- Run pytest."
    assert candidate.render() == candidate.render()
    assert semantic_blocks(parse_instructions(candidate.render())) == semantic_blocks(candidate)


def test_duplicate_instructions_order_and_distinct_equal_named_sections_survive() -> None:
    original = parse_instructions(
        "Intro.\n\n# Project\n## Same\n- Repeat\n- Middle\n- Repeat\n## Same\n- Repeat"
    )
    candidate = InstructionCandidate((original.blocks[0], original.blocks[1],
                                      original.blocks[3], original.blocks[4]))
    rendered = candidate.render()
    reparsed = parse_instructions(rendered)

    assert rendered.startswith("Intro.\n\n# Project")
    assert rendered.count("## Same") == 2
    assert rendered.count("- Repeat") == 3
    assert "Middle" not in rendered
    assert semantic_blocks(reparsed) == semantic_blocks(candidate)
    assert reparsed.blocks[1].heading_path[-1] == reparsed.blocks[2].heading_path[-1]
    assert reparsed.blocks[2].heading_path[-1] != reparsed.blocks[3].heading_path[-1]


def test_original_ordered_markers_are_not_renumbered() -> None:
    original = parse_instructions("# Steps\n1. Inspect.\n2. Test.\n3. Verify.")
    candidate = InstructionCandidate((original.blocks[1], original.blocks[2]))

    assert candidate.render() == "# Steps\n\n2. Test.\n\n3. Verify."
    assert semantic_blocks(parse_instructions(candidate.render())) == semantic_blocks(candidate)


@pytest.mark.parametrize(
    ("indented", "normalized", "kind"),
    [
        ("  - Second\n    - Nested", "- Second\n  - Nested",
         InstructionBlockKind.UNORDERED_LIST_ITEM),
        ("  2. Second\n    Continuation", "2. Second\n  Continuation",
         InstructionBlockKind.ORDERED_LIST_ITEM),
        ("  ```py\n    code()\n  ```", "```py\n  code()\n```",
         InstructionBlockKind.FENCED_CODE),
        ("  Separate paragraph.", "Separate paragraph.", InstructionBlockKind.PROSE),
    ],
)
def test_deleting_a_separator_block_cannot_nest_independent_instructions(
    indented: str, normalized: str, kind: InstructionBlockKind,
) -> None:
    original = parse_instructions("- First\n\nIntervening prose.\n\n" + indented)
    candidate = InstructionCandidate((original.blocks[0], original.blocks[2]))
    reparsed = parse_instructions(candidate.render())

    assert len(reparsed.blocks) == 2
    assert reparsed.blocks[0].text == "- First"
    assert reparsed.blocks[1].kind is kind
    assert reparsed.blocks[1].text == normalized
    assert candidate.blocks[1].text == indented  # Source text stays immutable.


@pytest.mark.parametrize(
    "source",
    [
        "Before headings.\n\n# Main\nWrapped prose.  \nMore prose.\n\nSecond paragraph.",
        "# Root\n## Child\n- Parent:\n  - A\n  - B\n\n  Continuation.\n\n- Other.",
        "# Root\n## Child\n1. Parent:\n   1. A\n   2. B\n\n2. Other.",
        "## First\n- Rule.\n## Second\nOther rule.\n# Independent\nLast rule.",
        "# Root\n```md\n## Literal\n- Literal\n\n```\n\nAfter.",
        "# Root\n- Example:\n\n  ```md\n  # Literal\n  - Literal\n  ```\n\n- Other.",
        "# Empty root\n# Kept root\n### Skipped level\n- Rule.\n## Empty tail",
    ],
)
def test_render_parse_cycle_preserves_supported_semantic_blocks(source: str) -> None:
    candidate = parse_instructions(source)
    rendered = candidate.render()
    reparsed = parse_instructions(rendered)

    assert semantic_blocks(reparsed) == semantic_blocks(candidate)
    assert reparsed.render() == rendered
    for block in candidate.blocks:
        assert block.text in rendered


def test_empty_candidate_has_no_headings() -> None:
    original = parse_instructions("# Root\n## Child\nRule.")

    assert original.blocks
    assert InstructionCandidate().render() == ""


def test_model_snapshots_are_immutable_and_hashable() -> None:
    heading = InstructionHeading(1, "Rules")
    supplied_path = [heading]
    block = InstructionBlock("Keep.", cast(tuple[InstructionHeading, ...], supplied_path))
    supplied_blocks = [block, block]
    candidate = InstructionCandidate(cast(tuple[InstructionBlock, ...], supplied_blocks))
    lookup = {candidate: "kept"}
    supplied_path.clear()
    supplied_blocks.clear()

    assert candidate.blocks == (block, block)
    assert block.heading_path == (heading,)
    assert lookup[InstructionCandidate((block, block))] == "kept"
    for obj, field, value in [(heading, "title", "Changed"), (block, "text", "Changed"),
                              (candidate, "blocks", ())]:
        with pytest.raises(FrozenInstanceError):
            setattr(obj, field, value)


def test_manual_return_to_parent_heading_restores_its_scope() -> None:
    root = InstructionHeading(1, "Root")
    child = InstructionHeading(2, "Child")
    candidate = InstructionCandidate((
        InstructionBlock("Child rule.", (root, child)),
        InstructionBlock("Parent rule.", (root,)),
    ))

    assert semantic_blocks(parse_instructions(candidate.render())) == semantic_blocks(candidate)
    assert candidate.render().count("# Root") == 2


def test_unrepresentable_manual_unsectioned_text_after_a_section_is_rejected() -> None:
    candidate = InstructionCandidate((
        InstructionBlock("Section rule.", (InstructionHeading(1, "Root"),)),
        InstructionBlock("Unsectioned rule."),
    ))

    with pytest.raises(ValueError, match="Unsectioned blocks must precede"):
        candidate.render()


def test_invalid_models_do_not_create_mutable_or_invalid_heading_paths() -> None:
    with pytest.raises(ValueError, match="between 1 and 6"):
        InstructionHeading(7, "Invalid")
    with pytest.raises(ValueError, match="single-line"):
        InstructionHeading(1, "One\nTwo")
    with pytest.raises(ValueError, match="strictly increase"):
        InstructionBlock("Rule.", (InstructionHeading(2, "Child"), InstructionHeading(1, "Root")))
    with pytest.raises(TypeError, match="InstructionHeading"):
        InstructionBlock("Rule.", cast(tuple[InstructionHeading, ...], ("Root",)))
    with pytest.raises(TypeError, match="InstructionBlockKind"):
        InstructionBlock("Rule.", kind=cast(InstructionBlockKind, "prose"))
    with pytest.raises(TypeError, match="must be a string"):
        InstructionBlock(cast(str, []))
    with pytest.raises(TypeError, match="InstructionBlock"):
        InstructionCandidate(cast(tuple[InstructionBlock, ...], ("Rule.",)))
