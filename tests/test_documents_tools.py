"""fs.extract_text / fs.write_docx tool wrappers (in-process, fake backends)."""

from __future__ import annotations

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError as FastMcpToolError

from mcp_fs import identity
from mcp_fs.context import ToolContext
from mcp_fs.extract import extract
from mcp_fs.fs_tools import documents
from mcp_fs.safety import SafetyManager
from tests.conftest import FakeManager, FakeStore, FakeVolume, make_config


async def _wire() -> tuple[FastMCP, FakeVolume]:
    config = make_config()
    config.safety.write_quota_bytes = 5_000_000  # a real .docx is ~40 KB
    store = FakeStore()
    volume = FakeVolume()
    ctx = ToolContext(config=config, store=store, manager=FakeManager(volume), safety=SafetyManager(config.safety))
    await store.create_project("proj-a", "alice")
    mcp = FastMCP("t")
    documents.register(mcp, ctx)
    return mcp, volume


@pytest.mark.asyncio
async def test_write_docx_then_extract_it_back() -> None:
    mcp, volume = await _wire()
    token = identity._current_identity.set("alice")
    try:
        await mcp.call_tool(
            "fs.write_docx",
            {"mount_id": "proj-a", "path": "/synthese.docx", "markdown": "# Titre\n\nUn **point** clef.", "title": "S"},
        )
        # The tool wrote a real .docx blob; extract it back out.
        data = await volume.read_bytes("/synthese.docx")
        result = extract(data, "/synthese.docx")
        assert result.fmt == "docx" and "Titre" in result.text and "point" in result.text
        # And the extract_text tool returns structured text over the same file.
        out = await mcp.call_tool("fs.extract_text", {"mount_id": "proj-a", "path": "/synthese.docx"})
        assert "Titre" in str(out)
    finally:
        identity._current_identity.reset(token)


@pytest.mark.asyncio
async def test_write_docx_rejects_non_docx_path() -> None:
    mcp, _ = await _wire()
    token = identity._current_identity.set("alice")
    try:
        with pytest.raises(FastMcpToolError):
            await mcp.call_tool("fs.write_docx", {"mount_id": "proj-a", "path": "/x.txt", "markdown": "# T"})
    finally:
        identity._current_identity.reset(token)


@pytest.mark.asyncio
async def test_extract_text_rejects_audio() -> None:
    mcp, volume = await _wire()
    await volume.write_bytes_atomic("/song.mp3", b"\x00\x01")
    token = identity._current_identity.set("alice")
    try:
        with pytest.raises(FastMcpToolError):
            await mcp.call_tool("fs.extract_text", {"mount_id": "proj-a", "path": "/song.mp3"})
    finally:
        identity._current_identity.reset(token)


@pytest.mark.asyncio
async def test_extract_text_forbidden_for_non_member() -> None:
    mcp, _ = await _wire()
    token = identity._current_identity.set("mallory@example.com")
    try:
        with pytest.raises(FastMcpToolError):
            await mcp.call_tool("fs.extract_text", {"mount_id": "proj-a", "path": "/whatever.txt"})
    finally:
        identity._current_identity.reset(token)
