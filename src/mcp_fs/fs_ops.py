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
import difflib
import fnmatch
import hashlib
import mimetypes
import re
import stat as stat_module
from typing import TYPE_CHECKING, Any

from mcp_fs import treesitter
from mcp_fs.docx_writer import markdown_to_docx
from mcp_fs.extract import UnsupportedDocument, extract
from mcp_fs.fs_tools.patch_v4a import OpKind, apply_update, parse_patch
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


# --------------------------------------------------------------------------- #
# Read variants
# --------------------------------------------------------------------------- #
async def read_lines(
    client: VolumeClient, safety: SafetyManager, person: str, mount_id: str, path: str, start_line: int, end_line: int
) -> dict[str, Any]:
    text = await client.read_text(path)
    safety.record_read(person, mount_id, path)
    lines = text.splitlines()
    window = lines[max(start_line - 1, 0) : end_line]
    return {"content": number_lines(window, max(start_line, 1)), "total_lines": len(lines)}


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _indent_block(lines: list[str], anchor: int, max_lines: int) -> tuple[int, int]:
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
        if lines[end].strip() and _indent_of(lines[end]) < base_indent:
            break
        end += 1
    return start, end


async def read_section(
    client: VolumeClient,
    safety: SafetyManager,
    person: str,
    mount_id: str,
    path: str,
    anchor_line: int,
    max_lines: int = 200,
) -> dict[str, Any]:
    text = await client.read_text(path)
    safety.record_read(person, mount_id, path)
    lines = text.splitlines()
    start, end = _indent_block(lines, anchor_line - 1, max_lines)
    return {"content": number_lines(lines[start:end], start + 1), "start_line": start + 1, "end_line": end}


async def read_many(
    client: VolumeClient,
    safety: SafetyManager,
    person: str,
    mount_id: str,
    paths: list[str],
    per_file_cap_lines: int = 500,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for raw_path in paths:
        try:
            norm = safety.normalize_path(raw_path)
            text = await client.read_text(norm)
            safety.record_read(person, mount_id, norm)
            lines = text.splitlines()
            results.append(
                {
                    "path": norm,
                    "content": number_lines(lines[:per_file_cap_lines], 1),
                    "truncated": len(lines) > per_file_cap_lines,
                }
            )
        except (ToolError, OSError) as exc:
            results.append({"path": raw_path, "error": str(exc)})
    return {"files": results}


async def head(
    client: VolumeClient, safety: SafetyManager, person: str, mount_id: str, path: str, lines: int = 20
) -> dict[str, Any]:
    text = await client.read_text(path)
    safety.record_read(person, mount_id, path)
    return {"content": number_lines(text.splitlines()[:lines], 1)}


async def tail(
    client: VolumeClient, safety: SafetyManager, person: str, mount_id: str, path: str, lines: int = 20
) -> dict[str, Any]:
    text = await client.read_text(path)
    safety.record_read(person, mount_id, path)
    all_lines = text.splitlines()
    start = max(len(all_lines) - lines, 0)
    return {"content": number_lines(all_lines[start:], start + 1)}


# --------------------------------------------------------------------------- #
# Recursive tree listing
# --------------------------------------------------------------------------- #
_TREE_CAP = 2000


async def build_tree(
    client: VolumeClient, path: str, depth: int, excludes: set[str], with_sizes: bool, counter: list[int]
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
            node["children"] = await build_tree(
                client, f"{path.rstrip('/')}/{name}", depth - 1, excludes, with_sizes, counter
            )
        nodes.append(node)
    return nodes


async def tree(
    client: VolumeClient,
    root: str,
    *,
    max_depth: int = 3,
    exclude_patterns: tuple[str, ...] = (),
    with_sizes: bool = False,
) -> dict[str, Any]:
    tree_excludes = {".git", "node_modules", "target", "dist", ".build", "coverage"} | set(exclude_patterns)
    counter = [0]
    nodes = await build_tree(client, root, max_depth, tree_excludes, with_sizes, counter)
    return {"path": root, "tree": nodes, "truncated": counter[0] >= _TREE_CAP}


# --------------------------------------------------------------------------- #
# Write / edit
# --------------------------------------------------------------------------- #
def _diff(old: str, new: str, path: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), path, path))


async def _commit(
    safety: SafetyManager, person: str, mount_id: str, client: VolumeClient, norm: str, new_text: str, op: str
) -> None:
    data = new_text.encode("utf-8")
    safety.charge_write(person, mount_id, len(data))
    await client.write_bytes_atomic(norm, data)
    safety.record_audit(person, mount_id, op, norm)


