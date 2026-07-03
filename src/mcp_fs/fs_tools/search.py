"""Search family: glob, grep, find_definition, find_references."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from mcp_fs import fs_ops, treesitter
from mcp_fs.fs_ops import DEFAULT_EXCLUDES
from mcp_fs.models import ErrorCode, ToolError

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from mcp_fs.context import ToolContext

_READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, destructiveHint=False)


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    """Register the search-family tools."""

    @mcp.tool(name="fs.glob", annotations=_READ_ONLY, description="Find files by glob pattern, newest first (cap 100).")
    async def fs_glob(
        mount_id: str, pattern: str, root: str = "/", exclude_patterns: list[str] | None = None
    ) -> dict[str, Any]:
        _, client = await ctx.client(mount_id)
        return await fs_ops.glob_files(client, ctx.norm(root), pattern, extra_excludes=tuple(exclude_patterns or ()))

    @mcp.tool(name="fs.grep", annotations=_READ_ONLY, description="Search file contents (files|content|count modes).")
    async def fs_grep(
        mount_id: str,
        pattern: str,
        root: str = "/",
        include_glob: str | None = None,
        exclude_glob: str | None = None,
        regex: bool = True,
        case_sensitive: bool = True,
        output_mode: str = "content",
        context_lines: int = 0,
        max_matches: int = 100,
    ) -> dict[str, Any]:
        _, client = await ctx.client(mount_id)
        return await fs_ops.grep_files(
            client,
            ctx.norm(root),
            pattern,
            include_glob=include_glob,
            exclude_glob=exclude_glob,
            regex=regex,
            case_sensitive=case_sensitive,
            output_mode=output_mode,
            context_lines=context_lines,
            max_matches=max_matches,
        )

    @mcp.tool(
        name="fs.find_definition", annotations=_READ_ONLY, description="Find a symbol definition via tree-sitter."
    )
    async def fs_find_definition(mount_id: str, name: str, root: str = "/", kind: str | None = None) -> dict[str, Any]:
        _, client = await ctx.client(mount_id)
        base = ctx.norm(root)
        results: list[dict[str, Any]] = []
        for path, _ in await fs_ops.iter_files(client, base, DEFAULT_EXCLUDES):
            if treesitter.language_for(path) is None:
                continue
            source = await client.read_bytes(path)
            for match in treesitter.find_definitions(path, source, name, kind):
                results.append({"path": match.path, "name": match.name, "kind": match.kind, "line": match.line})
        return {"definitions": results}

    @mcp.tool(
        name="fs.find_references", annotations=_READ_ONLY, description="Find identifier references via tree-sitter."
    )
    async def fs_find_references(mount_id: str, name: str, root: str = "/") -> dict[str, Any]:
        if not name:
            raise ToolError(ErrorCode.INVALID_ARGUMENT, "name is required")
        _, client = await ctx.client(mount_id)
        base = ctx.norm(root)
        results: list[dict[str, Any]] = []
        for path, _ in await fs_ops.iter_files(client, base, DEFAULT_EXCLUDES):
            if treesitter.language_for(path) is None:
                continue
            source = await client.read_bytes(path)
            for match in treesitter.find_references(path, source, name):
                results.append({"path": match.path, "line": match.line, "kind": match.kind})
        return {"references": results}
