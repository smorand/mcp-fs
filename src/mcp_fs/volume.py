"""VolumeClient: the async filesystem facade the tool surface talks to.

It composes a :class:`~mcp_fs.protocols.MetaBackend` (the directory tree and blob
reference counts) with a :class:`~mcp_fs.protocols.BlobBackend` (content-addressed
bytes). Files are stored by content sha256: writing puts the blob first, then the
metadata; deleting drops the metadata, then the blob once its refcount hits zero.
This is exactly the interface the JuiceFS-backed ``VolumeClient`` exposed, so the
``fs_tools`` are reused unchanged.
"""

from __future__ import annotations

import hashlib
import os
import stat as stat_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp_fs.protocols import BlobBackend, MetaBackend, NodeRow

_FILE_MODE = stat_module.S_IFREG | 0o644
_UID = 1000
_GID = 1000


def _sha256(data: bytes) -> str | None:
    """Return the content hash, or ``None`` for empty content (no blob stored)."""
    return hashlib.sha256(data).hexdigest() if data else None


class VolumeClient:
    """Async filesystem over a metadata store and a content-addressed blob store."""

    __slots__ = ("_blob", "_meta", "project_id")

    def __init__(self, project_id: str, meta: MetaBackend, blob: BlobBackend) -> None:
        self.project_id = project_id
        self._meta = meta
        self._blob = blob

    def close(self) -> None:
        self._meta.close()

    # -- existence / metadata ------------------------------------------------
    async def exists(self, path: str) -> bool:
        return await self._meta.get(path) is not None

    async def is_dir(self, path: str) -> bool:
        node = await self._meta.get(path)
        return node is not None and node.kind == "dir"

    async def is_file(self, path: str) -> bool:
        node = await self._meta.get(path)
        return node is not None and node.kind == "file"

    async def stat(self, path: str) -> os.stat_result:
        node = await self._meta.get(path)
        if node is None:
            raise FileNotFoundError(path)
        return os.stat_result(
            (node.mode, 0, 0, 1, _UID, _GID, node.size, int(node.atime), int(node.mtime), int(node.ctime))
        )

    async def listdir(self, path: str) -> list[tuple[str, str, int, float]]:
        children = await self._meta.list_children(path)
        return [(node.name, node.kind, node.size, node.mtime) for node in children]

    async def walk(self, top: str) -> list[tuple[str, list[str], list[str]]]:
        nodes = await self._meta.subtree(top)
        children: dict[str, list[NodeRow]] = {}
        for node in nodes:
            if node.parent is not None:
                children.setdefault(node.parent, []).append(node)
        result: list[tuple[str, list[str], list[str]]] = []
        for node in nodes:
            if node.kind != "dir":
                continue
            kids = children.get(node.path, [])
            dirs = [kid.name for kid in kids if kid.kind == "dir"]
            files = [kid.name for kid in kids if kid.kind == "file"]
            result.append((node.path, dirs, files))
        return result

    # -- reads ---------------------------------------------------------------
    async def read_bytes(self, path: str, offset: int = 0, length: int | None = None) -> bytes:
        node = await self._meta.get(path)
        if node is None or node.kind != "file":
            raise FileNotFoundError(path)
        if node.sha256 is None:
            return b""
        return await self._blob.get(node.sha256, offset, length)

    async def read_text(self, path: str) -> str:
        return (await self.read_bytes(path)).decode("utf-8", errors="replace")

    # -- writes --------------------------------------------------------------
    async def write_bytes_atomic(self, path: str, data: bytes) -> None:
        sha = _sha256(data)
        if sha is not None:
            await self._blob.put(sha, data)
        gc_sha = await self._meta.put_file(path, sha, len(data), mode=_FILE_MODE)
        if gc_sha is not None and gc_sha != sha:
            await self._blob.delete(gc_sha)

    async def append_bytes(self, path: str, data: bytes) -> None:
        existing = await self.read_bytes(path) if await self.exists(path) else b""
        await self.write_bytes_atomic(path, existing + data)

    async def create_empty(self, path: str) -> None:
        await self.write_bytes_atomic(path, b"")

    # -- directory & lifecycle ----------------------------------------------
    async def makedirs(self, path: str, *, exist_ok: bool = True) -> None:
        await self._meta.mkdirs(path, exist_ok=exist_ok)

    async def mkdir(self, path: str) -> None:
        await self._meta.mkdir(path)

    async def remove(self, path: str) -> None:
        gc_sha = await self._meta.delete_file(path)
        if gc_sha is not None:
            await self._blob.delete(gc_sha)

    async def rmdir(self, path: str) -> None:
        await self._meta.rmdir(path)

    async def rmtree(self, path: str) -> None:
        for sha in await self._meta.remove_subtree(path):
            await self._blob.delete(sha)

    async def rename(self, src: str, dst: str) -> None:
        if await self.exists(dst):
            if await self.is_dir(dst):
                await self.rmtree(dst)
            else:
                await self.remove(dst)
        await self._meta.rename(src, dst)