async def write_text(
    client: VolumeClient,
    safety: SafetyManager,
    person: str,
    mount_id: str,
    norm: str,
    content: str,
    *,
    overwrite: bool = False,
    create_parents: bool = True,
) -> dict[str, Any]:
    exists = await client.exists(norm)
    if exists and not overwrite:
        raise ToolError(ErrorCode.NO_CLOBBER, f"'{norm}' exists (pass overwrite=true)")
    diff = ""
    if exists:
        safety.ensure_read_before_write(person, mount_id, norm)
        old = await client.read_text(norm)
        diff = _diff(old, content, norm)
    if create_parents:
        parent = norm.rsplit("/", 1)[0] or "/"
        if parent != "/":
            await client.makedirs(parent, exist_ok=True)
    data = content.encode("utf-8")
    safety.charge_write(person, mount_id, len(data))
    await client.write_bytes_atomic(norm, data)
    safety.record_read(person, mount_id, norm)
    safety.record_audit(person, mount_id, "write", norm, f"{len(data)} bytes")
    return {"path": norm, "bytes_written": len(data), "overwritten": exists, "diff": diff}


async def append_text(
    client: VolumeClient,
    safety: SafetyManager,
    person: str,
    mount_id: str,
    norm: str,
    content: str,
    *,
    create: bool = False,
) -> dict[str, Any]:
    if not await client.exists(norm) and not create:
        raise ToolError(ErrorCode.NOT_FOUND, f"'{norm}' does not exist (pass create=true)")
    data = content.encode("utf-8")
    safety.charge_write(person, mount_id, len(data))
    await client.append_bytes(norm, data)
    safety.record_audit(person, mount_id, "append", norm, f"{len(data)} bytes")
    return {"path": norm, "bytes_appended": len(data)}


async def create_empty(
    client: VolumeClient, safety: SafetyManager, person: str, mount_id: str, norm: str, *, exist_ok: bool = False
) -> dict[str, Any]:
    if await client.exists(norm):
        if not exist_ok:
            raise ToolError(ErrorCode.NO_CLOBBER, f"'{norm}' already exists")
        return {"path": norm, "created": False}
    await client.create_empty(norm)
    safety.record_audit(person, mount_id, "create_empty", norm)
    return {"path": norm, "created": True}


_FUZZY_THRESHOLD = 0.6


def _apply_unique(text: str, old_string: str, new_string: str, *, replace_all: bool, path: str) -> str:
    count = text.count(old_string)
    if count == 0:
        raise ToolError(ErrorCode.NO_MATCH, f"old_string not found in '{path}'")
    if count > 1 and not replace_all:
        raise ToolError(ErrorCode.AMBIGUOUS_MATCH, f"old_string matches {count} sites in '{path}' (use replace_all)")
    return text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)


def _fuzzy_replace(text: str, search_block: str, replace_block: str, path: str) -> str:
    lines = text.splitlines(keepends=True)
    span = len(search_block.splitlines(keepends=True))
    best_ratio, best_index = 0.0, -1
    for start in range(0, max(len(lines) - span + 1, 0)):
        candidate = "".join(lines[start : start + span])
        ratio = difflib.SequenceMatcher(None, candidate, search_block).ratio()
        if ratio > best_ratio:
            best_ratio, best_index = ratio, start
    if best_index < 0 or best_ratio < _FUZZY_THRESHOLD:
        raise ToolError(ErrorCode.NO_MATCH, f"no fuzzy match for search_block in '{path}'")
    block = replace_block if replace_block.endswith("\n") else replace_block + "\n"
    return "".join(lines[:best_index]) + block + "".join(lines[best_index + span :])


