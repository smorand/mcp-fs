"""Filesystem operations shared by the MCP tools and the /api/fs data plane.

Each function is the single implementation of an operation, working on a
:class:`VolumeClient` plus (for mutating/reading-recorded ops) the safety manager
and the caller identity. The tool closures (``fs_tools/*``) and the REST router
(``dataplane.py``) are thin adapters over these, so the two surfaces stay iso.

Everything here is async and non-blocking except the CPU-bound document helpers,
which the callers already run via ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import hashlib
import mimetypes
import re
import stat as stat_module
from typing import TYPE_CHECKING, Any

from mcp_fs.docx_writer import markdown_to_docx
from mcp_fs.extract import UnsupportedDocument, extract
from mcp_fs.models import ErrorCode, ToolError

if TYPE_CHECKING:
    from mcp_fs.safety import SafetyManager
    from mcp_fs.volume import VolumeClient

DEFAULT_EXCLUDES = (".git", "node_modules", "target", "dist", ".build", "coverage", ".mcp_trash")
ALLOWED_ALGOS = frozenset({"md5", "sha1", "sha256", "sha512"})
MD_COMPANION_EXTS = frozenset(
    {
        ".pdf",
        ".docx",
        ".pptx",
        ".pptm",
        ".potx",
        ".ppsx",
        ".xlsx",
        ".xlsm",
        ".html",
        ".htm",
        ".csv",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".tif",
        ".tiff",
        ".webp",
    }
)
_MAX_FILES = 5000
_GLOB_CAP = 100


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #
def number_lines(lines: list[str], start: int) -> str:
    return "\n".join(f"{start + offset}\t{line}" for offset, line in enumerate(lines))


async def read_window(
    client: VolumeClient,
    safety: SafetyManager,
    person: str,
    mount_id: str,
    path: str,
    *,
    offset_lines: int = 0,
    limit_lines: int = 2000,
    line_numbered: bool = True,
) -> dict[str, Any]:
    text = await client.read_text(path)
    safety.record_read(person, mount_id, path)
    lines = text.splitlines()
    total = len(lines)
    cap = min(limit_lines, safety.config.max_read_lines)
    window = lines[offset_lines : offset_lines + cap]
    truncated = offset_lines + cap < total
    content = number_lines(window, offset_lines + 1) if line_numbered else "\n".join(window)
    return {
        "content": content,
        "total_lines": total,
        "truncated": truncated,
        "next_offset": offset_lines + cap if truncated else None,
    }


async def read_bytes_b64(
    client: VolumeClient,
    safety: SafetyManager,
    person: str,
    mount_id: str,
    path: str,
    *,
    offset: int = 0,
    length: int = 65536,
) -> dict[str, Any]:
    data = await client.read_bytes(path, offset, length)
    safety.record_read(person, mount_id, path)
    mime, _ = mimetypes.guess_type(path)
    return {
        "base64": base64.b64encode(data).decode("ascii"),
        "mime_type": mime or "application/octet-stream",
        "length": len(data),
    }


async def count_lines(client: VolumeClient, path: str) -> dict[str, Any]:
    text = await client.read_text(path)
    return {"total_lines": len(text.splitlines())}


# --------------------------------------------------------------------------- #
# Metadata
# --------------------------------------------------------------------------- #
def kind_of(mode: int) -> str:
    if stat_module.S_ISDIR(mode):
        return "dir"
    if stat_module.S_ISLNK(mode):
        return "symlink"
    if stat_module.S_ISREG(mode):
        return "file"
    return "other"


async def stat_info(client: VolumeClient, path: str) -> dict[str, Any]:
    st = await client.stat(path)
    return {
        "path": path,
        "size": st.st_size,
        "mode": oct(stat_module.S_IMODE(st.st_mode)),
        "kind": kind_of(st.st_mode),
        "mtime": st.st_mtime,
        "ctime": st.st_ctime,
        "atime": st.st_atime,
        "uid": st.st_uid,
        "gid": st.st_gid,
    }


async def exists_info(client: VolumeClient, path: str) -> dict[str, Any]:
    if not await client.exists(path):
        return {"exists": False, "kind": None}
    st = await client.stat(path)
    return {"exists": True, "kind": kind_of(st.st_mode)}


async def hash_file(client: VolumeClient, path: str, algo: str = "sha256") -> dict[str, Any]:
    if algo not in ALLOWED_ALGOS:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, f"unsupported algo '{algo}'")
    data = await client.read_bytes(path)
    return {"path": path, "algo": algo, "hash": hashlib.new(algo, data).hexdigest(), "size": len(data)}


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
async def iter_files(client: VolumeClient, root: str, excludes: tuple[str, ...]) -> list[tuple[str, float]]:
    """Return ``(path, mtime)`` for files under ``root`` honoring directory excludes."""
    files: list[tuple[str, float]] = []
    for dirpath, _dirs, filenames in await client.walk(root):
        if any(f"/{segment}" in f"{dirpath}/" or dirpath.endswith(f"/{segment}") for segment in excludes):
            continue
        for filename in filenames:
            full = f"{dirpath.rstrip('/')}/{filename}"
            try:
                stat = await client.stat(full)
            except OSError:
                continue
            files.append((full, stat.st_mtime))
            if len(files) >= _MAX_FILES:
                return files
    return files


async def glob_files(
    client: VolumeClient, root: str, pattern: str, *, extra_excludes: tuple[str, ...] = ()
) -> dict[str, Any]:
    matched = [
        (path, mtime)
        for path, mtime in await iter_files(client, root, DEFAULT_EXCLUDES)
        if (fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path.rsplit("/", 1)[-1], pattern))
        and not any(fnmatch.fnmatch(path, glob) for glob in extra_excludes)
    ]
    matched.sort(key=lambda item: item[1], reverse=True)
    return {"matches": [path for path, _ in matched[:_GLOB_CAP]], "truncated": len(matched) > _GLOB_CAP}


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
                {"path": path, "line": index + 1, "text": line, "context": lines[start:end] if context_lines else None}
            )
    return out


async def grep_files(
    client: VolumeClient,
    root: str,
    pattern: str,
    *,
    include_glob: str | None = None,
    exclude_glob: str | None = None,
    regex: bool = True,
    case_sensitive: bool = True,
    output_mode: str = "content",
    context_lines: int = 0,
    max_matches: int = 100,
) -> dict[str, Any]:
    flags = 0 if case_sensitive else re.IGNORECASE
    matcher = re.compile(pattern if regex else re.escape(pattern), flags)
    hits: list[dict[str, Any]] = []
    files_with_matches: list[str] = []
    for path, _ in await iter_files(client, root, DEFAULT_EXCLUDES):
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


# --------------------------------------------------------------------------- #
# Copy
# --------------------------------------------------------------------------- #
async def _copy_tree(client: VolumeClient, src: str, dst: str) -> None:
    await client.makedirs(dst, exist_ok=True)
    for name, kind, _size, _mtime in await client.listdir(src):
        child_src = f"{src.rstrip('/')}/{name}"
        child_dst = f"{dst.rstrip('/')}/{name}"
        if kind == "dir":
            await _copy_tree(client, child_src, child_dst)
        else:
            await client.write_bytes_atomic(child_dst, await client.read_bytes(child_src))


async def copy_path(
    client: VolumeClient,
    safety: SafetyManager,
    person: str,
    mount_id: str,
    src: str,
    dst: str,
    *,
    overwrite: bool = False,
    recursive: bool = False,
) -> dict[str, Any]:
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
        safety.charge_write(person, mount_id, len(data))
        await client.write_bytes_atomic(dst, data)
    safety.record_audit(person, mount_id, "copy", src, f"-> {dst}")
    return {"source": src, "destination": dst}


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #
def companion_path(norm: str) -> str:
    """Return the ``.md`` companion path for a source (``report.pdf`` -> ``report.md``)."""
    dot = norm.rfind(".")
    stem = norm[:dot] if dot > norm.rfind("/") else norm
    return f"{stem}.md"


def _doc_payload(
    source: str, md_path: str | None, fmt: str, text: str, preview_chars: int, *, cached: bool
) -> dict[str, Any]:
    return {
        "path": source,
        "md_path": md_path,
        "format": fmt,
        "chars": len(text),
        "cached": cached,
        "preview": text[:preview_chars],
    }


async def extract_document(
    client: VolumeClient,
    safety: SafetyManager,
    person: str,
    mount_id: str,
    norm: str,
    *,
    max_chars: int = 200_000,
    preview_chars: int = 4_000,
    ocr: bool = True,
    refresh: bool = False,
) -> dict[str, Any]:
    if not await client.is_file(norm):
        raise ToolError(ErrorCode.NOT_FOUND, f"not a file: {norm}")
    ext = f".{norm.rsplit('.', 1)[-1].lower()}" if "." in norm else ""
    md_path = companion_path(norm) if ext in MD_COMPANION_EXTS else None

    if (
        md_path
        and not refresh
        and await client.exists(md_path)
        and (await client.stat(md_path)).st_mtime >= (await client.stat(norm)).st_mtime
    ):
        text = await client.read_text(md_path)
        return _doc_payload(norm, md_path, "md", text, preview_chars, cached=True)

    data = await client.read_bytes(norm)
    try:
        result = await asyncio.to_thread(extract, data, norm, max_chars=max_chars, ocr=ocr)
    except UnsupportedDocument as exc:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, str(exc)) from exc
    except Exception as exc:  # a corrupt/misnamed file is caller data, not a server bug
        raise ToolError(ErrorCode.INVALID_ARGUMENT, f"could not extract {norm}: {exc}") from exc

    if md_path and result.text.strip():
        md_bytes = result.text.encode("utf-8")
        safety.charge_write(person, mount_id, len(md_bytes))
        await client.write_bytes_atomic(md_path, md_bytes)
        safety.record_read(person, mount_id, md_path)
        safety.record_audit(person, mount_id, "extract_text", md_path, f"{len(md_bytes)} bytes")
    else:
        md_path = None
    payload = _doc_payload(norm, md_path, result.fmt, result.text, preview_chars, cached=False)
    payload["truncated"] = result.truncated
    payload["meta"] = result.meta
    payload["note"] = result.note
    return payload


async def write_docx(
    client: VolumeClient,
    safety: SafetyManager,
    person: str,
    mount_id: str,
    norm: str,
    markdown: str,
    *,
    title: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    if not norm.lower().endswith(".docx"):
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "path must end with .docx")
    exists = await client.exists(norm)
    if exists and not overwrite:
        raise ToolError(ErrorCode.NO_CLOBBER, f"'{norm}' exists (pass overwrite=true)")
    if exists:
        safety.ensure_read_before_write(person, mount_id, norm)
    data = await asyncio.to_thread(markdown_to_docx, markdown, title=title)
    parent = norm.rsplit("/", 1)[0] or "/"
    if parent != "/":
        await client.makedirs(parent, exist_ok=True)
    safety.charge_write(person, mount_id, len(data))
    await client.write_bytes_atomic(norm, data)
    safety.record_read(person, mount_id, norm)
    safety.record_audit(person, mount_id, "write_docx", norm, f"{len(data)} bytes")
    return {"path": norm, "bytes_written": len(data), "overwritten": exists}
