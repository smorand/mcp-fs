"""Edit family: edit, multi_edit, search_replace, apply_patch (V4A), insert_at_line.

Thin adapters over :mod:`mcp_fs.fs_ops` (shared with the /api/fs data plane).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from mcp_fs import fs_ops

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from mcp_fs.context import ToolContext

_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    """Register the edit-family tools."""

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
        return await fs_ops.edit_unique(
            client,
            ctx.safety,
            person,
            mount_id,
            ctx.norm(path),
            old_string,
            new_string,
            replace_all=replace_all,
            dry_run=dry_run,
        )

    @mcp.tool(
        name="fs.multi_edit", annotations=_DESTRUCTIVE, description="Apply several edits atomically (all or nothing)."
    )
    async def fs_multi_edit(
        mount_id: str, path: str, edits: list[dict[str, Any]], dry_run: bool = False
    ) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        return await fs_ops.multi_edit(client, ctx.safety, person, mount_id, ctx.norm(path), edits, dry_run=dry_run)

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
        return await fs_ops.search_replace(
            client, ctx.safety, person, mount_id, ctx.norm(path), search_block, replace_block, fuzzy=fuzzy
        )

    @mcp.tool(
        name="fs.insert_at_line", annotations=_DESTRUCTIVE, description="Insert content before a 1-based line number."
    )
    async def fs_insert_at_line(mount_id: str, path: str, line: int, content: str) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        return await fs_ops.insert_at_line(client, ctx.safety, person, mount_id, ctx.norm(path), line, content)

    @mcp.tool(
        name="fs.apply_patch", annotations=_DESTRUCTIVE, description="Apply a multi-file V4A patch within one volume."
    )
    async def fs_apply_patch(mount_id: str, patch_text: str) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        return await fs_ops.apply_patch(client, ctx.safety, person, mount_id, patch_text)
