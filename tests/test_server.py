"""Tests for the FastAPI host app: health endpoint and identity gate."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mcp_fs.server import build_app
from mcp_fs.version import __version__
from tests.conftest import make_config


def test_health_and_unauthorized_mcp(tmp_path: Path) -> None:
    config = make_config()
    config.infra.admin.path = str(tmp_path / "admin.db")
    config.infra.meta.dir = str(tmp_path / "volumes")
    app = build_app(config)
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok", "version": __version__}

        # /mcp without an identity header is rejected by the middleware (401)
        denied = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert denied.status_code == 401
        assert denied.json()["error"] == "ERR_UNAUTHENTICATED"
