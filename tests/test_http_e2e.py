"""End-to-end tests through the real ASGI application and a real MCP round trip.

Unlike the functional tests (which call ``mcp.call_tool`` directly), these drive
the full HTTP path: the FastAPI host app, the identity middleware, and a real
``tools/call`` JSON-RPC exchange over the streamable-HTTP transport. They run
without a live MinIO/S3 stack by wiring the in-memory fakes from
:mod:`tests.conftest` behind the same server plumbing that :func:`build_app`
uses; the JWT-mode tests exercise the production :func:`build_app` directly for
admin-only tools that need no blob storage.

The MCP streamable-HTTP transport enforces DNS rebinding protection: FastMCP
auto-allows ``localhost:*`` / ``127.0.0.1:*`` host headers, so the TestClient is
created with a ``base_url`` carrying an explicit port to satisfy that guard.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.fastmcp import FastMCP

from mcp_fs.context import ToolContext
from mcp_fs.identity import IdentityMiddleware, IdentityResolver
from mcp_fs.models import AuthConfig, JwtConfig
from mcp_fs.safety import SafetyManager
from mcp_fs.server import build_app, register_all
from tests.conftest import FakeManager, FakeStore, FakeVolume, bearer, make_config

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from httpx import Response

    from mcp_fs.models import ServerConfig

# The transport security guard accepts hosts shaped like ``localhost:<port>``.
_BASE_URL = "http://localhost:5002"
_MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _parse_mcp(response: Response) -> dict[str, Any]:
    """Decode an MCP JSON-RPC reply from either an SSE stream or plain JSON."""
    if "event-stream" in response.headers.get("content-type", ""):
        for line in response.text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[len("data: ") :])
        raise AssertionError("no SSE data frame in response")
    return response.json()


def _tools_call(client: TestClient, headers: dict[str, str], tool: str, **arguments: Any) -> Response:
    """Issue a ``tools/call`` over HTTP and return the raw response."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    return client.post("/mcp", headers={**_MCP_HEADERS, **headers}, json=body)


def _call_as(client: TestClient, actor: str, tool: str, /, **arguments: Any) -> dict[str, Any]:
    """Call ``tool`` as ``actor`` (a minted bearer) and return the JSON-RPC ``result``.

    ``actor``, ``client`` and ``tool`` are positional-only so a tool argument
    named ``person`` (e.g. on admin.add_member) cannot collide with them.
    """
    response = _tools_call(client, bearer(actor), tool, **arguments)
    assert response.status_code == 200, response.text
    payload = _parse_mcp(response)
    return payload["result"]


def _structured(result: dict[str, Any]) -> dict[str, Any]:
    """Return the structured tool output, asserting the call did not error."""
    assert result.get("isError") is False, result
    return result["structuredContent"]


def _error_text(result: dict[str, Any]) -> str:
    """Return the error text of a tool result that signalled ``isError``."""
    assert result.get("isError") is True, result
    return result["content"][0]["text"]


def _build_fake_app(config: ServerConfig, store: FakeStore, manager: FakeManager) -> FastAPI:
    """Mirror :func:`build_app` but over the in-memory fakes (no MinIO needed).

    This keeps the real server plumbing under test (the identity middleware, the
    streamable-HTTP MCP mount, the lifespan that runs the session manager) while
    swapping only the storage backends for fakes.
    """
    safety = SafetyManager(config.safety)
    ctx = ToolContext(config=config, store=store, manager=manager, safety=safety)  # type: ignore[arg-type]
    mcp: FastMCP = FastMCP(
        "mcp-fs-test",
        stateless_http=True,
        json_response=False,
        streamable_http_path=config.server.mcp_path,
    )
    register_all(mcp, ctx)
    mcp_app = mcp.streamable_http_app()
    resolver = IdentityResolver(config.auth)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            await store.connect()
            try:
                yield
            finally:
                await store.close()

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.mount("/", mcp_app)
    app.add_middleware(IdentityMiddleware, resolver=resolver, protected_prefix=config.server.mcp_path)
    return app


@pytest.fixture
def fake_http_client() -> Iterator[TestClient]:
    """A TestClient over the real ASGI app wired to the in-memory fakes."""
    config = make_config()
    store = FakeStore()
    manager = FakeManager(FakeVolume())
    app = _build_fake_app(config, store, manager)
    with TestClient(app, base_url=_BASE_URL) as client:
        yield client


