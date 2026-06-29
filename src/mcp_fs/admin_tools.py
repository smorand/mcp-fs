"""Management tools: project lifecycle and membership.

Authority model:
- **Platform admin** (``auth.admins`` in config): create projects (designating any owner),
  list every project/user, and act on any project (override per-project ownership).
- **Project owner**: manage members and delete their own project.
- **Member**: read membership of their project; access its files via ``fs.*``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from mcp_fs.identity import current_person
from mcp_fs.models import ErrorCode, ToolError

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from mcp_fs.context import ToolContext

_READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, destructiveHint=False)
_MUTATING = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)
_PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$")


def _validate_project_id(project_id: str) -> None:
    if not _PROJECT_ID.match(project_id):
        raise ToolError(
            ErrorCode.INVALID_ARGUMENT,
            "project_id must be 3-32 chars, lowercase letters/digits/hyphens, alphanumeric bounds",
        )


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    """Register the admin-family tools."""

    @mcp.tool(
        name="admin.create_project",
        annotations=_MUTATING,
        description="Create a project for a designated owner and provision its volume (platform admin only).",
    )
    async def admin_create_project(project_id: str, owner: str) -> dict[str, Any]:
        caller = current_person()
        ctx.require_admin(caller)
        _validate_project_id(project_id)
        if not owner.strip():
            raise ToolError(ErrorCode.INVALID_ARGUMENT, "owner is required")
        project = await ctx.store.create_project(project_id, owner)
        try:
            await ctx.manager.provision_volume(project_id)
        except Exception:
            await ctx.store.delete_project(project_id)
            raise
        return {"project_id": project.id, "owner": project.owner, "created_at": project.created_at}

    @mcp.tool(
        name="admin.delete_project",
        annotations=_DESTRUCTIVE,
        description="Delete a project and recursively tear down its volume (owner or platform admin).",
    )
    async def admin_delete_project(project_id: str) -> dict[str, Any]:
        caller = current_person()
        await ctx.require_owner_or_admin(project_id, caller)
        await ctx.manager.deprovision_volume(project_id)
        await ctx.store.delete_project(project_id)
        return {"project_id": project_id, "deleted": True}

    @mcp.tool(name="admin.list_projects", annotations=_READ_ONLY, description="List projects the caller can access.")
    async def admin_list_projects() -> dict[str, Any]:
        person = current_person()
        projects = await ctx.store.list_projects_for(person)
        return {
            "projects": [
                {"project_id": p.id, "owner": p.owner, "created_at": p.created_at, "is_owner": p.owner == person}
                for p in projects
            ]
        }

    @mcp.tool(
        name="admin.list_all_projects", annotations=_READ_ONLY, description="List every project (platform admin only)."
    )
    async def admin_list_all_projects() -> dict[str, Any]:
        caller = current_person()
        ctx.require_admin(caller)
        projects = await ctx.store.list_all_projects()
        return {"projects": [{"project_id": p.id, "owner": p.owner, "created_at": p.created_at} for p in projects]}

    @mcp.tool(
        name="admin.list_users",
        annotations=_READ_ONLY,
        description="List every known person and platform admins (platform admin only).",
    )
    async def admin_list_users() -> dict[str, Any]:
        caller = current_person()
        ctx.require_admin(caller)
        persons = set(await ctx.store.list_all_persons()) | set(ctx.config.auth.admins)
        admins = set(ctx.config.auth.admins)
        return {"users": [{"person": p, "is_admin": p in admins} for p in sorted(persons)]}

    @mcp.tool(
        name="admin.add_member",
        annotations=_MUTATING,
        description="Add a person to a project (owner or platform admin).",
    )
    async def admin_add_member(project_id: str, person: str) -> dict[str, Any]:
        caller = current_person()
        await ctx.require_owner_or_admin(project_id, caller)
        member = await ctx.store.add_member(project_id, person, added_by=caller)
        return {"project_id": project_id, "person": member.person, "role": member.role.value}

    @mcp.tool(
        name="admin.remove_member",
        annotations=_DESTRUCTIVE,
        description="Remove a person from a project (owner or platform admin).",
    )
    async def admin_remove_member(project_id: str, person: str) -> dict[str, Any]:
        caller = current_person()
        await ctx.require_owner_or_admin(project_id, caller)
        await ctx.store.remove_member(project_id, person)
        return {"project_id": project_id, "person": person, "removed": True}

    @mcp.tool(
        name="admin.list_members",
        annotations=_READ_ONLY,
        description="List members of a project (member or platform admin).",
    )
    async def admin_list_members(project_id: str) -> dict[str, Any]:
        caller = current_person()
        if not ctx.is_admin(caller):
            await ctx.store.require_member(project_id, caller)
        elif await ctx.store.get_project(project_id) is None:
            raise ToolError(ErrorCode.PROJECT_NOT_FOUND, f"project '{project_id}' not found")
        members = await ctx.store.list_members(project_id)
        return {
            "project_id": project_id,
            "members": [{"person": m.person, "role": m.role.value, "added_by": m.added_by} for m in members],
        }
