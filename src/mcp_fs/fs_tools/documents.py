"""Document family: extract_text and write_docx (thin adapters over fs_ops)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from mcp_fs import fs_ops

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from mcp_fs.context import ToolContext

# extract_text materializes a derived .md cache: it writes, but never destroys user data.
_MATERIALIZE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)


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
        return await fs_ops.extract_document(
            client,
            ctx.safety,
            person,
            mount_id,
            ctx.norm(path),
            max_chars=max_chars,
            preview_chars=preview_chars,
            ocr=ocr,
            refresh=refresh,
        )

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
        return await fs_ops.write_docx(
            client, ctx.safety, person, mount_id, ctx.norm(path), markdown, title=title, overwrite=overwrite
        )
