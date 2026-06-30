"""Compose the FastMCP streamable-HTTP server and its FastAPI host application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

from mcp_fs import admin_tools
from mcp_fs.backends import build_admin_store
from mcp_fs.context import ToolContext
from mcp_fs.fs_tools import edit, lifecycle, listing, metadata, read, search, write
from mcp_fs.identity import IdentityMiddleware, IdentityResolver
from mcp_fs.manager import StoreManager
from mcp_fs.safety import SafetyManager
from mcp_fs.version import __version__

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from mcp_fs.models import ServerConfig

logger = logging.getLogger(__name__)

_TOOL_MODULES = (read, write, edit, search, listing, metadata, lifecycle, admin_tools)


def register_all(mcp: FastMCP, ctx: ToolContext) -> None:
    """Register every tool family on the FastMCP instance."""
    for module in _TOOL_MODULES:
        module.register(mcp, ctx)


def build_app(config: ServerConfig) -> FastAPI:
    """Build the FastAPI application hosting the MCP streamable-HTTP endpoint."""
    store = build_admin_store(config)
    manager = StoreManager(config)
    safety = SafetyManager(config.safety)
    ctx = ToolContext(config=config, store=store, manager=manager, safety=safety)

    mcp: FastMCP = FastMCP(
        "mcp-fs",
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
            logger.info("mcp-fs %s ready (auth=jwt)", __version__)
            try:
                yield
            finally:
                await store.close()

    app = FastAPI(title="mcp-fs", version=__version__, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    app.mount("/", mcp_app)
    app.add_middleware(IdentityMiddleware, resolver=resolver, protected_prefix=config.server.mcp_path)

    app.state.tool_context = ctx
    app.state.store = store
    app.state.manager = manager
    return app
