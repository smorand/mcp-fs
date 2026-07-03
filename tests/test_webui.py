"""Tests for the web UI plus the /api/fs data plane (cookie and JWT auth)."""

from __future__ import annotations

import asyncio
import io
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mcp_fs.context import ToolContext
from mcp_fs.models import WebUiConfig
from mcp_fs.safety import SafetyManager
from mcp_fs.webui import mount_web
from tests.conftest import FakeManager, FakeStore, FakeVolume, make_config, mint


def _build() -> tuple[TestClient, FakeStore, FakeVolume]:
    config = make_config()
    config.webui = WebUiConfig(enabled=True, secret_key="test-secret")
    store = FakeStore()
    volume = FakeVolume()
    manager = FakeManager(volume)
    ctx = ToolContext(config=config, store=store, manager=manager, safety=SafetyManager(config.safety))  # type: ignore[arg-type]
    asyncio.run(store.create_project("proj-a", "alice"))
    app = FastAPI()
    mount_web(app, ctx)
    return TestClient(app), store, volume


def _login(client: TestClient, email: str) -> None:
    resp = client.post("/login", data={"email": email}, follow_redirects=False)
    assert resp.status_code == 303


def _bearer(email: str) -> dict[str, str]:
    return {"X-Forwarded-Authorization": f"Bearer {mint(email)}"}


def test_pages_render() -> None:
    client, _store, _vol = _build()
    assert client.get("/login").status_code == 200
    # not logged in -> index redirects to login
    assert client.get("/", follow_redirects=False).status_code == 303
    _login(client, "alice")
    index = client.get("/")
    assert index.status_code == 200
    assert "proj-a" in index.text


def test_roots_cookie_and_jwt() -> None:
    client, _store, _vol = _build()
    # no auth
    assert client.get("/api/fs/roots").status_code == 401
    # cookie (caseless email)
    _login(client, "ALICE")
    body = client.get("/api/fs/roots").json()
    assert [r["mount_id"] for r in body["roots"]] == ["proj-a"]
    # jwt (fresh client, no cookie)
    fresh, _s, _v = _build()
    jwt_body = fresh.get("/api/fs/roots", headers=_bearer("alice")).json()
    assert [r["mount_id"] for r in jwt_body["roots"]] == ["proj-a"]


def test_upload_list_download_roundtrip() -> None:
    client, _store, _vol = _build()
    _login(client, "alice")
    up = client.post(
        "/api/fs/proj-a/upload",
        data={"directory": "/docs"},
        files=[("files", ("hello.txt", b"bonjour\n", "text/plain"))],
    )
    assert up.status_code == 200
    assert up.json()["written"] == ["/docs/hello.txt"]

    listing = client.get("/api/fs/proj-a/list", params={"path": "/docs"}).json()
    assert [e["name"] for e in listing["entries"]] == ["hello.txt"]

    down = client.get("/api/fs/proj-a/download", params={"path": "/docs/hello.txt"})
    assert down.status_code == 200
    assert down.content == b"bonjour\n"
    assert "attachment" in down.headers["content-disposition"]


def test_folder_upload_with_relative_paths() -> None:
    client, _store, _vol = _build()
    _login(client, "alice")
    up = client.post(
        "/api/fs/proj-a/upload",
        data={"directory": "/root", "paths": ["a/x.txt", "a/b/y.txt"]},
        files=[
            ("files", ("x.txt", b"x", "text/plain")),
            ("files", ("y.txt", b"y", "text/plain")),
        ],
    )
    assert set(up.json()["written"]) == {"/root/a/x.txt", "/root/a/b/y.txt"}


def test_mkdir_move_delete() -> None:
    client, _store, _vol = _build()
    _login(client, "alice")
    client.post("/api/fs/proj-a/upload", data={"directory": "/"}, files=[("files", ("f.txt", b"hi", "text/plain"))])
    assert client.post("/api/fs/proj-a/mkdir", json={"path": "/sub"}).status_code == 200
    assert client.post("/api/fs/proj-a/move", json={"source": "/f.txt", "destination": "/sub/g.txt"}).status_code == 200
    listing = client.get("/api/fs/proj-a/list", params={"path": "/sub"}).json()
    assert [e["name"] for e in listing["entries"]] == ["g.txt"]
    assert client.post("/api/fs/proj-a/delete", json={"path": "/sub"}).status_code == 200
    assert client.get("/api/fs/proj-a/list", params={"path": "/sub"}).json()["entries"] == []


def test_download_zip() -> None:
    client, _store, _vol = _build()
    _login(client, "alice")
    client.post(
        "/api/fs/proj-a/upload",
        data={"directory": "/pack", "paths": ["a.txt", "d/b.txt"]},
        files=[("files", ("a.txt", b"aaa", "text/plain")), ("files", ("b.txt", b"bbb", "text/plain"))],
    )
    resp = client.get("/api/fs/proj-a/download-zip", params={"path": "/pack"})
    assert resp.status_code == 200
    names = set(zipfile.ZipFile(io.BytesIO(resp.content)).namelist())
    assert names == {"a.txt", "d/b.txt"}


def test_non_member_is_forbidden() -> None:
    client, _store, _vol = _build()
    resp = client.get("/api/fs/proj-a/list", params={"path": "/"}, headers=_bearer("mallory@example.com"))
    assert resp.status_code == 403


def test_path_escape_rejected() -> None:
    client, _store, _vol = _build()
    _login(client, "alice")
    resp = client.get("/api/fs/proj-a/download", params={"path": "/a\x00b"})
    assert resp.status_code == 400
