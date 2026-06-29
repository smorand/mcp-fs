"""StoreManager: cache volume clients and provision / tear down volumes.

A volume is a metadata database (one SQLite file) plus a blob bucket. Provisioning
creates both; deprovisioning drops the cached client, empties and removes the
bucket, then deletes the database file (and its WAL sidecars).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from mcp_fs.backends import build_blob_store, build_meta_store
from mcp_fs.config import volume_meta_path
from mcp_fs.volume import VolumeClient

if TYPE_CHECKING:
    from mcp_fs.models import ServerConfig

logger = logging.getLogger(__name__)


class StoreManager:
    """Caches one :class:`VolumeClient` per project and provisions new volumes."""

    __slots__ = ("_clients", "_config", "_lock")

    def __init__(self, config: ServerConfig) -> None:
        self._config = config
        self._clients: dict[str, VolumeClient] = {}
        self._lock = asyncio.Lock()

    async def get_client(self, project_id: str) -> VolumeClient:
        """Return a cached client for ``project_id``, opening one on first use."""
        async with self._lock:
            client = self._clients.get(project_id)
            if client is None:
                meta = build_meta_store(self._config, project_id)
                blob = build_blob_store(self._config, project_id)
                client = VolumeClient(project_id, meta, blob)
                self._clients[project_id] = client
            return client

    def forget(self, project_id: str) -> None:
        """Drop and close a cached client (e.g. after the project is deleted)."""
        client = self._clients.pop(project_id, None)
        if client is not None:
            client.close()

    async def provision_volume(self, project_id: str) -> None:
        """Create the metadata database and the blob bucket (idempotent)."""
        logger.info("Provisioning volume '%s'", project_id)
        meta = build_meta_store(self._config, project_id)
        meta.close()
        blob = build_blob_store(self._config, project_id)
        await blob.ensure_bucket()

    async def deprovision_volume(self, project_id: str) -> None:
        """Tear down a volume: drop the client, remove the bucket, delete the files."""
        logger.info("Deprovisioning volume '%s'", project_id)
        self.forget(project_id)
        blob = build_blob_store(self._config, project_id)
        await blob.remove_bucket()
        base = volume_meta_path(self._config, project_id)
        for suffix in ("", "-wal", "-shm"):
            Path(f"{base}{suffix}").unlink(missing_ok=True)
