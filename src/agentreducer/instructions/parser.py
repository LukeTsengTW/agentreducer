"""A small line-oriented Markdown subset, retaining opaque text conservatively."""

import re

from .models import (
    InstructionBlock,
    InstructionBlockKind,
    InstructionCandidate,
    InstructionHeading,
)

_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|$)")
_LIST = re.compile(r"^( {0,3})([-+*]|[0-9]{1,9}[.)])(?:[ \t]+(.*)|$)")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_THEMATIC = re.compile(r"^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")


def parse_instructions(text: str) -> InstructionCandidate:
    """Parse ATX headings, paragraphs, list items, and backtick/tilde fences.

    CRLF/CR become LF. Blank means ASCII spaces/tabs only. Headings supply
    context; each paragraph, top-level list item, or standalone fence is atomic.
    More-indented list content and nonblank lazy continuation stay with their
    item. Unknown syntax remains text. Unterminated fences consume through EOF.
    See README for the deliberately limited Markdown grammar.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    headings: list[InstructionHeading] = []
    blocks: list[InstructionBlock] = []
    occurrence = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        if _blank(line):
            index += 1
            continue
        heading = _HEADING.fullmatch(line)
        if heading:
            level = len(heading[1])
            title = (heading[2] or "").strip(" \t")
            title = re.sub(r"(?:^|[ \t]+)#+$", "", title).rstrip(" \t")
            while headings and headings[-1].level >= level:
                headings.pop()
            occurrence += 1
            headings.append(InstructionHeading(level, title, occurrence))
            index += 1
            continue

        fence = _opening_fence(line)
        item = _list_item(line)
        if fence:
            end = _fence_end(lines, index, fence)
            kind = InstructionBlockKind.FENCED_CODE
        elif item:
            end = _list_end(lines, index, item)
            kind = (
                InstructionBlockKind.UNORDERED_LIST_ITEM
                if item[2] in "-+*" else InstructionBlockKind.ORDERED_LIST_ITEM
            )
        else:
            end = index + 1
            while end < len(lines) and not _blank(lines[end]):
                if (_HEADING.fullmatch(lines[end]) or _list_item(lines[end])
                        or _opening_fence(lines[end])):
                    break
                end += 1
            kind = InstructionBlockKind.PROSE
        blocks.append(InstructionBlock("\n".join(lines[index:end]), tuple(headings), kind))
        index = end
    return InstructionCandidate(tuple(blocks))


def _blank(line: str) -> bool:
    return not line.strip(" \t")


def _indent(line: str) -> int:
    prefix = line[:len(line) - len(line.lstrip(" \t"))]
    return len(prefix.expandtabs(4))


def _list_item(line: str) -> re.Match[str] | None:
    return None if _THEMATIC.fullmatch(line) else _LIST.fullmatch(line)


def _opening_fence(line: str) -> str | None:
    match = _FENCE.fullmatch(line)
    if match and not (match[1][0] == "`" and "`" in match[2]):
        return match[1]
    return None


def _closing_fence(line: str, fence: str) -> bool:
    return bool(re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*", line))


def _fence_end(lines: list[str], start: int, fence: str) -> int:
    index = start + 1
    while index < len(lines):
        if _closing_fence(lines[index], fence):
            return index + 1
        index += 1
    return index


def _item_fence(line: str) -> str | None:
    """Find a fence in indented item content or immediately after a nested marker."""
    content = line.lstrip(" \t")
    item = _list_item(content)
    return _opening_fence((item[3] or "") if item else content)


def _list_end(lines: list[str], start: int, item: re.Match[str]) -> int:
    base_indent = len(item[1])
    fence = _opening_fence(item[3] or "")
    index = start + 1
    while index < len(lines):
        line = lines[index]
        if fence:
            # List containers may add indentation at several nesting depths.
            if _closing_fence(line.lstrip(" \t"), fence):
                fence = None
            index += 1
            continue
        if _blank(line):
            following = index + 1
            while following < len(lines) and _blank(lines[following]):
                following += 1
            if following == len(lines) or _indent(lines[following]) <= base_indent:
                break
            index = following
            continue
        if _indent(line) > base_indent:
            fence = _item_fence(line)
        elif (_HEADING.fullmatch(line) or _list_item(line) or _opening_fence(line)
              or _THEMATIC.fullmatch(line)):
            break
        # Unindented plain text without a blank line is a lazy continuation.
        index += 1
    return index
