"""Write family: write (no-clobber + atomic), append, create_empty.

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
    """Register the write-family tools."""

    @mcp.tool(
        name="fs.write",
        annotations=_DESTRUCTIVE,
        description="Create or overwrite a file (no-clobber by default, atomic).",
    )
    async def fs_write(
        mount_id: str,
        path: str,
        content: str,
        overwrite: bool = False,
        create_parents: bool = True,
    ) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        return await fs_ops.write_text(
            client,
            ctx.safety,
            person,
            mount_id,
            ctx.norm(path),
            content,
            overwrite=overwrite,
            create_parents=create_parents,
        )

    @mcp.tool(
        name="fs.append", annotations=_DESTRUCTIVE, description="Append content to a file (optionally create it)."
    )
    async def fs_append(mount_id: str, path: str, content: str, create: bool = False) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        return await fs_ops.append_text(client, ctx.safety, person, mount_id, ctx.norm(path), content, create=create)

    @mcp.tool(name="fs.create_empty", annotations=_DESTRUCTIVE, description="Create an empty file (touch).")
    async def fs_create_empty(mount_id: str, path: str, exist_ok: bool = False) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        return await fs_ops.create_empty(client, ctx.safety, person, mount_id, ctx.norm(path), exist_ok=exist_ok)
