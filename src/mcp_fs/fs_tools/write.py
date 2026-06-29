"""Write family: write (no-clobber + atomic), append, create_empty."""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from mcp_fs.models import ErrorCode, ToolError

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
        norm = ctx.norm(path)
        exists = await client.exists(norm)
        if exists and not overwrite:
            raise ToolError(ErrorCode.NO_CLOBBER, f"'{norm}' exists (pass overwrite=true)")
        diff = ""
        if exists:
            ctx.safety.ensure_read_before_write(person, mount_id, norm)
            old = await client.read_text(norm)
            diff = "".join(
                difflib.unified_diff(old.splitlines(keepends=True), content.splitlines(keepends=True), norm, norm)
            )
        if create_parents:
            parent = norm.rsplit("/", 1)[0] or "/"
            if parent != "/":
                await client.makedirs(parent, exist_ok=True)
        data = content.encode("utf-8")
        ctx.safety.charge_write(person, mount_id, len(data))
        await client.write_bytes_atomic(norm, data)
        ctx.safety.record_read(person, mount_id, norm)
        ctx.safety.record_audit(person, mount_id, "write", norm, f"{len(data)} bytes")
        return {"path": norm, "bytes_written": len(data), "overwritten": exists, "diff": diff}

    @mcp.tool(
        name="fs.append", annotations=_DESTRUCTIVE, description="Append content to a file (optionally create it)."
    )
    async def fs_append(mount_id: str, path: str, content: str, create: bool = False) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        norm = ctx.norm(path)
        if not await client.exists(norm) and not create:
            raise ToolError(ErrorCode.NOT_FOUND, f"'{norm}' does not exist (pass create=true)")
        data = content.encode("utf-8")
        ctx.safety.charge_write(person, mount_id, len(data))
        await client.append_bytes(norm, data)
        ctx.safety.record_audit(person, mount_id, "append", norm, f"{len(data)} bytes")
        return {"path": norm, "bytes_appended": len(data)}

    @mcp.tool(name="fs.create_empty", annotations=_DESTRUCTIVE, description="Create an empty file (touch).")
    async def fs_create_empty(mount_id: str, path: str, exist_ok: bool = False) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        norm = ctx.norm(path)
        if await client.exists(norm):
            if not exist_ok:
                raise ToolError(ErrorCode.NO_CLOBBER, f"'{norm}' already exists")
            return {"path": norm, "created": False}
        await client.create_empty(norm)
        ctx.safety.record_audit(person, mount_id, "create_empty", norm)
        return {"path": norm, "created": True}
