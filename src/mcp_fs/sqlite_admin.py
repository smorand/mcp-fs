"""SQLite ACL backend: projects and per-person memberships.

Authorization is by *person*, not by token: every project has one owner and a
set of members. Mirrors the semantics of the PostgreSQL store from mcp-juicefs,
but with zero external services (one local SQLite file).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from mcp_fs.models import ErrorCode, Member, Project, Role, ToolError, normalize_identity
from mcp_fs.sqlite_db import SqliteDb

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS project (
    id         TEXT PRIMARY KEY,
    owner      TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_member (
    project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    person     TEXT NOT NULL,
    role       TEXT NOT NULL,
    added_by   TEXT NOT NULL,
    added_at   TEXT NOT NULL,
    PRIMARY KEY (project_id, person)
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _to_project(record: sqlite3.Row) -> Project:
    return Project(id=record["id"], owner=record["owner"], created_at=record["created_at"])


def _to_member(record: sqlite3.Row) -> Member:
    return Member(
        project_id=record["project_id"],
        person=record["person"],
        role=Role(record["role"]),
        added_by=record["added_by"],
        added_at=record["added_at"],
    )


class SqliteAdminStore:
    """Persistent registry of projects and their members, on SQLite."""

    __slots__ = ("_db", "_path")

    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: SqliteDb | None = None

    @property
    def _store(self) -> SqliteDb:
        if self._db is None:
            msg = "AdminStore is not connected"
            raise RuntimeError(msg)
        return self._db

    async def connect(self) -> None:
        """Open the database and ensure the schema exists."""
        self._db = SqliteDb(self._path)
        self._db.run_sync(lambda conn: conn.executescript(_SCHEMA))
        logger.info("AdminStore connected (%s)", self._path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            self._db.close()
            self._db = None

    # ----------------------------------------------------------------- writes
    async def create_project(self, project_id: str, owner: str) -> Project:
        owner = normalize_identity(owner)

        def _fn(conn: sqlite3.Connection) -> Project:
            if conn.execute("SELECT 1 FROM project WHERE id=?", (project_id,)).fetchone() is not None:
                raise ToolError(ErrorCode.PROJECT_EXISTS, f"project '{project_id}' already exists")
            created_at = _now()
            conn.execute(
                "INSERT INTO project(id, owner, created_at) VALUES(?,?,?)",
                (project_id, owner, created_at),
            )
            conn.execute(
                "INSERT INTO project_member(project_id, person, role, added_by, added_at) VALUES(?,?,?,?,?)",
                (project_id, owner, Role.OWNER.value, owner, created_at),
            )
            return Project(id=project_id, owner=owner, created_at=created_at)

        return await self._store.run(_fn)

    async def delete_project(self, project_id: str) -> None:
        await self._store.run(lambda conn: conn.execute("DELETE FROM project WHERE id=?", (project_id,)))

    async def add_member(self, project_id: str, person: str, added_by: str) -> Member:
        person = normalize_identity(person)
        added_by = normalize_identity(added_by)

        def _fn(conn: sqlite3.Connection) -> Member:
            added_at = _now()
            conn.execute(
                "INSERT INTO project_member(project_id, person, role, added_by, added_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(project_id, person) DO UPDATE SET added_by=excluded.added_by",
                (project_id, person, Role.MEMBER.value, added_by, added_at),
            )
            record = conn.execute(
                "SELECT project_id, person, role, added_by, added_at FROM project_member "
                "WHERE project_id=? AND person=?",
                (project_id, person),
            ).fetchone()
            return _to_member(record)

        return await self._store.run(_fn)

    async def remove_member(self, project_id: str, person: str) -> None:
        person = normalize_identity(person)
        project = await self.get_project(project_id)
        if project is None:
            raise ToolError(ErrorCode.PROJECT_NOT_FOUND, f"project '{project_id}' not found")
        if project.owner == person:
            raise ToolError(ErrorCode.INVALID_ARGUMENT, "cannot remove the project owner")
        await self._store.run(
            lambda conn: conn.execute(
                "DELETE FROM project_member WHERE project_id=? AND person=?",
                (project_id, person),
            )
        )

    # ------------------------------------------------------------------ reads
    async def get_project(self, project_id: str) -> Project | None:
        def _fn(conn: sqlite3.Connection) -> Project | None:
            record = conn.execute("SELECT id, owner, created_at FROM project WHERE id=?", (project_id,)).fetchone()
            return _to_project(record) if record else None

        return await self._store.run(_fn)

    async def list_projects_for(self, person: str) -> list[Project]:
        person = normalize_identity(person)

        def _fn(conn: sqlite3.Connection) -> list[Project]:
            rows = conn.execute(
                "SELECT p.id, p.owner, p.created_at FROM project p "
                "JOIN project_member m ON m.project_id = p.id "
                "WHERE m.person=? ORDER BY p.created_at",
                (person,),
            ).fetchall()
            return [_to_project(r) for r in rows]

        return await self._store.run(_fn)

    async def list_all_projects(self) -> list[Project]:
        def _fn(conn: sqlite3.Connection) -> list[Project]:
            rows = conn.execute("SELECT id, owner, created_at FROM project ORDER BY created_at").fetchall()
            return [_to_project(r) for r in rows]

        return await self._store.run(_fn)

    async def list_all_persons(self) -> list[str]:
        def _fn(conn: sqlite3.Connection) -> list[str]:
            rows = conn.execute("SELECT DISTINCT person FROM project_member ORDER BY person").fetchall()
            return [r["person"] for r in rows]

        return await self._store.run(_fn)

    async def list_members(self, project_id: str) -> list[Member]:
        def _fn(conn: sqlite3.Connection) -> list[Member]:
            rows = conn.execute(
                "SELECT project_id, person, role, added_by, added_at FROM project_member "
                "WHERE project_id=? ORDER BY added_at",
                (project_id,),
            ).fetchall()
            return [_to_member(r) for r in rows]

        return await self._store.run(_fn)

    async def is_member(self, project_id: str, person: str) -> bool:
        person = normalize_identity(person)

        def _fn(conn: sqlite3.Connection) -> bool:
            return (
                conn.execute(
                    "SELECT 1 FROM project_member WHERE project_id=? AND person=?",
                    (project_id, person),
                ).fetchone()
                is not None
            )

        return await self._store.run(_fn)

    # ------------------------------------------------------------------ gates
    async def require_member(self, project_id: str, person: str) -> None:
        if await self.get_project(project_id) is None:
            raise ToolError(ErrorCode.PROJECT_NOT_FOUND, f"project '{project_id}' not found")
        if not await self.is_member(project_id, person):
            raise ToolError(ErrorCode.FORBIDDEN, f"'{person}' is not a member of '{project_id}'")

    async def require_owner(self, project_id: str, person: str) -> Project:
        person = normalize_identity(person)
        project = await self.get_project(project_id)
        if project is None:
            raise ToolError(ErrorCode.PROJECT_NOT_FOUND, f"project '{project_id}' not found")
        if project.owner != person:
            raise ToolError(ErrorCode.FORBIDDEN, f"'{person}' is not the owner of '{project_id}'")
        return project
