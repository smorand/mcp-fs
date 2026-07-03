"""Data-plane HTTP API (/api/fs): human/app file operations over the volume.

This is the bytes plane, distinct from the agent plane (the MCP ``fs.*`` tools):
it lets a UI or a script upload, download, browse and organize files directly
through the same :class:`VolumeClient` and the same project ACL. Every endpoint
resolves an identity (a session cookie or a Bearer JWT, provided by the
``identity`` dependency), then checks project membership.
"""

from __future__ import annotations

import io
import mimetypes
import posixpath
import zipfile
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from mcp_fs import fs_ops
from mcp_fs.models import ErrorCode, ToolError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mcp_fs.context import ToolContext
    from mcp_fs.volume import VolumeClient

_HTTP_FOR_CODE = {
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.PROJECT_NOT_FOUND: 404,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.PATH_OUT_OF_BOUNDS: 400,
    ErrorCode.NO_CLOBBER: 409,
    ErrorCode.INVALID_ARGUMENT: 400,
}


def _http(exc: ToolError) -> HTTPException:
    return HTTPException(status_code=_HTTP_FOR_CODE.get(exc.code, 400), detail=f"{exc.code.value}: {exc.message}")


class MkdirBody(BaseModel):
    path: str


class DeleteBody(BaseModel):
    path: str


class MoveBody(BaseModel):
    source: str
    destination: str


class CopyBody(BaseModel):
    source: str
    destination: str
    overwrite: bool = False
    recursive: bool = False


class ExtractBody(BaseModel):
    path: str
    max_chars: int = 200_000
    preview_chars: int = 4_000
    ocr: bool = True
    refresh: bool = False


class WriteDocxBody(BaseModel):
    path: str
    markdown: str
    title: str | None = None
    overwrite: bool = False


