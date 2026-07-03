"""/api/fs parity endpoints: read, stat, hash, copy, glob, grep, extract, docx.

These mirror the MCP fs.* tools (same fs_ops underneath), so the data plane and
the agent plane can do the same things.
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mcp_fs.context import ToolContext
from mcp_fs.models import WebUiConfig
from mcp_fs.safety import SafetyManager
from mcp_fs.webui import mount_web
from tests.conftest import FakeManager, FakeStore, FakeVolume, make_config
from tests.test_webui import _login


def _build() -> TestClient:
    config = make_config()
    config.webui = WebUiConfig(enabled=True, secret_key="test-secret")
    config.safety.write_quota_bytes = 5_000_000  # a .docx is ~40 KB
    store = FakeStore()
    ctx = ToolContext(
        config=config, store=store, manager=FakeManager(FakeVolume()), safety=SafetyManager(config.safety)
    )
    asyncio.run(store.create_project("proj-a", "alice"))
    app = FastAPI()
    mount_web(app, ctx)
    client = TestClient(app)
    _login(client, "alice")
    return client


def test_read_stat_hash_count_copy_glob_grep_audit() -> None:
    client = _build()
    up = client.post(
        "/api/fs/proj-a/upload",
        data={"directory": "/docs"},
        files=[("files", ("hello.txt", b"alpha\nbeta\n", "text/plain"))],
    )
    assert up.status_code == 200

    assert "alpha" in client.get("/api/fs/proj-a/read", params={"path": "/docs/hello.txt"}).json()["content"]
    stat = client.get("/api/fs/proj-a/stat", params={"path": "/docs/hello.txt"}).json()
    assert stat["kind"] == "file" and stat["size"] > 0
    digest = client.get("/api/fs/proj-a/hash", params={"path": "/docs/hello.txt"}).json()
    assert digest["algo"] == "sha256" and len(digest["hash"]) == 64
    assert client.get("/api/fs/proj-a/count-lines", params={"path": "/docs/hello.txt"}).json()["total_lines"] == 2

    assert (
        client.post(
            "/api/fs/proj-a/copy", json={"source": "/docs/hello.txt", "destination": "/docs/copy.txt"}
        ).status_code
        == 200
    )
    assert "/docs/hello.txt" in client.get("/api/fs/proj-a/glob", params={"pattern": "*.txt"}).json()["matches"]
    grep = client.get("/api/fs/proj-a/grep", params={"pattern": "alpha", "output_mode": "files"}).json()
    assert "/docs/hello.txt" in grep["files"]
    assert any(e["op"] == "copy" for e in client.get("/api/fs/proj-a/audit-log").json()["entries"])


def test_extract_text_and_write_docx_over_api() -> None:
    client = _build()
    client.post(
        "/api/fs/proj-a/upload", data={"directory": "/"}, files=[("files", ("t.csv", b"a,b\n1,2\n", "text/csv"))]
    )
    extracted = client.post("/api/fs/proj-a/extract-text", json={"path": "/t.csv"}).json()
    assert extracted["md_path"] == "/t.md" and "| a | b |" in extracted["preview"]

    written = client.post("/api/fs/proj-a/write-docx", json={"path": "/out.docx", "markdown": "# Titre\n\nhi"})
    assert written.status_code == 200 and written.json()["bytes_written"] > 2000
    downloaded = client.get("/api/fs/proj-a/download", params={"path": "/out.docx"})
    assert downloaded.content[:2] == b"PK"  # a real .docx (zip)


def test_api_non_member_forbidden_on_parity_endpoint() -> None:
    from tests.test_webui import _bearer

    client = _build()
    client.cookies.clear()  # drop the alice session so the mallory bearer is used
    resp = client.get("/api/fs/proj-a/stat", params={"path": "/x"}, headers=_bearer("mallory@example.com"))
    assert resp.status_code == 403
