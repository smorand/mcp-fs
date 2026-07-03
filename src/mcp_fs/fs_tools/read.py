"""Read family: read, read_bytes, read_lines, read_section, read_many, head, tail, count_lines."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from mcp_fs import fs_ops
from mcp_fs.fs_ops import number_lines as _number_lines
from mcp_fs.models import ErrorCode, ToolError

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
        norm = ctx.norm(path)
        text = await client.read_text(norm)
        ctx.safety.record_read(person, mount_id, norm)
        lines = text.splitlines()
        window = lines[max(start_line - 1, 0) : end_line]
        return {"content": _number_lines(window, max(start_line, 1)), "total_lines": len(lines)}

    @mcp.tool(
        name="fs.read_section", annotations=_READ_ONLY, description="Read the indentation block around an anchor line."
    )
    async def fs_read_section(mount_id: str, path: str, anchor_line: int, max_lines: int = 200) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        norm = ctx.norm(path)
        text = await client.read_text(norm)
        ctx.safety.record_read(person, mount_id, norm)
        lines = text.splitlines()
        start, end = _indent_block(lines, anchor_line - 1, max_lines)
        return {
            "content": _number_lines(lines[start:end], start + 1),
            "start_line": start + 1,
            "end_line": end,
        }

    @mcp.tool(
        name="fs.read_many",
        annotations=_READ_ONLY,
        description="Batch read several files with per-file error isolation.",
    )
    async def fs_read_many(mount_id: str, paths: list[str], per_file_cap_lines: int = 500) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        results: list[dict[str, Any]] = []
        for raw_path in paths:
            try:
                norm = ctx.norm(raw_path)
                text = await client.read_text(norm)
                ctx.safety.record_read(person, mount_id, norm)
                lines = text.splitlines()
                results.append(
                    {
                        "path": norm,
                        "content": _number_lines(lines[:per_file_cap_lines], 1),
                        "truncated": len(lines) > per_file_cap_lines,
                    }
                )
            except (ToolError, OSError) as exc:
                results.append({"path": raw_path, "error": str(exc)})
        return {"files": results}

    @mcp.tool(name="fs.head", annotations=_READ_ONLY, description="First N lines of a file.")
    async def fs_head(mount_id: str, path: str, lines: int = 20) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        norm = ctx.norm(path)
        text = await client.read_text(norm)
        ctx.safety.record_read(person, mount_id, norm)
        head = text.splitlines()[:lines]
        return {"content": _number_lines(head, 1)}

    @mcp.tool(name="fs.tail", annotations=_READ_ONLY, description="Last N lines of a file.")
    async def fs_tail(mount_id: str, path: str, lines: int = 20) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        norm = ctx.norm(path)
        text = await client.read_text(norm)
        ctx.safety.record_read(person, mount_id, norm)
        all_lines = text.splitlines()
        start = max(len(all_lines) - lines, 0)
        return {"content": _number_lines(all_lines[start:], start + 1)}

    @mcp.tool(name="fs.count_lines", annotations=_READ_ONLY, description="Count lines without returning content.")
    async def fs_count_lines(mount_id: str, path: str) -> dict[str, Any]:
        _, client = await ctx.client(mount_id)
        return await fs_ops.count_lines(client, ctx.norm(path))


def _indent_block(lines: list[str], anchor: int, max_lines: int) -> tuple[int, int]:
    """Return [start, end) bounds of the indentation block surrounding ``anchor``."""
    if not lines:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "file is empty")
    anchor = max(0, min(anchor, len(lines) - 1))
    base_indent = _indent_of(lines[anchor])
    start = anchor
    while start > 0:
        previous = lines[start - 1]
        if previous.strip() and _indent_of(previous) < base_indent:
            start -= 1
            break
        start -= 1
    end = anchor + 1
    while end < len(lines) and end - start < max_lines:
        current = lines[end]
        if current.strip() and _indent_of(current) < base_indent:
            break
        end += 1
    return start, end


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())
