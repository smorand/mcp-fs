"""Metadata family: stat, exists, hash."""

from __future__ import annotations

import hashlib
import stat as stat_module
from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from mcp_fs.models import ErrorCode, ToolError

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from mcp_fs.context import ToolContext

_READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, destructiveHint=False)
_ALLOWED_ALGOS = frozenset({"md5", "sha1", "sha256", "sha512"})


def _kind_of(mode: int) -> str:
    if stat_module.S_ISDIR(mode):
        return "dir"
    if stat_module.S_ISLNK(mode):
        return "symlink"
    if stat_module.S_ISREG(mode):
        return "file"
    return "other"


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    """Register the metadata-family tools."""

    @mcp.tool(name="fs.stat", annotations=_READ_ONLY, description="POSIX metadata for a path.")
    async def fs_stat(mount_id: str, path: str) -> dict[str, Any]:
        _, client = await ctx.client(mount_id)
        norm = ctx.norm(path)
        st = await client.stat(norm)
        return {
            "path": norm,
            "size": st.st_size,
            "mode": oct(stat_module.S_IMODE(st.st_mode)),
            "kind": _kind_of(st.st_mode),
            "mtime": st.st_mtime,
            "ctime": st.st_ctime,
            "atime": st.st_atime,
            "uid": st.st_uid,
            "gid": st.st_gid,
        }

    @mcp.tool(name="fs.exists", annotations=_READ_ONLY, description="Probe whether a path exists and its kind.")
    async def fs_exists(mount_id: str, path: str) -> dict[str, Any]:
        _, client = await ctx.client(mount_id)
        norm = ctx.norm(path)
        if not await client.exists(norm):
            return {"exists": False, "kind": None}
        st = await client.stat(norm)
        return {"exists": True, "kind": _kind_of(st.st_mode)}

    @mcp.tool(name="fs.hash", annotations=_READ_ONLY, description="Content hash (md5|sha1|sha256|sha512).")
    async def fs_hash(mount_id: str, path: str, algo: str = "sha256") -> dict[str, Any]:
        if algo not in _ALLOWED_ALGOS:
            raise ToolError(ErrorCode.INVALID_ARGUMENT, f"unsupported algo '{algo}'")
        _, client = await ctx.client(mount_id)
        norm = ctx.norm(path)
        data = await client.read_bytes(norm)
        digest = hashlib.new(algo, data).hexdigest()
        return {"path": norm, "algo": algo, "hash": digest, "size": len(data)}
