"""Shared test fixtures: in-memory fakes for the volume and the ACL store.

These fakes let the real tool logic run without a live MinIO/S3 stack. Tests that
need the real SQLite metadata store use :class:`tests.test_sqlite_meta` helpers;
integration tests (marked ``integration``) exercise the real stack and are
deselected unless ``MCP_FS_INTEGRATION=1``.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import jwt as jwtlib
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.server.fastmcp import FastMCP

from mcp_fs import identity
from mcp_fs.context import ToolContext
from mcp_fs.models import (
    AdminConfig,
    AuthConfig,
    BlobConfig,
    ErrorCode,
    InfraConfig,
    JwtConfig,
    Member,
    Project,
    Role,
    SafetyConfig,
    ServerConfig,
    SqliteMetaConfig,
    ToolError,
)
from mcp_fs.safety import SafetyManager
from mcp_fs.server import register_all


def _generate_test_keypair() -> tuple[bytes, str]:
    """Generate an RS256 keypair; write the public key to a temp file for the verifier."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    handle = tempfile.NamedTemporaryFile(prefix="mcpfs-test-jwt-", suffix=".pub", delete=False)  # noqa: SIM115
    handle.write(public_pem)
    handle.close()
    return private_pem, handle.name


_TEST_PRIVATE_KEY, TEST_PUBLIC_KEY_PATH = _generate_test_keypair()


def mint(email: str, *, issuer: str = "web-a2a", ttl: int = 3600) -> str:
    """Mint a signed RS256 token for ``email`` (the test stand-in for web-a2a)."""
    now = int(time.time())
    return jwtlib.encode(
        {"email": email, "iss": issuer, "iat": now, "exp": now + ttl},
        _TEST_PRIVATE_KEY,
        algorithm="RS256",
    )


def bearer(email: str) -> dict[str, str]:
    """Return the forwarded-authorization header carrying a minted token for ``email``."""
    return {"X-Forwarded-Authorization": f"Bearer {mint(email)}"}


