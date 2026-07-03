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

_READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, destructiveHint=False)
_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    """Register the document-family tools."""

    @mcp.tool(
        name="fs.extract_text",
        annotations=_READ_ONLY,
        description=(
            "Extract Markdown/plain text from a stored document "
            "(PDF, DOCX, PPTX, XLSX, HTML, CSV, images with OCR, text). Audio/video unsupported."
        ),
    )
    async def fs_extract_text(
        mount_id: str,
        path: str,
        max_chars: int = 200_000,
        ocr: bool = True,
    ) -> dict[str, Any]:
        _, client = await ctx.client(mount_id)
        norm = ctx.norm(path)
        if not await client.is_file(norm):
            raise ToolError(ErrorCode.NOT_FOUND, f"not a file: {norm}")
        data = await client.read_bytes(norm)
        try:
            result = await asyncio.to_thread(extract, data, norm, max_chars=max_chars, ocr=ocr)
        except UnsupportedDocument as exc:
            raise ToolError(ErrorCode.INVALID_ARGUMENT, str(exc)) from exc
        except Exception as exc:
            raise ToolError(ErrorCode.INVALID_ARGUMENT, f"could not extract {norm}: {exc}") from exc
        return result.as_dict(norm)

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
