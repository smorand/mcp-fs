"""Metadata family: stat, exists, hash (thin adapters over fs_ops)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from mcp_fs import fs_ops

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from mcp_fs.context import ToolContext

_READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, destructiveHint=False)


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    """Register the metadata-family tools."""

    @mcp.tool(name="fs.stat", annotations=_READ_ONLY, description="POSIX metadata for a path.")
    async def fs_stat(mount_id: str, path: str) -> dict[str, Any]:
        _, client = await ctx.client(mount_id)
        return await fs_ops.stat_info(client, ctx.norm(path))

    @mcp.tool(name="fs.exists", annotations=_READ_ONLY, description="Probe whether a path exists and its kind.")
    async def fs_exists(mount_id: str, path: str) -> dict[str, Any]:
        _, client = await ctx.client(mount_id)
        return await fs_ops.exists_info(client, ctx.norm(path))

    @mcp.tool(name="fs.hash", annotations=_READ_ONLY, description="Content hash (md5|sha1|sha256|sha512).")
    async def fs_hash(mount_id: str, path: str, algo: str = "sha256") -> dict[str, Any]:
        _, client = await ctx.client(mount_id)
        return await fs_ops.hash_file(client, ctx.norm(path), algo)
