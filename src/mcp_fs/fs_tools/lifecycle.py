"""Lifecycle family: mkdir, delete (trash by default), move, copy, list_allowed_roots, audit_log."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from mcp_fs.models import ErrorCode, ToolError

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from mcp_fs.context import ToolContext
    from mcp_fs.volume import VolumeClient

_READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, destructiveHint=False)
_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    """Register the lifecycle-family tools."""

    @mcp.tool(name="fs.mkdir", annotations=_DESTRUCTIVE, description="Create a directory (parents by default).")
    async def fs_mkdir(mount_id: str, path: str, parents: bool = True, exist_ok: bool = True) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        norm = ctx.norm(path)
        if parents:
            await client.makedirs(norm, exist_ok=exist_ok)
        elif await client.exists(norm):
            if not exist_ok:
                raise ToolError(ErrorCode.NO_CLOBBER, f"'{norm}' already exists")
        else:
            await client.mkdir(norm)
        ctx.safety.record_audit(person, mount_id, "mkdir", norm)
        return {"path": norm, "created": True}

    @mcp.tool(name="fs.delete", annotations=_DESTRUCTIVE, description="Delete a path (moves to trash by default).")
    async def fs_delete(mount_id: str, path: str, recursive: bool = False, trash: bool = True) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        norm = ctx.norm(path)
        if not await client.exists(norm):
            raise ToolError(ErrorCode.NOT_FOUND, f"'{norm}' does not exist")
        is_dir = await client.is_dir(norm)
        if is_dir and not recursive:
            raise ToolError(ErrorCode.INVALID_ARGUMENT, f"'{norm}' is a directory (pass recursive=true)")
        hard = not trash
        if hard and not ctx.config.safety.allow_hard_delete:
            raise ToolError(ErrorCode.NOT_SUPPORTED, "hard delete disabled (server started without allow_hard_delete)")
        if hard:
            await (client.rmtree(norm) if is_dir else client.remove(norm))
            destination = None
        else:
            destination = ctx.safety.trash_path(norm)
            await client.makedirs(destination.rsplit("/", 1)[0], exist_ok=True)
            await client.rename(norm, destination)
        ctx.safety.record_audit(person, mount_id, "delete", norm, "hard" if hard else f"-> {destination}")
        return {"path": norm, "trashed": not hard, "trash_path": destination}

    @mcp.tool(
        name="fs.move", annotations=_DESTRUCTIVE, description="Rename or relocate a path (no-clobber by default)."
    )
    async def fs_move(mount_id: str, source: str, destination: str, overwrite: bool = False) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        src = ctx.norm(source)
        dst = ctx.norm(destination)
        if not await client.exists(src):
            raise ToolError(ErrorCode.NOT_FOUND, f"'{src}' does not exist")
        if await client.exists(dst) and not overwrite:
            raise ToolError(ErrorCode.NO_CLOBBER, f"'{dst}' exists (pass overwrite=true)")
        await client.rename(src, dst)
        ctx.safety.record_audit(person, mount_id, "move", src, f"-> {dst}")
        return {"source": src, "destination": dst}

    @mcp.tool(name="fs.copy", annotations=_DESTRUCTIVE, description="Copy a file or tree (no-clobber by default).")
    async def fs_copy(
        mount_id: str,
        source: str,
        destination: str,
        overwrite: bool = False,
        recursive: bool = False,
    ) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        src = ctx.norm(source)
        dst = ctx.norm(destination)
        if not await client.exists(src):
            raise ToolError(ErrorCode.NOT_FOUND, f"'{src}' does not exist")
        if await client.exists(dst) and not overwrite:
            raise ToolError(ErrorCode.NO_CLOBBER, f"'{dst}' exists (pass overwrite=true)")
        if await client.is_dir(src):
            if not recursive:
                raise ToolError(ErrorCode.INVALID_ARGUMENT, f"'{src}' is a directory (pass recursive=true)")
            await _copy_tree(client, src, dst)
        else:
            data = await client.read_bytes(src)
            ctx.safety.charge_write(person, mount_id, len(data))
            await client.write_bytes_atomic(dst, data)
        ctx.safety.record_audit(person, mount_id, "copy", src, f"-> {dst}")
        return {"source": src, "destination": dst}

    @mcp.tool(
        name="fs.list_allowed_roots", annotations=_READ_ONLY, description="List the volume roots the caller can access."
    )
    async def fs_list_allowed_roots(mount_id: str) -> dict[str, Any]:
        person = await ctx.authorize(mount_id)
        projects = await ctx.store.list_projects_for(person)
        return {"person": person, "roots": [{"mount_id": p.id, "root": "/", "owner": p.owner} for p in projects]}

    @mcp.tool(name="fs.audit_log", annotations=_READ_ONLY, description="Recent mutations performed in this session.")
    async def fs_audit_log(mount_id: str, since: float | None = None, limit: int = 20) -> dict[str, Any]:
        person = await ctx.authorize(mount_id)
        entries = list(ctx.safety.session(person, mount_id).audit)
        if since is not None:
            entries = [entry for entry in entries if entry.timestamp >= since]
        recent = entries[-limit:]
        return {
            "entries": [
                {"timestamp": entry.timestamp, "op": entry.op, "path": entry.path, "detail": entry.detail}
                for entry in recent
            ]
        }


async def _copy_tree(client: VolumeClient, src: str, dst: str) -> None:
    await client.makedirs(dst, exist_ok=True)
    for name, kind, _size, _mtime in await client.listdir(src):
        child_src = f"{src.rstrip('/')}/{name}"
        child_dst = f"{dst.rstrip('/')}/{name}"
        if kind == "dir":
            await _copy_tree(client, child_src, child_dst)
        else:
            data = await client.read_bytes(child_src)
            await client.write_bytes_atomic(child_dst, data)