async def edit_unique(
    client: VolumeClient,
    safety: SafetyManager,
    person: str,
    mount_id: str,
    norm: str,
    old_string: str,
    new_string: str,
    *,
    replace_all: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    safety.ensure_read_before_write(person, mount_id, norm)
    old = await client.read_text(norm)
    new = _apply_unique(old, old_string, new_string, replace_all=replace_all, path=norm)
    diff = _diff(old, new, norm)
    if not dry_run:
        await _commit(safety, person, mount_id, client, norm, new, "edit")
    return {"path": norm, "applied": not dry_run, "diff": diff}


async def multi_edit(
    client: VolumeClient,
    safety: SafetyManager,
    person: str,
    mount_id: str,
    norm: str,
    edits: list[dict[str, Any]],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    safety.ensure_read_before_write(person, mount_id, norm)
    old = await client.read_text(norm)
    new = old
    for spec in edits:
        new = _apply_unique(
            new,
            str(spec["old_string"]),
            str(spec["new_string"]),
            replace_all=bool(spec.get("replace_all", False)),
            path=norm,
        )
    diff = _diff(old, new, norm)
    if not dry_run:
        await _commit(safety, person, mount_id, client, norm, new, "multi_edit")
    return {"path": norm, "applied": not dry_run, "edits": len(edits), "diff": diff}


async def search_replace(
    client: VolumeClient,
    safety: SafetyManager,
    person: str,
    mount_id: str,
    norm: str,
    search_block: str,
    replace_block: str,
    *,
    fuzzy: bool = False,
) -> dict[str, Any]:
    safety.ensure_read_before_write(person, mount_id, norm)
    old = await client.read_text(norm)
    if search_block in old:
        new = old.replace(search_block, replace_block, 1)
    elif fuzzy:
        new = _fuzzy_replace(old, search_block, replace_block, norm)
    else:
        raise ToolError(ErrorCode.NO_MATCH, f"search_block not found in '{norm}'")
    await _commit(safety, person, mount_id, client, norm, new, "search_replace")
    return {"path": norm, "applied": True, "diff": _diff(old, new, norm)}


async def insert_at_line(
    client: VolumeClient, safety: SafetyManager, person: str, mount_id: str, norm: str, line: int, content: str
) -> dict[str, Any]:
    safety.ensure_read_before_write(person, mount_id, norm)
    old = await client.read_text(norm)
    lines = old.splitlines(keepends=True)
    position = max(0, min(line - 1, len(lines)))
    insert = content if content.endswith("\n") else content + "\n"
    new = "".join(lines[:position]) + insert + "".join(lines[position:])
    await _commit(safety, person, mount_id, client, norm, new, "insert_at_line")
    return {"path": norm, "applied": True, "line": line}


async def apply_patch(
    client: VolumeClient, safety: SafetyManager, person: str, mount_id: str, patch_text: str
) -> dict[str, Any]:
    ops = parse_patch(patch_text)
    touched: list[dict[str, str]] = []
    for op in ops:
        norm = safety.normalize_path(op.path)
        if op.kind is OpKind.ADD:
            data = op.add_content.encode("utf-8")
            safety.charge_write(person, mount_id, len(data))
            await client.write_bytes_atomic(norm, data)
            touched.append({"path": norm, "op": "add"})
        elif op.kind is OpKind.DELETE:
            safety.ensure_read_before_write(person, mount_id, norm)
            await client.remove(norm)
            touched.append({"path": norm, "op": "delete"})
        else:
            safety.ensure_read_before_write(person, mount_id, norm)
            old = await client.read_text(norm)
            new = apply_update(old, op)
            await _commit(safety, person, mount_id, client, norm, new, "apply_patch")
            if op.move_to:
                dst = safety.normalize_path(op.move_to)
                await client.rename(norm, dst)
                touched.append({"path": norm, "op": "update", "moved_to": dst})
            else:
                touched.append({"path": norm, "op": "update"})
        safety.record_audit(person, mount_id, "apply_patch", norm)
    return {"files": touched}


# --------------------------------------------------------------------------- #
# Tree-sitter code search
# --------------------------------------------------------------------------- #
async def find_definitions(client: VolumeClient, root: str, name: str, kind: str | None = None) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for path, _ in await iter_files(client, root, DEFAULT_EXCLUDES):
        if treesitter.language_for(path) is None:
            continue
        source = await client.read_bytes(path)
        for match in treesitter.find_definitions(path, source, name, kind):
            results.append({"path": match.path, "name": match.name, "kind": match.kind, "line": match.line})
    return {"definitions": results}


async def find_references(client: VolumeClient, root: str, name: str) -> dict[str, Any]:
    if not name:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "name is required")
    results: list[dict[str, Any]] = []
    for path, _ in await iter_files(client, root, DEFAULT_EXCLUDES):
        if treesitter.language_for(path) is None:
            continue
        source = await client.read_bytes(path)
        for match in treesitter.find_references(path, source, name):
            results.append({"path": match.path, "line": match.line, "kind": match.kind})
    return {"references": results}
