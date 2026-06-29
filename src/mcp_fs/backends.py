"""Backend factories: map the config ``backend`` discriminator to an implementation.

This is the single place that knows which concrete class implements each Protocol.
To add a backend (e.g. a PostgreSQL ``MetaBackend`` or a local-filesystem
``BlobBackend``), implement the Protocol and add a branch here. Nothing else in
the codebase needs to change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_fs.config import admin_db_path, volume_bucket, volume_meta_path
from mcp_fs.minio_blob import MinioBlobStore
from mcp_fs.models import ErrorCode, ToolError
from mcp_fs.sqlite_admin import SqliteAdminStore
from mcp_fs.sqlite_meta import SqliteMetaStore

if TYPE_CHECKING:
    from mcp_fs.models import ServerConfig
    from mcp_fs.protocols import AdminBackend, BlobBackend, MetaBackend


def build_admin_store(config: ServerConfig) -> AdminBackend:
    """Return the configured ACL backend."""
    backend = config.infra.admin.backend
    if backend == "sqlite":
        return SqliteAdminStore(admin_db_path(config))
    raise ToolError(ErrorCode.NOT_SUPPORTED, f"admin backend '{backend}' not supported")


def build_meta_store(config: ServerConfig, project_id: str) -> MetaBackend:
    """Return the configured metadata backend for ``project_id``."""
    backend = config.infra.meta.backend
    if backend == "sqlite":
        return SqliteMetaStore(volume_meta_path(config, project_id))
    raise ToolError(ErrorCode.NOT_SUPPORTED, f"meta backend '{backend}' not supported")


def build_blob_store(config: ServerConfig, project_id: str) -> BlobBackend:
    """Return the configured blob backend for ``project_id``."""
    backend = config.infra.blob.backend
    if backend in ("minio", "s3"):
        return MinioBlobStore(config.infra.blob, volume_bucket(config, project_id))
    raise ToolError(ErrorCode.NOT_SUPPORTED, f"blob backend '{backend}' not supported")
