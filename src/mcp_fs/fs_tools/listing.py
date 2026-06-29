"""List family: list_dir, tree."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from mcp_fs.context import ToolContext
    from mcp_fs.volume import VolumeClient

_READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, destructiveHint=False)
_DEFAULT_EXCLUDES = (".git", "node_modules", "target", "dist", ".build", "coverage")
_TREE_CAP = 2000


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
        norm = ctx.norm(path)
        excludes = set(_DEFAULT_EXCLUDES) | set(exclude_patterns or ())
        counter = [0]
        tree = await _build_tree(client, norm, max_depth, excludes, with_sizes, counter)
        return {"path": norm, "tree": tree, "truncated": counter[0] >= _TREE_CAP}


def _entry(name: str, kind: str, size: int, mtime: float, *, with_sizes: bool) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name, "kind": kind}
    if with_sizes:
        entry["size"] = size
        entry["mtime"] = mtime
    return entry


async def _build_tree(
    client: VolumeClient,
    path: str,
    depth: int,
    excludes: set[str],
    with_sizes: bool,
    counter: list[int],
) -> list[dict[str, Any]]:
    if depth < 0 or counter[0] >= _TREE_CAP:
        return []
    nodes: list[dict[str, Any]] = []
    for name, kind, size, _mtime in await client.listdir(path):
        if name in excludes:
            continue
        counter[0] += 1
        if counter[0] >= _TREE_CAP:
            break
        node: dict[str, Any] = {"name": name, "kind": kind}
        if with_sizes and kind == "file":
            node["size"] = size
        if kind == "dir" and depth > 0:
            node["children"] = await _build_tree(
                client, f"{path.rstrip('/')}/{name}", depth - 1, excludes, with_sizes, counter
            )
        nodes.append(node)
    return nodes
