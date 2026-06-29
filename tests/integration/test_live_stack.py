"""End-to-end integration test against a live SQLite + MinIO/S3 stack.

Skipped unless ``MCP_FS_INTEGRATION=1``. Drives the real tool surface through
FastMCP against the real SqliteMetaStore and MinioBlobStore.

Run it (MinIO must be reachable at the configured endpoint)::

    MCP_FS_INTEGRATION=1 uv run pytest -q -m integration tests/integration
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from mcp_fs.backends import build_admin_store
from mcp_fs.config import load_server_config
from mcp_fs.context import ToolContext
from mcp_fs.manager import StoreManager
from mcp_fs.safety import SafetyManager
from mcp_fs.server import register_all
from tests.conftest import acting_as

pytestmark = pytest.mark.integration

_CONFIG = Path("config/local.yaml")
_PROJECT = "it-fs-live"


async def _call(mcp: FastMCP, tool: str, **arguments: object) -> dict:
    result = await mcp.call_tool(tool, arguments)
    if isinstance(result, tuple):
        _content, structured = result
        if structured is not None:
            return structured
        result = _content
    return json.loads(result[0].text)


async def test_live_create_write_read_delete(tmp_path: Path) -> None:
    config = load_server_config(_CONFIG)
    config.infra.meta.dir = str(tmp_path / "volumes")
    config.infra.admin.path = str(tmp_path / "admin.db")

    store = build_admin_store(config)
    await store.connect()
    manager = StoreManager(config)
    ctx = ToolContext(config=config, store=store, manager=manager, safety=SafetyManager(config.safety))
    mcp: FastMCP = FastMCP("integration")
    register_all(mcp, ctx)

    try:
        with acting_as("alice"):  # alice is a platform admin in config/local.yaml
            created = await _call(mcp, "admin.create_project", project_id=_PROJECT, owner="alice")
            assert created["owner"] == "alice"

            await _call(mcp, "fs.write", mount_id=_PROJECT, path="/dir/hello.txt", content="hello live\n")
            read = await _call(mcp, "fs.read", mount_id=_PROJECT, path="/dir/hello.txt")
            assert "hello live" in read["content"]

            # content-addressed copy: same bytes, a second path
            await _call(mcp, "fs.copy", mount_id=_PROJECT, source="/dir/hello.txt", destination="/dir/clone.txt")
            digest = await _call(mcp, "fs.hash", mount_id=_PROJECT, path="/dir/clone.txt")
            assert digest["size"] == len("hello live\n")

            grep = await _call(mcp, "fs.grep", mount_id=_PROJECT, pattern="live", output_mode="content")
            assert any(hit["path"].endswith("hello.txt") for hit in grep["matches"])

            listing = await _call(mcp, "fs.list_dir", mount_id=_PROJECT, path="/dir")
            assert listing["total"] == 2

            await _call(mcp, "fs.delete", mount_id=_PROJECT, path="/dir/hello.txt")
            exists = await _call(mcp, "fs.exists", mount_id=_PROJECT, path="/dir/hello.txt")
            assert exists["exists"] is False
    finally:
        await manager.deprovision_volume(_PROJECT)
        await store.delete_project(_PROJECT)
        await store.close()
