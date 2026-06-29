"""Shared tool context: the service handles every tool closure captures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from mcp_fs.identity import current_person
from mcp_fs.models import ErrorCode, ToolError

if TYPE_CHECKING:
    from mcp_fs.manager import StoreManager
    from mcp_fs.models import ServerConfig
    from mcp_fs.protocols import AdminBackend
    from mcp_fs.safety import SafetyManager
    from mcp_fs.volume import VolumeClient


@dataclass(slots=True)
class ToolContext:
    """Bundle of services injected into every MCP tool."""

    config: ServerConfig
    store: AdminBackend
    manager: StoreManager
    safety: SafetyManager

    async def authorize(self, mount_id: str) -> str:
        """Return the caller if they are a member of ``mount_id``, else raise."""
        person = current_person()
        await self.store.require_member(mount_id, person)
        return person

    def is_admin(self, person: str) -> bool:
        """Return whether ``person`` is a configured platform admin."""
        return person in self.config.auth.admins

    def require_admin(self, person: str) -> None:
        """Authorize ``person`` as a platform admin or raise ``ERR_FORBIDDEN``."""
        if not self.is_admin(person):
            raise ToolError(ErrorCode.FORBIDDEN, f"'{person}' is not a platform admin")

    async def require_owner_or_admin(self, mount_id: str, person: str) -> None:
        """Authorize ``person`` as project owner OR platform admin, else raise."""
        if self.is_admin(person):
            if await self.store.get_project(mount_id) is None:
                raise ToolError(ErrorCode.PROJECT_NOT_FOUND, f"project '{mount_id}' not found")
            return
        await self.store.require_owner(mount_id, person)

    async def client(self, mount_id: str) -> tuple[str, VolumeClient]:
        """Authorize the caller and return ``(person, volume_client)``."""
        person = await self.authorize(mount_id)
        return person, await self.manager.get_client(mount_id)

    def norm(self, path: str) -> str:
        """Normalize an in-volume path (delegates to the safety manager)."""
        return self.safety.normalize_path(path)
