"""Search family: glob, grep, find_definition, find_references."""

from __future__ import annotations

import fnmatch
import re
from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from mcp_fs import treesitter
from mcp_fs.models import ErrorCode, ToolError

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from mcp_fs.context import ToolContext
    from mcp_fs.volume import VolumeClient

_READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, destructiveHint=False)
_DEFAULT_EXCLUDES = (".git", "node_modules", "target", "dist", ".build", "coverage", ".mcp_trash")
_MAX_FILES = 5000
_GLOB_CAP = 100


async def _iter_files(client: VolumeClient, root: str, excludes: tuple[str, ...]) -> list[tuple[str, float]]:
    """Return ``(path, mtime)`` for files under ``root`` honoring directory excludes."""
    files: list[tuple[str, float]] = []
    for dirpath, _dirs, filenames in await client.walk(root):
        if any(f"/{segment}" in f"{dirpath}/" or dirpath.endswith(f"/{segment}") for segment in excludes):
            continue
        for filename in filenames:
            full = f"{dirpath.rstrip('/')}/{filename}"
            try:
                stat = await client.stat(full)
                files.append((full, stat.st_mtime))
            except OSError:
                continue
            if len(files) >= _MAX_FILES:
                return files
    return files


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    """Register the search-family tools."""

    @mcp.tool(name="fs.glob", annotations=_READ_ONLY, description="Find files by glob pattern, newest first (cap 100).")
    async def fs_glob(
        mount_id: str, pattern: str, root: str = "/", exclude_patterns: list[str] | None = None
    ) -> dict[str, Any]:
        _, client = await ctx.client(mount_id)
        base = ctx.norm(root)
        extra = tuple(exclude_patterns or ())
        matched = [
            (path, mtime)
            for path, mtime in await _iter_files(client, base, _DEFAULT_EXCLUDES)
            if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path.rsplit("/", 1)[-1], pattern)
            if not any(fnmatch.fnmatch(path, glob) for glob in extra)
        ]
        matched.sort(key=lambda item: item[1], reverse=True)
        return {"matches": [path for path, _ in matched[:_GLOB_CAP]], "truncated": len(matched) > _GLOB_CAP}

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
        base = ctx.norm(root)
        flags = 0 if case_sensitive else re.IGNORECASE
        matcher = re.compile(pattern if regex else re.escape(pattern), flags)
        hits: list[dict[str, Any]] = []
        files_with_matches: list[str] = []
        for path, _ in await _iter_files(client, base, _DEFAULT_EXCLUDES):
            if include_glob and not fnmatch.fnmatch(path, include_glob):
                continue
            if exclude_glob and fnmatch.fnmatch(path, exclude_glob):
                continue
            file_hits = await _grep_file(client, path, matcher, context_lines)
            if not file_hits:
                continue
            files_with_matches.append(path)
            hits.extend(file_hits)
            if len(hits) >= max_matches:
                break
        if output_mode == "files":
            return {"files": files_with_matches}
        if output_mode == "count":
            return {"count": len(hits), "files": len(files_with_matches)}
        return {"matches": hits[:max_matches], "truncated": len(hits) > max_matches}

    @mcp.tool(
        name="fs.find_definition", annotations=_READ_ONLY, description="Find a symbol definition via tree-sitter."
    )
    async def fs_find_definition(mount_id: str, name: str, root: str = "/", kind: str | None = None) -> dict[str, Any]:
        _, client = await ctx.client(mount_id)
        base = ctx.norm(root)
        results: list[dict[str, Any]] = []
        for path, _ in await _iter_files(client, base, _DEFAULT_EXCLUDES):
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
        for path, _ in await _iter_files(client, base, _DEFAULT_EXCLUDES):
            if treesitter.language_for(path) is None:
                continue
            source = await client.read_bytes(path)
            for match in treesitter.find_references(path, source, name):
                results.append({"path": match.path, "line": match.line, "kind": match.kind})
        return {"references": results}


async def _grep_file(
    client: VolumeClient, path: str, matcher: re.Pattern[str], context_lines: int
) -> list[dict[str, Any]]:
    try:
        text = await client.read_text(path)
    except OSError:
        return []
    lines = text.splitlines()
    out: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if matcher.search(line):
            start = max(0, index - context_lines)
            end = min(len(lines), index + context_lines + 1)
            out.append(
                {
                    "path": path,
                    "line": index + 1,
                    "text": line,
                    "context": lines[start:end] if context_lines else None,
                }
            )
    return out