class FakeVolume:
    """In-memory implementation of the VolumeClient async interface."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {"/"}

    async def exists(self, path: str) -> bool:
        return path in self.files or path in self.dirs

    async def is_dir(self, path: str) -> bool:
        return path in self.dirs

    async def is_file(self, path: str) -> bool:
        return path in self.files

    async def stat(self, path: str) -> os.stat_result:
        if path in self.dirs:
            mode, size = 0o040755, 0
        elif path in self.files:
            mode, size = 0o100644, len(self.files[path])
        else:
            raise FileNotFoundError(path)
        return os.stat_result((mode, 0, 0, 1, 1000, 1000, size, 0, 0, 0))

    async def listdir(self, path: str) -> list[tuple[str, str, int, float]]:
        prefix = path.rstrip("/") + "/"
        seen: dict[str, tuple[str, int]] = {}
        for fpath, data in self.files.items():
            if fpath.startswith(prefix) and "/" not in fpath[len(prefix) :]:
                seen[fpath[len(prefix) :]] = ("file", len(data))
        for dpath in self.dirs:
            if dpath.startswith(prefix) and dpath != path and "/" not in dpath[len(prefix) :].rstrip("/"):
                name = dpath[len(prefix) :].rstrip("/")
                if name:
                    seen[name] = ("dir", 0)
        return [(name, kind, size, 0.0) for name, (kind, size) in seen.items()]

    async def walk(self, top: str) -> list[tuple[str, list[str], list[str]]]:
        result: list[tuple[str, list[str], list[str]]] = []
        for directory in sorted(self.dirs):
            if not (directory == top or directory.startswith(top.rstrip("/") + "/")):
                continue
            entries = await self.listdir(directory)
            dirs = [name for name, kind, _s, _m in entries if kind == "dir"]
            files = [name for name, kind, _s, _m in entries if kind == "file"]
            result.append((directory, dirs, files))
        return result

    async def read_bytes(self, path: str, offset: int = 0, length: int | None = None) -> bytes:
        if path not in self.files:
            raise FileNotFoundError(path)
        data = self.files[path][offset:]
        return data if length is None else data[:length]

    async def read_text(self, path: str) -> str:
        return (await self.read_bytes(path)).decode("utf-8", errors="replace")

    async def write_bytes_atomic(self, path: str, data: bytes) -> None:
        await self._ensure_parent(path)
        self.files[path] = data

    async def append_bytes(self, path: str, data: bytes) -> None:
        await self._ensure_parent(path)
        self.files[path] = self.files.get(path, b"") + data

    async def create_empty(self, path: str) -> None:
        await self.write_bytes_atomic(path, b"")

    async def makedirs(self, path: str, *, exist_ok: bool = True) -> None:
        if path in self.dirs and not exist_ok:
            raise FileExistsError(path)
        current = ""
        for part in path.strip("/").split("/"):
            current = f"{current}/{part}"
            self.dirs.add(current)

    async def mkdir(self, path: str) -> None:
        if path in self.dirs:
            raise FileExistsError(path)
        self.dirs.add(path)

    async def remove(self, path: str) -> None:
        if path not in self.files:
            raise FileNotFoundError(path)
        del self.files[path]

    async def rmdir(self, path: str) -> None:
        self.dirs.discard(path)

    async def rmtree(self, path: str) -> None:
        prefix = path.rstrip("/") + "/"
        for fpath in [p for p in self.files if p == path or p.startswith(prefix)]:
            del self.files[fpath]
        for dpath in [d for d in self.dirs if d == path or d.startswith(prefix)]:
            self.dirs.discard(dpath)

    async def rename(self, src: str, dst: str) -> None:
        if src in self.files:
            await self._ensure_parent(dst)
            self.files[dst] = self.files.pop(src)
        elif src in self.dirs:
            self.dirs.discard(src)
            self.dirs.add(dst)
        else:
            raise FileNotFoundError(src)

    async def _ensure_parent(self, path: str) -> None:
        parent = path.rsplit("/", 1)[0] or "/"
        if parent != "/":
            await self.makedirs(parent, exist_ok=True)


@dataclass
class _StoredProject:
    owner: str
    members: dict[str, Role] = field(default_factory=dict)


class FakeStore:
    """In-memory ACL store mirroring AdminStore semantics."""

    def __init__(self) -> None:
        self.projects: dict[str, _StoredProject] = {}

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def create_project(self, project_id: str, owner: str) -> Project:
        if project_id in self.projects:
            raise ToolError(ErrorCode.PROJECT_EXISTS, project_id)
        self.projects[project_id] = _StoredProject(owner=owner, members={owner: Role.OWNER})
        return Project(id=project_id, owner=owner, created_at="now")

    async def delete_project(self, project_id: str) -> None:
        self.projects.pop(project_id, None)

    async def get_project(self, project_id: str) -> Project | None:
        stored = self.projects.get(project_id)
        return Project(id=project_id, owner=stored.owner, created_at="now") if stored else None

    async def is_member(self, project_id: str, person: str) -> bool:
        stored = self.projects.get(project_id)
        return bool(stored and person in stored.members)

    async def add_member(self, project_id: str, person: str, added_by: str) -> Member:
        self.projects[project_id].members[person] = Role.MEMBER
        return Member(project_id=project_id, person=person, role=Role.MEMBER, added_by=added_by, added_at="now")

    async def remove_member(self, project_id: str, person: str) -> None:
        stored = self.projects[project_id]
        if stored.owner == person:
            raise ToolError(ErrorCode.INVALID_ARGUMENT, "cannot remove owner")
        stored.members.pop(person, None)

    async def list_members(self, project_id: str) -> list[Member]:
        stored = self.projects[project_id]
        return [
            Member(project_id=project_id, person=p, role=r, added_by=stored.owner, added_at="now")
            for p, r in stored.members.items()
        ]

    async def list_projects_for(self, person: str) -> list[Project]:
        return [
            Project(id=pid, owner=s.owner, created_at="now") for pid, s in self.projects.items() if person in s.members
        ]

    async def list_all_projects(self) -> list[Project]:
        return [Project(id=pid, owner=s.owner, created_at="now") for pid, s in self.projects.items()]

    async def list_all_persons(self) -> list[str]:
        persons: set[str] = set()
        for stored in self.projects.values():
            persons.update(stored.members)
        return sorted(persons)

    async def require_member(self, project_id: str, person: str) -> None:
        if project_id not in self.projects:
            raise ToolError(ErrorCode.PROJECT_NOT_FOUND, project_id)
        if not await self.is_member(project_id, person):
            raise ToolError(ErrorCode.FORBIDDEN, person)

    async def require_owner(self, project_id: str, person: str) -> Project:
        if project_id not in self.projects:
            raise ToolError(ErrorCode.PROJECT_NOT_FOUND, project_id)
        if self.projects[project_id].owner != person:
            raise ToolError(ErrorCode.FORBIDDEN, person)
        return Project(id=project_id, owner=person, created_at="now")


class FakeManager:
    """Manager returning a single shared FakeVolume; provisioning is a no-op."""

    def __init__(self, volume: FakeVolume) -> None:
        self.volume = volume
        self.provisioned: list[str] = []
        self.deprovisioned: list[str] = []

    async def get_client(self, project_id: str) -> FakeVolume:
        return self.volume

    async def provision_volume(self, project_id: str) -> None:
        self.provisioned.append(project_id)

    async def deprovision_volume(self, project_id: str) -> None:
        self.deprovisioned.append(project_id)

    def forget(self, project_id: str) -> None:
        pass


@dataclass
class Harness:
    """Bundle exposing the wired MCP instance and fakes for assertions."""

    mcp: FastMCP
    store: FakeStore
    manager: FakeManager
    volume: FakeVolume
    ctx: ToolContext

    async def call(self, tool: str, **arguments: Any) -> dict[str, Any]:
        result = await self.mcp.call_tool(tool, arguments)
        if isinstance(result, tuple):
            content, structured = result
            if structured is not None:
                return structured
            result = content
        if isinstance(result, list):
            return json.loads(result[0].text)
        return result


def make_config() -> ServerConfig:
    """Return a minimal in-memory server configuration."""
    return ServerConfig(
        auth=AuthConfig(admins=["alice"], jwt=JwtConfig(public_key_path=TEST_PUBLIC_KEY_PATH)),
        infra=InfraConfig(
            meta=SqliteMetaConfig(dir="state/volumes"),
            blob=BlobConfig(endpoint="http://minio:9000", access_key="ak", secret_key="sk"),
            admin=AdminConfig(path="state/admin.db"),
        ),
        safety=SafetyConfig(write_quota_bytes=10_000, read_guard=True),
    )


@pytest.fixture
def harness() -> Harness:
    """Wire a FastMCP instance over the fakes with all tools registered."""
    config = make_config()
    volume = FakeVolume()
    store = FakeStore()
    manager = FakeManager(volume)
    ctx = ToolContext(config=config, store=store, manager=manager, safety=SafetyManager(config.safety))  # type: ignore[arg-type]
    mcp: FastMCP = FastMCP("test")
    register_all(mcp, ctx)
    return Harness(mcp=mcp, store=store, manager=manager, volume=volume, ctx=ctx)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip integration tests unless MCP_FS_INTEGRATION=1 (needs a live MinIO/S3 stack)."""
    if os.environ.get("MCP_FS_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="integration: set MCP_FS_INTEGRATION=1 to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@contextlib.contextmanager
def acting_as(person: str) -> Iterator[None]:
    """Bind ``person`` as the current identity for the duration of the block."""
    token = identity._current_identity.set(person)
    try:
        yield
    finally:
        identity._current_identity.reset(token)
