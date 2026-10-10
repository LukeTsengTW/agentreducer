import pytest

from agentreducer.instructions import (
    InstructionBlockKind as Kind,
    InstructionCandidate,
    parse_instructions,
)


def test_one_heading_and_one_rule() -> None:
    candidate = parse_instructions("# Project\n\n- Use typing.")

    assert len(candidate.blocks) == 1
    block = candidate.blocks[0]
    assert block.text == "- Use typing."
    assert block.kind is Kind.UNORDERED_LIST_ITEM
    assert [(heading.level, heading.title) for heading in block.heading_path] == [(1, "Project")]


def test_heading_hierarchy_and_independent_sections() -> None:
    candidate = parse_instructions(
        "# Project\n## Python\n### Testing\n- Run pytest.\n"
        "## JavaScript\n- Run npm test.\n# Other project\nOther rule."
    )

    assert [tuple((h.level, h.title) for h in b.heading_path) for b in candidate.blocks] == [
        ((1, "Project"), (2, "Python"), (3, "Testing")),
        ((1, "Project"), (2, "JavaScript")),
        ((1, "Other project"),),
    ]
    assert len(candidate.blocks) == 3


def test_paragraphs_and_text_before_the_first_heading_preserve_whitespace() -> None:
    candidate = parse_instructions(
        "  Intro line.  \nSecond line.\n\n# Rules\n\n"
        "Preserve compatibility.\nAvoid new syntax.  \n\nAnother paragraph.\n"
    )

    assert [block.text for block in candidate.blocks] == [
        "  Intro line.  \nSecond line.",
        "Preserve compatibility.\nAvoid new syntax.  ",
        "Another paragraph.",
    ]
    assert all(block.kind is Kind.PROSE for block in candidate.blocks)
    assert candidate.blocks[0].heading_path == ()
    assert candidate.blocks[1].heading_path[0].title == "Rules"


@pytest.mark.parametrize("marker", ["-", "*", "+", "1.", "23.", "9)"])
@pytest.mark.parametrize("indent", ["", "   "])
def test_top_level_list_markers_and_duplicate_items(marker: str, indent: str) -> None:
    line = f"{indent}{marker} Keep this.  "
    candidate = parse_instructions(f"{line}\n{line}")
    expected_kind = Kind.UNORDERED_LIST_ITEM if marker in "-*+" else Kind.ORDERED_LIST_ITEM

    assert len(candidate.blocks) == 2
    assert [block.text for block in candidate.blocks] == [line, line]
    assert all(block.kind is expected_kind for block in candidate.blocks)


@pytest.mark.parametrize(
    "item",
    [
        "- Before finishing:\n  - run pytest\n  - run mypy\n  - run ruff",
        "1. Before finishing:\n   1. run pytest\n   2. run mypy\n      - nested detail",
        "- Preserve Python 3.11.\n  Do not use Python 3.12 syntax.",
        "- First sentence.\nLazy continuation without indentation.",
        "- Parent:\n\n  Child paragraph.\n\n \t\n  - nested item",
        "- Parent:\n\tContinuation with a tab.",
    ],
)
def test_nested_lists_and_continuations_are_atomic(item: str) -> None:
    candidate = parse_instructions(item + "\n\n- Next rule.\n\nSeparate prose.")

    assert [block.text for block in candidate.blocks] == [item, "- Next rule.", "Separate prose."]
    assert item in candidate.render()


@pytest.mark.parametrize("fence", ["```", "````", "~~~"])
def test_standalone_fences_do_not_parse_markdown_in_the_body(fence: str) -> None:
    code = f"{fence}markdown\n# Fake heading\n- Fake rule\n\n1. Fake ordered rule\n{fence}"
    candidate = parse_instructions("# Real\n\n" + code + "\n\nAfter code.")

    assert len(candidate.blocks) == 2
    assert candidate.blocks[0].kind is Kind.FENCED_CODE
    assert candidate.blocks[0].text == code
    assert candidate.blocks[1].text == "After code."
    assert all([h.title for h in b.heading_path] == ["Real"] for b in candidate.blocks)


