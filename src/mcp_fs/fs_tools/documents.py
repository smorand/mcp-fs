"""Document family: extract_text (read any doc as Markdown) and write_docx.

These let an agent read binary office/PDF/image files stored in a volume and
emit a Word document from generated Markdown, without the agent handling bytes.
Extraction and rendering are CPU bound, so they run off the event loop.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from mcp_fs.docx_writer import markdown_to_docx
from mcp_fs.extract import UnsupportedDocument, extract
from mcp_fs.models import ErrorCode, ToolError

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from mcp_fs.context import ToolContext

_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)
# extract_text materializes a derived .md cache: it writes, but never destroys user data.
_MATERIALIZE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)

# Formats that get a Markdown companion (transformed on extraction); already-text
# formats (.md/.txt/.json/...) are returned inline with no companion.
_MD_COMPANION_EXTS = frozenset(
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


def _companion_path(norm: str) -> str:
    """Return the ``.md`` companion path for a source (``report.pdf`` -> ``report.md``)."""
    dot = norm.rfind(".")
    stem = norm[:dot] if dot > norm.rfind("/") else norm
    return f"{stem}.md"


def _payload(
    source: str, md_path: str | None, fmt: str, text: str, preview_chars: int, *, cached: bool
) -> dict[str, Any]:
    """Compact extract_text result: a preview plus where to read the full Markdown."""
    return {
        "path": source,
        "md_path": md_path,
        "format": fmt,
        "chars": len(text),
        "cached": cached,
        "preview": text[:preview_chars],
    }


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    """Register the document-family tools."""

    @mcp.tool(
        name="fs.extract_text",
        annotations=_MATERIALIZE,
        description=(
            "Extract a document to Markdown and store it as a companion .md next to the source "
            "(report.pdf -> report.md), reusing it if already up to date. Returns md_path + a preview; "
            "read the .md with fs.read for the full content. Handles PDF, DOCX, PPTX, XLSX, HTML, CSV, "
            "images (OCR) and text; audio/video unsupported."
        ),
    )
    async def fs_extract_text(
        mount_id: str,
        path: str,
        max_chars: int = 200_000,
        preview_chars: int = 4_000,
        ocr: bool = True,
        refresh: bool = False,
    ) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        norm = ctx.norm(path)
        if not await client.is_file(norm):
            raise ToolError(ErrorCode.NOT_FOUND, f"not a file: {norm}")
        ext = f".{norm.rsplit('.', 1)[-1].lower()}" if "." in norm else ""
        md_path = _companion_path(norm) if ext in _MD_COMPANION_EXTS else None

        # Reuse an up-to-date companion instead of re-extracting.
        if (
            md_path
            and not refresh
            and await client.exists(md_path)
            and (await client.stat(md_path)).st_mtime >= (await client.stat(norm)).st_mtime
        ):
            text = await client.read_text(md_path)
            return _payload(norm, md_path, "md", text, preview_chars, cached=True)

        data = await client.read_bytes(norm)
        try:
            result = await asyncio.to_thread(extract, data, norm, max_chars=max_chars, ocr=ocr)
        except UnsupportedDocument as exc:
            raise ToolError(ErrorCode.INVALID_ARGUMENT, str(exc)) from exc
        except Exception as exc:
            raise ToolError(ErrorCode.INVALID_ARGUMENT, f"could not extract {norm}: {exc}") from exc

        # Persist the companion (only when extraction produced text).
        if md_path and result.text.strip():
            md_bytes = result.text.encode("utf-8")
            ctx.safety.charge_write(person, mount_id, len(md_bytes))
            await client.write_bytes_atomic(md_path, md_bytes)
            ctx.safety.record_read(person, mount_id, md_path)
            ctx.safety.record_audit(person, mount_id, "extract_text", md_path, f"{len(md_bytes)} bytes")
        else:
            md_path = None
        payload = _payload(norm, md_path, result.fmt, result.text, preview_chars, cached=False)
        payload["truncated"] = result.truncated
        payload["meta"] = result.meta
        payload["note"] = result.note
        return payload

    @mcp.tool(
        name="fs.write_docx",
        annotations=_DESTRUCTIVE,
        description="Render Markdown into a .docx Word document and write it to the volume.",
    )
    async def fs_write_docx(
        mount_id: str,
        path: str,
        markdown: str,
        title: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        person, client = await ctx.client(mount_id)
        norm = ctx.norm(path)
        if not norm.lower().endswith(".docx"):
            raise ToolError(ErrorCode.INVALID_ARGUMENT, "path must end with .docx")
        exists = await client.exists(norm)
        if exists and not overwrite:
            raise ToolError(ErrorCode.NO_CLOBBER, f"'{norm}' exists (pass overwrite=true)")
        if exists:
            ctx.safety.ensure_read_before_write(person, mount_id, norm)
        data = await asyncio.to_thread(markdown_to_docx, markdown, title=title)
        parent = norm.rsplit("/", 1)[0] or "/"
        if parent != "/":
            await client.makedirs(parent, exist_ok=True)
        ctx.safety.charge_write(person, mount_id, len(data))
        await client.write_bytes_atomic(norm, data)
        ctx.safety.record_read(person, mount_id, norm)
        ctx.safety.record_audit(person, mount_id, "write_docx", norm, f"{len(data)} bytes")
        return {"path": norm, "bytes_written": len(data), "overwritten": exists}