def _rsa_keypair(tmp_path: Path) -> tuple[bytes, Path]:
    """Generate an RSA keypair, returning the private PEM and the public-key file path."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_file = tmp_path / "jwt.pub"
    public_file.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_pem, public_file


# --------------------------------------------------------------------------- #
# Tool surface and round trips over HTTP (debug auth, in-memory fakes)
# --------------------------------------------------------------------------- #
def test_http_tools_list_exposes_full_surface(fake_http_client: TestClient) -> None:
    """tools/list over HTTP returns the whole surface: 31 fs.* + 8 admin.* = 39."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    response = fake_http_client.post("/mcp", headers={**_MCP_HEADERS, **bearer("alice")}, json=body)
    assert response.status_code == 200
    tools = _parse_mcp(response)["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert len(names) == 39
    assert sum(name.startswith("fs.") for name in names) == 31
    assert sum(name.startswith("admin.") for name in names) == 8
    # A couple of representative tools are present and reachable.
    assert {"fs.read", "fs.write", "admin.create_project"} <= names


def test_http_create_write_read_roundtrip(fake_http_client: TestClient) -> None:
    """A real provision, write, then read cycle over the streamable-HTTP transport."""
    created = _structured(
        _call_as(fake_http_client, "alice", "admin.create_project", project_id="proj-a", owner="alice")
    )
    assert created["owner"] == "alice"

    written = _structured(
        _call_as(fake_http_client, "alice", "fs.write", mount_id="proj-a", path="/notes.txt", content="line1\nline2\n")
    )
    assert written["bytes_written"] == 12

    read = _structured(_call_as(fake_http_client, "alice", "fs.read", mount_id="proj-a", path="/notes.txt"))
    assert read["total_lines"] == 2
    assert "1\tline1" in read["content"]


def test_http_missing_identity_header_is_rejected(fake_http_client: TestClient) -> None:
    """A protected /mcp call without the forwarded-user header is a 401 at the middleware."""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "admin.list_projects", "arguments": {}},
    }
    response = fake_http_client.post("/mcp", headers=_MCP_HEADERS, json=body)
    assert response.status_code == 401
    assert response.json()["error"] == "ERR_UNAUTHENTICATED"


def test_http_error_code_propagates_for_non_member(fake_http_client: TestClient) -> None:
    """A ToolError surfaces as an MCP tool error (isError) carrying the stable ERR_ code."""
    _structured(_call_as(fake_http_client, "alice", "admin.create_project", project_id="proj-a", owner="alice"))
    result = _call_as(fake_http_client, "mallory", "fs.read", mount_id="proj-a", path="/secret.txt")
    assert "ERR_FORBIDDEN" in _error_text(result)


def test_http_admin_tool_rejects_non_admin(fake_http_client: TestClient) -> None:
    """Creating a project as a non-admin surfaces ERR_FORBIDDEN over MCP, not a crash."""
    result = _call_as(fake_http_client, "bob", "admin.create_project", project_id="proj-x", owner="bob")
    assert "ERR_FORBIDDEN" in _error_text(result)


def test_http_invalid_argument_propagates(fake_http_client: TestClient) -> None:
    """Argument validation errors propagate as MCP tool errors with ERR_INVALID_ARGUMENT."""
    result = _call_as(fake_http_client, "alice", "admin.create_project", project_id="Bad_ID", owner="alice")
    assert "ERR_INVALID_ARGUMENT" in _error_text(result)


def test_http_soft_delete_then_audit_log(fake_http_client: TestClient) -> None:
    """Write then soft-delete (trash), and confirm the audit log records both over HTTP."""
    _structured(_call_as(fake_http_client, "alice", "admin.create_project", project_id="proj-a", owner="alice"))
    _structured(_call_as(fake_http_client, "alice", "fs.write", mount_id="proj-a", path="/doc.txt", content="data"))

    deleted = _structured(_call_as(fake_http_client, "alice", "fs.delete", mount_id="proj-a", path="/doc.txt"))
    assert deleted["trashed"] is True
    assert deleted["trash_path"].startswith("/.mcp_trash/")

    # The file no longer exists at its original path, but lives under the trash dir.
    exists = _structured(_call_as(fake_http_client, "alice", "fs.exists", mount_id="proj-a", path="/doc.txt"))
    assert exists["exists"] is False

    audit = _structured(_call_as(fake_http_client, "alice", "fs.audit_log", mount_id="proj-a"))
    ops = [entry["op"] for entry in audit["entries"]]
    assert "write" in ops
    assert ops[-1] == "delete"
    assert audit["entries"][-1]["path"] == "/doc.txt"


# --------------------------------------------------------------------------- #
# Multi-member access end to end (owner adds member, member reads, others not)
# --------------------------------------------------------------------------- #
def test_http_multi_member_access_flow(fake_http_client: TestClient) -> None:
    """Owner adds a member who can then read; a non-member is forbidden, all over HTTP."""
    # Platform admin provisions a project owned by carol.
    _structured(_call_as(fake_http_client, "alice", "admin.create_project", project_id="team-proj", owner="carol"))
    # The owner seeds a shared file and adds bob as a member.
    _structured(
        _call_as(fake_http_client, "carol", "fs.write", mount_id="team-proj", path="/shared.txt", content="team\n")
    )
    added = _structured(_call_as(fake_http_client, "carol", "admin.add_member", project_id="team-proj", person="bob"))
    assert added["person"] == "bob"

    # The freshly added member can read the shared file.
    read = _structured(_call_as(fake_http_client, "bob", "fs.read", mount_id="team-proj", path="/shared.txt"))
    assert "team" in read["content"]

    # A member sees the membership roster; a non-member does not.
    members = _structured(_call_as(fake_http_client, "bob", "admin.list_members", project_id="team-proj"))
    assert {m["person"] for m in members["members"]} == {"carol", "bob"}

    forbidden = _call_as(fake_http_client, "dora", "fs.read", mount_id="team-proj", path="/shared.txt")
    assert "ERR_FORBIDDEN" in _error_text(forbidden)


