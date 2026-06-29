"""Storage backend contracts: MetaBackend, BlobBackend, AdminBackend.

These Protocols are the seams that make the storage layer pluggable. The tool
surface only ever sees :class:`~mcp_fs.volume.VolumeClient` (composed from a
:class:`MetaBackend` and a :class:`BlobBackend`) and an :class:`AdminBackend`.
Adding a new backend (PostgreSQL metadata, local-filesystem blobs, ...) means
implementing one of these Protocols and registering it in ``backends.py`` —
nothing in ``fs_tools/`` or ``admin_tools.py`` changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from mcp_fs.models import Member, Project


@dataclass(frozen=True, slots=True)
class NodeRow:
    """One entry in a volume's directory tree (file or directory)."""

    path: str
    parent: str | None
    name: str
    kind: str  # "dir" | "file"
    size: int
    mode: int  # full POSIX mode including type bits (S_IFDIR / S_IFREG)
    mtime: float
    ctime: float
    atime: float
    sha256: str | None  # content hash for files, None for directories / empty files


class MetaBackend(Protocol):
    """A volume's metadata tree plus content-addressed blob reference counts."""

    async def get(self, path: str) -> NodeRow | None: ...

    async def list_children(self, parent: str) -> list[NodeRow]: ...

    async def subtree(self, root: str) -> list[NodeRow]: ...

    async def put_file(self, path: str, sha256: str | None, size: int, *, mode: int) -> str | None:
        """Upsert a file node; return a sha whose refcount hit 0 (caller GCs it)."""

    async def delete_file(self, path: str) -> str | None:
        """Remove a file node; return a sha whose refcount hit 0, else None."""

    async def remove_subtree(self, path: str) -> list[str]:
        """Remove a node and all descendants; return shas whose refcount hit 0."""

    async def mkdirs(self, path: str, *, exist_ok: bool = True) -> None: ...

    async def mkdir(self, path: str) -> None: ...

    async def rmdir(self, path: str) -> None: ...

    async def rename(self, src: str, dst: str) -> None: ...

    def close(self) -> None: ...


class BlobBackend(Protocol):
    """A content-addressed byte store, keyed by sha256, scoped to one volume."""

    async def put(self, sha256: str, data: bytes) -> None: ...

    async def get(self, sha256: str, offset: int = 0, length: int | None = None) -> bytes: ...

    async def exists(self, sha256: str) -> bool: ...

    async def delete(self, sha256: str) -> None: ...

    async def ensure_bucket(self) -> None: ...

    async def remove_bucket(self) -> None: ...


class AdminBackend(Protocol):
    """ACL registry of projects and their members."""

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def create_project(self, project_id: str, owner: str) -> Project: ...

    async def delete_project(self, project_id: str) -> None: ...

    async def add_member(self, project_id: str, person: str, added_by: str) -> Member: ...

    async def remove_member(self, project_id: str, person: str) -> None: ...

    async def get_project(self, project_id: str) -> Project | None: ...

    async def list_projects_for(self, person: str) -> list[Project]: ...

    async def list_all_projects(self) -> list[Project]: ...

    async def list_all_persons(self) -> list[str]: ...

    async def list_members(self, project_id: str) -> list[Member]: ...

    async def is_member(self, project_id: str, person: str) -> bool: ...

    async def require_member(self, project_id: str, person: str) -> None: ...

    async def require_owner(self, project_id: str, person: str) -> Project: ...