def test_fence_closer_requires_matching_character_sufficient_length_and_no_payload() -> None:
    code = (
        "````text\n```\n~~~~\n```` not a closer\n    ````\n"
        "# Still code\n- Still code\n  `````"
    )
    candidate = parse_instructions(code + "\n\nAfter.")

    assert [block.text for block in candidate.blocks] == [code, "After."]
    assert candidate.blocks[0].kind is Kind.FENCED_CODE


@pytest.mark.parametrize(
    "item",
    [
        "- Example:\n  ```md\n  # Code heading\n  - Code bullet\n  ```\n  Explanation.",
        "- ```md\n  # Code heading\n  - Code bullet\n  ```",
        "1. Example:\n\n   ```py\n   print('hello')\n\n   ```",
        "- Example:\n  - ```md\n    # Code heading\n    - Code bullet\n    ```",
        "- Example:\n  ~~~md\n  # Code heading\n  ~~~",
    ],
)
def test_fences_in_list_items_remain_inside_the_atomic_item(item: str) -> None:
    candidate = parse_instructions("# Real\n\n" + item + "\n- Next rule.")

    assert [block.text for block in candidate.blocks] == [item, "- Next rule."]
    assert all(block.kind in (Kind.UNORDERED_LIST_ITEM, Kind.ORDERED_LIST_ITEM)
               for block in candidate.blocks)
    assert all([h.title for h in b.heading_path] == ["Real"] for b in candidate.blocks)


@pytest.mark.parametrize("prefix", ["", "- Example:\n  "])
def test_unterminated_fence_retains_every_line_through_eof(prefix: str) -> None:
    source = prefix + "```md\n# Code, not a section\n- Code, not a rule\n\n"
    candidate = parse_instructions(source)

    assert len(candidate.blocks) == 1
    assert candidate.blocks[0].text == source
    assert candidate.render() == source


def test_repeated_blank_lines_and_mixed_newlines() -> None:
    candidate = parse_instructions("\r\n# Rules\r\n\r\n \t\r- First\r\r\r- Second\n\n")

    assert [block.text for block in candidate.blocks] == ["- First", "- Second"]
    assert candidate.render() == "# Rules\n\n- First\n\n- Second"


@pytest.mark.parametrize("source", ["", " \t\r\n\r", "# Only a heading", "# Parent\n\n## Empty child\n"])
def test_empty_and_heading_only_documents(source: str) -> None:
    candidate = parse_instructions(source)

    assert candidate == InstructionCandidate()
    assert candidate.render() == ""


def test_atx_levels_closing_markers_and_literal_hashes() -> None:
    candidate = parse_instructions("   # Project ###\n#### C#\n###### Details\nRule.\n")

    assert [(h.level, h.title) for h in candidate.blocks[0].heading_path] == [
        (1, "Project"), (4, "C#"), (6, "Details")
    ]
    assert candidate.render() == "# Project\n\n#### C#\n\n###### Details\n\nRule."


@pytest.mark.parametrize(
    "source",
    [
        "> Quote\n> With another line.",
        "| A | B |\n| --- | --- |\n| One | Two |",
        "<div>Keep this HTML verbatim.</div>",
        "Setext-looking title\n====================",
        "Setext-looking title\n--------------------",
        "#not-an-atx-heading\n####### Seven hashes stay text.",
        "    Indented text.  \n    # Still indented text.",
        "\u00a0\n\u2003\nKeep\u2028this\u2029text.",
        "* * *",
    ],
)
def test_unsupported_constructs_are_retained_as_opaque_prose(source: str) -> None:
    candidate = parse_instructions(source)

    assert len(candidate.blocks) == 1
    assert candidate.blocks[0].kind is Kind.PROSE
    assert candidate.blocks[0].text == source
    assert candidate.render() == source


def test_repeated_heading_names_have_distinct_section_occurrences() -> None:
    source = "# Project\n## Same\n- Repeat\n## Same\n- Repeat"
    first = parse_instructions(source)
    second = parse_instructions(source)

    assert first == second
    assert hash(first) == hash(second)
    left, right = first.blocks
    assert left.text == right.text
    assert left.heading_path[0] == right.heading_path[0]
    assert left.heading_path[1].title == right.heading_path[1].title
    assert left.heading_path[1].occurrence != right.heading_path[1].occurrence
