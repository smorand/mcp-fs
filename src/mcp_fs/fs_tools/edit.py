"""Edit family: edit, multi_edit, search_replace, apply_patch (V4A), insert_at_line."""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from mcp_fs.fs_tools.patch_v4a import OpKind, apply_update, parse_patch
from mcp_fs.models import ErrorCode, ToolError

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from mcp_fs.context import ToolContext
    from mcp_fs.volume import VolumeClient

_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)
_FUZZY_THRESHOLD = 0.6


def _diff(old: str, new: str, path: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), path, path))


def _apply_unique(text: str, old_string: str, new_string: str, *, replace_all: bool, path: str) -> str:
    count = text.count(old_string)
    if count == 0:
        raise ToolError(ErrorCode.NO_MATCH, f"old_string not found in '{path}'")
    if count > 1 and not replace_all:
        raise ToolError(ErrorCode.AMBIGUOUS_MATCH, f"old_string matches {count} sites in '{path}' (use replace_all)")
    return text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    """Register the edit-family tools."""

    async def _commit(person: str, mount_id: str, client: VolumeClient, norm: str, new_text: str, op: str) -> None:
        data = new_text.encode("utf-8")
        ctx.safety.charge_write(person, mount_id, len(data))
        await client.write_bytes_atomic(norm, data)
        ctx.safety.record_audit(person, mount_id, op, norm)

    @mcp.tool(
        name="fs.edit", annotations=_DESTRUCTIVE, description="Replace a unique string; dry_run returns the diff."
    )
    async def fs_edit(
        mount_id: str,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        norm = ctx.norm(path)
        ctx.safety.ensure_read_before_write(person, mount_id, norm)
        old = await client.read_text(norm)
        new = _apply_unique(old, old_string, new_string, replace_all=replace_all, path=norm)
        diff = _diff(old, new, norm)
        if not dry_run:
            await _commit(person, mount_id, client, norm, new, "edit")
        return {"path": norm, "applied": not dry_run, "diff": diff}

    @mcp.tool(
        name="fs.multi_edit", annotations=_DESTRUCTIVE, description="Apply several edits atomically (all or nothing)."
    )
    async def fs_multi_edit(
        mount_id: str, path: str, edits: list[dict[str, Any]], dry_run: bool = False
    ) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        norm = ctx.norm(path)
        ctx.safety.ensure_read_before_write(person, mount_id, norm)
        old = await client.read_text(norm)
        new = old
        for spec in edits:
            new = _apply_unique(
                new,
                str(spec["old_string"]),
                str(spec["new_string"]),
                replace_all=bool(spec.get("replace_all", False)),
                path=norm,
            )
        diff = _diff(old, new, norm)
        if not dry_run:
            await _commit(person, mount_id, client, norm, new, "multi_edit")
        return {"path": norm, "applied": not dry_run, "edits": len(edits), "diff": diff}

    @mcp.tool(
        name="fs.search_replace",
        annotations=_DESTRUCTIVE,
        description="Replace a multi-line block (optional fuzzy match).",
    )
    async def fs_search_replace(
        mount_id: str,
        path: str,
        search_block: str,
        replace_block: str,
        fuzzy: bool = False,
    ) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        norm = ctx.norm(path)
        ctx.safety.ensure_read_before_write(person, mount_id, norm)
        old = await client.read_text(norm)
        if search_block in old:
            new = old.replace(search_block, replace_block, 1)
        elif fuzzy:
            new = _fuzzy_replace(old, search_block, replace_block, norm)
        else:
            raise ToolError(ErrorCode.NO_MATCH, f"search_block not found in '{norm}'")
        await _commit(person, mount_id, client, norm, new, "search_replace")
        return {"path": norm, "applied": True, "diff": _diff(old, new, norm)}

    @mcp.tool(
        name="fs.insert_at_line", annotations=_DESTRUCTIVE, description="Insert content before a 1-based line number."
    )
    async def fs_insert_at_line(mount_id: str, path: str, line: int, content: str) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        norm = ctx.norm(path)
        ctx.safety.ensure_read_before_write(person, mount_id, norm)
        old = await client.read_text(norm)
        lines = old.splitlines(keepends=True)
        position = max(0, min(line - 1, len(lines)))
        insert = content if content.endswith("\n") else content + "\n"
        new = "".join(lines[:position]) + insert + "".join(lines[position:])
        await _commit(person, mount_id, client, norm, new, "insert_at_line")
        return {"path": norm, "applied": True, "line": line}

    @mcp.tool(
        name="fs.apply_patch", annotations=_DESTRUCTIVE, description="Apply a multi-file V4A patch within one volume."
    )
    async def fs_apply_patch(mount_id: str, patch_text: str) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        ops = parse_patch(patch_text)
        touched: list[dict[str, str]] = []
        for op in ops:
            norm = ctx.norm(op.path)
            if op.kind is OpKind.ADD:
                data = op.add_content.encode("utf-8")
                ctx.safety.charge_write(person, mount_id, len(data))
                await client.write_bytes_atomic(norm, data)
                touched.append({"path": norm, "op": "add"})
            elif op.kind is OpKind.DELETE:
                ctx.safety.ensure_read_before_write(person, mount_id, norm)
                await client.remove(norm)
                touched.append({"path": norm, "op": "delete"})
            else:
                ctx.safety.ensure_read_before_write(person, mount_id, norm)
                old = await client.read_text(norm)
                new = apply_update(old, op)
                await _commit(person, mount_id, client, norm, new, "apply_patch")
                if op.move_to:
                    dst = ctx.norm(op.move_to)
                    await client.rename(norm, dst)
                    touched.append({"path": norm, "op": "update", "moved_to": dst})
                else:
                    touched.append({"path": norm, "op": "update"})
            ctx.safety.record_audit(person, mount_id, "apply_patch", norm)
        return {"files": touched}


def _fuzzy_replace(text: str, search_block: str, replace_block: str, path: str) -> str:
    """Replace the window most similar to ``search_block`` (whitespace tolerant)."""
    lines = text.splitlines(keepends=True)
    needle = search_block.splitlines(keepends=True)
    span = len(needle)
    best_ratio, best_index = 0.0, -1
    for start in range(0, max(len(lines) - span + 1, 0)):
        candidate = "".join(lines[start : start + span])
        ratio = difflib.SequenceMatcher(None, candidate, search_block).ratio()
        if ratio > best_ratio:
            best_ratio, best_index = ratio, start
    if best_index < 0 or best_ratio < _FUZZY_THRESHOLD:
        raise ToolError(ErrorCode.NO_MATCH, f"no fuzzy match for search_block in '{path}'")
    block = replace_block if replace_block.endswith("\n") else replace_block + "\n"
    return "".join(lines[:best_index]) + block + "".join(lines[best_index + span :])
