"""Minimal V4A unified-diff parser/applier (the ``apply_patch`` envelope format).

Supports the three file operations of the format::

    *** Begin Patch
    *** Add File: path
    +line
    *** Update File: path
    @@ optional context
     unchanged
    -removed
    +added
    *** Delete File: path
    *** End Patch

Hunks are applied by reconstructing the "old" block (context + removed lines)
and replacing the first occurrence with the "new" block (context + added
lines). This is robust for typical agent edits without a full diff engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from mcp_fs.models import ErrorCode, ToolError

_BEGIN = "*** Begin Patch"
_END = "*** End Patch"
_ADD = "*** Add File: "
_UPDATE = "*** Update File: "
_DELETE = "*** Delete File: "
_MOVE = "*** Move to: "
_HUNK = "@@"


class OpKind(StrEnum):
    """File-level patch operation kind."""

    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"


@dataclass(slots=True)
class Hunk:
    """One contiguous change region within an Update operation."""

    removed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FileOp:
    """A single file's patch operation."""

    kind: OpKind
    path: str
    move_to: str | None = None
    add_content: str = ""
    hunks: list[Hunk] = field(default_factory=list)


def parse_patch(text: str) -> list[FileOp]:
    """Parse a V4A patch envelope into a list of :class:`FileOp`."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != _BEGIN:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "patch must start with '*** Begin Patch'")
    ops: list[FileOp] = []
    index = 1
    while index < len(lines):
        line = lines[index]
        if line.strip() == _END:
            return ops
        if line.startswith(_ADD):
            index = _parse_add(lines, index, ops)
        elif line.startswith(_UPDATE):
            index = _parse_update(lines, index, ops)
        elif line.startswith(_DELETE):
            ops.append(FileOp(kind=OpKind.DELETE, path=line[len(_DELETE) :].strip()))
            index += 1
        else:
            raise ToolError(ErrorCode.INVALID_ARGUMENT, f"unexpected patch line: {line!r}")
    raise ToolError(ErrorCode.INVALID_ARGUMENT, "patch missing '*** End Patch'")


def apply_update(original: str, op: FileOp) -> str:
    """Apply an Update operation's hunks to ``original`` text."""
    content = original
    for hunk in op.hunks:
        old_block = "\n".join(hunk.context_before + hunk.removed + hunk.context_after)
        new_block = "\n".join(hunk.context_before + hunk.added + hunk.context_after)
        if old_block and old_block not in content:
            raise ToolError(ErrorCode.NO_MATCH, f"hunk context not found in '{op.path}'")
        content = content.replace(old_block, new_block, 1) if old_block else new_block
    return content


def _parse_add(lines: list[str], index: int, ops: list[FileOp]) -> int:
    path = lines[index][len(_ADD) :].strip()
    index += 1
    body: list[str] = []
    while index < len(lines) and not _is_marker(lines[index]):
        body.append(lines[index][1:] if lines[index].startswith("+") else lines[index])
        index += 1
    ops.append(FileOp(kind=OpKind.ADD, path=path, add_content="\n".join(body)))
    return index


def _parse_update(lines: list[str], index: int, ops: list[FileOp]) -> int:
    path = lines[index][len(_UPDATE) :].strip()
    index += 1
    move_to: str | None = None
    if index < len(lines) and lines[index].startswith(_MOVE):
        move_to = lines[index][len(_MOVE) :].strip()
        index += 1
    hunks: list[Hunk] = []
    current: Hunk | None = None
    while index < len(lines) and not _is_file_marker(lines[index]):
        raw = lines[index]
        if raw.startswith(_HUNK):
            current = Hunk()
            hunks.append(current)
            index += 1
            continue
        if current is None:
            current = Hunk()
            hunks.append(current)
        _classify_line(raw, current)
        index += 1
    ops.append(FileOp(kind=OpKind.UPDATE, path=path, move_to=move_to, hunks=hunks))
    return index


def _classify_line(raw: str, hunk: Hunk) -> None:
    if raw.startswith("+"):
        hunk.added.append(raw[1:])
    elif raw.startswith("-"):
        hunk.removed.append(raw[1:])
    else:
        text = raw[1:] if raw.startswith(" ") else raw
        if hunk.removed or hunk.added:
            hunk.context_after.append(text)
        else:
            hunk.context_before.append(text)


def _is_marker(line: str) -> bool:
    return line.strip() == _END or _is_file_marker(line)


def _is_file_marker(line: str) -> bool:
    return line.startswith((_ADD, _UPDATE, _DELETE)) or line.strip() == _END
