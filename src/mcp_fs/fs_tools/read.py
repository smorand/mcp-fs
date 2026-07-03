"""Read family: read, read_bytes, read_lines, read_section, read_many, head, tail, count_lines.

Thin adapters over :mod:`mcp_fs.fs_ops` (shared with the /api/fs data plane).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from mcp_fs import fs_ops

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from mcp_fs.context import ToolContext

_READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, destructiveHint=False)


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    """Register the read-family tools."""

    @mcp.tool(name="fs.read", annotations=_READ_ONLY, description="Read a text file with line-numbered, paged output.")
    async def fs_read(
        mount_id: str,
        path: str,
        offset_lines: int = 0,
        limit_lines: int = 2000,
        line_numbered: bool = True,
    ) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        return await fs_ops.read_window(
            client,
            ctx.safety,
            person,
            mount_id,
            ctx.norm(path),
            offset_lines=offset_lines,
            limit_lines=limit_lines,
            line_numbered=line_numbered,
        )

    @mcp.tool(name="fs.read_bytes", annotations=_READ_ONLY, description="Read raw bytes (base64) with MIME type.")
    async def fs_read_bytes(
        mount_id: str, path: str, offset_bytes: int = 0, length_bytes: int = 65536
    ) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        return await fs_ops.read_bytes_b64(
            client, ctx.safety, person, mount_id, ctx.norm(path), offset=offset_bytes, length=length_bytes
        )

    @mcp.tool(
        name="fs.read_lines", annotations=_READ_ONLY, description="Read an inclusive line range [start_line, end_line]."
    )
    async def fs_read_lines(mount_id: str, path: str, start_line: int, end_line: int) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        return await fs_ops.read_lines(client, ctx.safety, person, mount_id, ctx.norm(path), start_line, end_line)

    @mcp.tool(
        name="fs.read_section", annotations=_READ_ONLY, description="Read the indentation block around an anchor line."
    )
    async def fs_read_section(mount_id: str, path: str, anchor_line: int, max_lines: int = 200) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        return await fs_ops.read_section(client, ctx.safety, person, mount_id, ctx.norm(path), anchor_line, max_lines)

    @mcp.tool(
        name="fs.read_many",
        annotations=_READ_ONLY,
        description="Batch read several files with per-file error isolation.",
    )
    async def fs_read_many(mount_id: str, paths: list[str], per_file_cap_lines: int = 500) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        return await fs_ops.read_many(client, ctx.safety, person, mount_id, paths, per_file_cap_lines)

    @mcp.tool(name="fs.head", annotations=_READ_ONLY, description="First N lines of a file.")
    async def fs_head(mount_id: str, path: str, lines: int = 20) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        return await fs_ops.head(client, ctx.safety, person, mount_id, ctx.norm(path), lines)

    @mcp.tool(name="fs.tail", annotations=_READ_ONLY, description="Last N lines of a file.")
    async def fs_tail(mount_id: str, path: str, lines: int = 20) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        return await fs_ops.tail(client, ctx.safety, person, mount_id, ctx.norm(path), lines)

    @mcp.tool(name="fs.count_lines", annotations=_READ_ONLY, description="Count lines without returning content.")
    async def fs_count_lines(mount_id: str, path: str) -> dict[str, Any]:
        _, client = await ctx.client(mount_id)
        return await fs_ops.count_lines(client, ctx.norm(path))
