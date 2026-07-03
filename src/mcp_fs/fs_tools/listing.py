"""List family: list_dir, tree."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from mcp_fs import fs_ops

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from mcp_fs.context import ToolContext

_READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, destructiveHint=False)


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    """Register the list-family tools."""

    @mcp.tool(
        name="fs.list_dir", annotations=_READ_ONLY, description="Flat directory listing with kinds and optional sizes."
    )
    async def fs_list_dir(
        mount_id: str,
        path: str = "/",
        include_hidden: bool = False,
        sort_by: str = "name",
        with_sizes: bool = False,
    ) -> dict[str, Any]:
        _, client = await ctx.client(mount_id)
        norm = ctx.norm(path)
        raw = await client.listdir(norm)
        entries = [
            _entry(name, kind, size, mtime, with_sizes=with_sizes)
            for name, kind, size, mtime in raw
            if include_hidden or not name.startswith(".")
        ]
        entries.sort(key=lambda item: item.get("size", 0) if sort_by == "size" else item["name"])
        return {"path": norm, "entries": entries, "total": len(entries)}

    @mcp.tool(name="fs.tree", annotations=_READ_ONLY, description="Recursive JSON tree to a maximum depth.")
    async def fs_tree(
        mount_id: str,
        path: str = "/",
        max_depth: int = 3,
        exclude_patterns: list[str] | None = None,
        with_sizes: bool = False,
    ) -> dict[str, Any]:
        _, client = await ctx.client(mount_id)
        return await fs_ops.tree(
            client,
            ctx.norm(path),
            max_depth=max_depth,
            exclude_patterns=tuple(exclude_patterns or ()),
            with_sizes=with_sizes,
        )


def _entry(name: str, kind: str, size: int, mtime: float, *, with_sizes: bool) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "kind": kind}
    if with_sizes:
        entry["size"] = size
        entry["mtime"] = mtime
    return entry