def test_http_removed_member_loses_access(fake_http_client: TestClient) -> None:
    """After removal a former member is forbidden again, verified over the HTTP path."""
    _structured(_call_as(fake_http_client, "alice", "admin.create_project", project_id="proj-r", owner="alice"))
    _structured(_call_as(fake_http_client, "alice", "admin.add_member", project_id="proj-r", person="bob"))
    _structured(_call_as(fake_http_client, "alice", "fs.write", mount_id="proj-r", path="/f.txt", content="hi"))
    # bob can read while a member.
    _structured(_call_as(fake_http_client, "bob", "fs.read", mount_id="proj-r", path="/f.txt"))
    # Owner removes bob; bob is forbidden afterwards.
    _structured(_call_as(fake_http_client, "alice", "admin.remove_member", project_id="proj-r", person="bob"))
    result = _call_as(fake_http_client, "bob", "fs.read", mount_id="proj-r", path="/f.txt")
    assert "ERR_FORBIDDEN" in _error_text(result)


# --------------------------------------------------------------------------- #
# JWT-mode end to end through the production build_app (admin-only, no MinIO)
# --------------------------------------------------------------------------- #
def _jwt_app(tmp_path: Path) -> tuple[FastAPI, bytes]:
    """Build the production app in JWT mode and return it with the signing key."""
    private_pem, public_file = _rsa_keypair(tmp_path)
    config = make_config()
    config.auth = AuthConfig(admins=["alice"], jwt=JwtConfig(public_key_path=str(public_file)))
    config.infra.admin.path = str(tmp_path / "admin.db")
    config.infra.meta.dir = str(tmp_path / "volumes")
    return build_app(config), private_pem


def test_jwt_mode_valid_token_round_trip(tmp_path: Path) -> None:
    """A correctly signed token authenticates a real MCP tools/call through the middleware."""
    app, private_pem = _jwt_app(tmp_path)
    token = jwt.encode({"email": "alice", "iss": "web-a2a"}, private_pem, algorithm="RS256")
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "admin.list_projects", "arguments": {}},
    }
    with TestClient(app, base_url=_BASE_URL) as client:
        response = client.post(
            "/mcp", headers={**_MCP_HEADERS, "X-Forwarded-Authorization": f"Bearer {token}"}, json=body
        )
        assert response.status_code == 200
        result = _parse_mcp(response)["result"]
        assert _structured(result) == {"projects": []}


def test_jwt_mode_invalid_token_is_rejected(tmp_path: Path) -> None:
    """A malformed bearer token is rejected by the middleware with a 401 before any tool runs."""
    app, _ = _jwt_app(tmp_path)
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "admin.list_projects", "arguments": {}},
    }
    with TestClient(app, base_url=_BASE_URL) as client:
        response = client.post(
            "/mcp", headers={**_MCP_HEADERS, "X-Forwarded-Authorization": "Bearer not-a-jwt"}, json=body
        )
        assert response.status_code == 401
        assert response.json()["error"] == "ERR_UNAUTHENTICATED"


def test_jwt_mode_missing_bearer_is_rejected(tmp_path: Path) -> None:
    """Omitting the Authorization header in JWT mode yields a 401 at the middleware."""
    app, _ = _jwt_app(tmp_path)
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "admin.list_projects", "arguments": {}},
    }
    with TestClient(app, base_url=_BASE_URL) as client:
        response = client.post("/mcp", headers=_MCP_HEADERS, json=body)
        assert response.status_code == 401
        assert response.json()["error"] == "ERR_UNAUTHENTICATED"


def test_jwt_mode_token_without_username_claim_is_rejected(tmp_path: Path) -> None:
    """A validly signed token lacking the username claim is rejected by the middleware."""
    app, private_pem = _jwt_app(tmp_path)
    token = jwt.encode({"sub": "nobody", "iss": "web-a2a"}, private_pem, algorithm="RS256")
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "admin.list_projects", "arguments": {}},
    }
    with TestClient(app, base_url=_BASE_URL) as client:
        response = client.post(
            "/mcp", headers={**_MCP_HEADERS, "X-Forwarded-Authorization": f"Bearer {token}"}, json=body
        )
        assert response.status_code == 401
        assert response.json()["error"] == "ERR_UNAUTHENTICATED"