def build_dataplane_router(ctx: ToolContext, identity: Callable[..., Awaitable[str]]) -> APIRouter:
    """Return the ``/api/fs`` router; ``identity`` yields the caller's email."""
    router = APIRouter(prefix="/api/fs", tags=["fs"])

    def _norm(path: str) -> str:
        try:
            return ctx.norm(path)
        except ToolError as exc:
            raise _http(exc) from exc

    async def _client(mount_id: str, person: str) -> VolumeClient:
        try:
            await ctx.store.require_member(mount_id, person)
        except ToolError as exc:
            raise _http(exc) from exc
        return await ctx.manager.get_client(mount_id)

    async def _guard(coro: Awaitable[dict[str, Any]]) -> dict[str, Any]:
        """Await an fs_ops call, mapping its ToolError to the right HTTP status."""
        try:
            return await coro
        except ToolError as exc:
            raise _http(exc) from exc

    @router.get("/roots")
    async def roots(person: str = Depends(identity)) -> dict[str, Any]:
        projects = await ctx.store.list_projects_for(person)
        return {"person": person, "roots": [{"mount_id": p.id, "owner": p.owner} for p in projects]}

    @router.get("/{mount_id}/list")
    async def list_dir(mount_id: str, path: str = "/", person: str = Depends(identity)) -> dict[str, Any]:
        client = await _client(mount_id, person)
        norm = _norm(path)
        entries = [
            {"name": name, "kind": kind, "size": size, "mtime": mtime}
            for name, kind, size, mtime in await client.listdir(norm)
        ]
        entries.sort(key=lambda item: (item["kind"] != "dir", str(item["name"]).lower()))
        return {"path": norm, "entries": entries}

    @router.post("/{mount_id}/mkdir")
    async def mkdir(mount_id: str, body: MkdirBody, person: str = Depends(identity)) -> dict[str, Any]:
        client = await _client(mount_id, person)
        norm = _norm(body.path)
        await client.makedirs(norm, exist_ok=True)
        return {"path": norm, "created": True}

    @router.post("/{mount_id}/delete")
    async def delete(mount_id: str, body: DeleteBody, person: str = Depends(identity)) -> dict[str, Any]:
        client = await _client(mount_id, person)
        norm = _norm(body.path)
        if not await client.exists(norm):
            raise HTTPException(status_code=404, detail=f"not found: {norm}")
        if await client.is_dir(norm):
            await client.rmtree(norm)
        else:
            await client.remove(norm)
        return {"path": norm, "deleted": True}

    @router.post("/{mount_id}/move")
    async def move(mount_id: str, body: MoveBody, person: str = Depends(identity)) -> dict[str, Any]:
        client = await _client(mount_id, person)
        src, dst = _norm(body.source), _norm(body.destination)
        if not await client.exists(src):
            raise HTTPException(status_code=404, detail=f"not found: {src}")
        await client.rename(src, dst)
        return {"source": src, "destination": dst}

    @router.post("/{mount_id}/upload")
    async def upload(
        mount_id: str,
        files: Annotated[list[UploadFile], File()],
        directory: Annotated[str, Form()] = "/",
        paths: Annotated[list[str], Form()] = [],  # noqa: B006 - FastAPI form default
        person: str = Depends(identity),
    ) -> dict[str, Any]:
        """Upload one file (flat), or a whole folder when per-file relative ``paths`` are given."""
        client = await _client(mount_id, person)
        base = _norm(directory)
        written: list[str] = []
        for index, upload_file in enumerate(files):
            rel = paths[index] if index < len(paths) and paths[index] else (upload_file.filename or "file")
            dest = _norm(posixpath.join(base, rel))
            data = await upload_file.read()
            await client.write_bytes_atomic(dest, data)
            written.append(dest)
        return {"written": written, "count": len(written)}

    @router.get("/{mount_id}/download")
    async def download(mount_id: str, path: str, person: str = Depends(identity)) -> Response:
        client = await _client(mount_id, person)
        norm = _norm(path)
        if not await client.is_file(norm):
            raise HTTPException(status_code=404, detail=f"not a file: {norm}")
        data = await client.read_bytes(norm)
        mime, _ = mimetypes.guess_type(norm)
        name = posixpath.basename(norm)
        return Response(
            content=data,
            media_type=mime or "application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @router.get("/{mount_id}/download-zip")
    async def download_zip(mount_id: str, path: str = "/", person: str = Depends(identity)) -> StreamingResponse:
        client = await _client(mount_id, person)
        root = _norm(path)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for dirpath, _dirs, filenames in await client.walk(root):
                for name in filenames:
                    full = f"{dirpath.rstrip('/')}/{name}"
                    arcname = full[len(root) :].lstrip("/") or name
                    archive.writestr(arcname, await client.read_bytes(full))
        buffer.seek(0)
        label = posixpath.basename(root.rstrip("/")) or mount_id
        return StreamingResponse(
            iter([buffer.getvalue()]),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{label}.zip"'},
        )

    # -- parity with the MCP fs.* tools (same fs_ops under the hood) -----------
    @router.get("/{mount_id}/read")
    async def read(
        mount_id: str,
        path: str,
        offset_lines: int = 0,
        limit_lines: int = 2000,
        line_numbered: bool = True,
        person: str = Depends(identity),
    ) -> dict[str, Any]:
        client = await _client(mount_id, person)
        return await _guard(
            fs_ops.read_window(
                client,
                ctx.safety,
                person,
                mount_id,
                _norm(path),
                offset_lines=offset_lines,
                limit_lines=limit_lines,
                line_numbered=line_numbered,
            )
        )

    @router.get("/{mount_id}/read-bytes")
    async def read_bytes(
        mount_id: str, path: str, offset: int = 0, length: int = 65536, person: str = Depends(identity)
    ) -> dict[str, Any]:
        client = await _client(mount_id, person)
        return await _guard(
            fs_ops.read_bytes_b64(client, ctx.safety, person, mount_id, _norm(path), offset=offset, length=length)
        )

    @router.get("/{mount_id}/stat")
    async def stat(mount_id: str, path: str, person: str = Depends(identity)) -> dict[str, Any]:
        client = await _client(mount_id, person)
        return await _guard(fs_ops.stat_info(client, _norm(path)))

    @router.get("/{mount_id}/exists")
    async def exists(mount_id: str, path: str, person: str = Depends(identity)) -> dict[str, Any]:
        client = await _client(mount_id, person)
        return await _guard(fs_ops.exists_info(client, _norm(path)))

    @router.get("/{mount_id}/hash")
    async def hash_path(
        mount_id: str, path: str, algo: str = "sha256", person: str = Depends(identity)
    ) -> dict[str, Any]:
        client = await _client(mount_id, person)
        return await _guard(fs_ops.hash_file(client, _norm(path), algo))

    @router.get("/{mount_id}/count-lines")
    async def count_lines(mount_id: str, path: str, person: str = Depends(identity)) -> dict[str, Any]:
        client = await _client(mount_id, person)
        return await _guard(fs_ops.count_lines(client, _norm(path)))

    @router.get("/{mount_id}/glob")
    async def glob(
        mount_id: str,
        pattern: str,
        root: str = "/",
        exclude_patterns: Annotated[list[str], Query()] = [],  # noqa: B006 - FastAPI query default
        person: str = Depends(identity),
    ) -> dict[str, Any]:
        client = await _client(mount_id, person)
        return await _guard(fs_ops.glob_files(client, _norm(root), pattern, extra_excludes=tuple(exclude_patterns)))

    @router.get("/{mount_id}/grep")
    async def grep(
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
        person: str = Depends(identity),
    ) -> dict[str, Any]:
        client = await _client(mount_id, person)
        return await _guard(
            fs_ops.grep_files(
                client,
                _norm(root),
                pattern,
                include_glob=include_glob,
                exclude_glob=exclude_glob,
                regex=regex,
                case_sensitive=case_sensitive,
                output_mode=output_mode,
                context_lines=context_lines,
                max_matches=max_matches,
            )
        )

    @router.post("/{mount_id}/copy")
    async def copy(mount_id: str, body: CopyBody, person: str = Depends(identity)) -> dict[str, Any]:
        client = await _client(mount_id, person)
        return await _guard(
            fs_ops.copy_path(
                client,
                ctx.safety,
                person,
                mount_id,
                _norm(body.source),
                _norm(body.destination),
                overwrite=body.overwrite,
                recursive=body.recursive,
            )
        )

    @router.post("/{mount_id}/extract-text")
    async def extract_text(mount_id: str, body: ExtractBody, person: str = Depends(identity)) -> dict[str, Any]:
        client = await _client(mount_id, person)
        return await _guard(
            fs_ops.extract_document(
                client,
                ctx.safety,
                person,
                mount_id,
                _norm(body.path),
                max_chars=body.max_chars,
                preview_chars=body.preview_chars,
                ocr=body.ocr,
                refresh=body.refresh,
            )
        )

    @router.post("/{mount_id}/write-docx")
    async def write_docx(mount_id: str, body: WriteDocxBody, person: str = Depends(identity)) -> dict[str, Any]:
        client = await _client(mount_id, person)
        return await _guard(
            fs_ops.write_docx(
                client,
                ctx.safety,
                person,
                mount_id,
                _norm(body.path),
                body.markdown,
                title=body.title,
                overwrite=body.overwrite,
            )
        )

    @router.get("/{mount_id}/audit-log")
    async def audit_log(
        mount_id: str, since: float | None = None, limit: int = 20, person: str = Depends(identity)
    ) -> dict[str, Any]:
        await _client(mount_id, person)
        entries = list(ctx.safety.session(person, mount_id).audit)
        if since is not None:
            entries = [entry for entry in entries if entry.timestamp >= since]
        return {
            "entries": [
                {"timestamp": e.timestamp, "op": e.op, "path": e.path, "detail": e.detail} for e in entries[-limit:]
            ]
        }

    return router
