"""CLI entry point: load the configuration and serve the MCP HTTP endpoint."""

from __future__ import annotations

import logging
from pathlib import Path  # noqa: TC003 - runtime type for the Typer option annotation
from typing import Annotated

import typer
import uvicorn

from mcp_fs.config import Settings, load_server_config
from mcp_fs.logging_config import setup_logging
from mcp_fs.server import build_app
from mcp_fs.tracing import configure_tracing
from mcp_fs.version import __version__

app = typer.Typer(add_completion=False, help="Pure-Python filesystem MCP server (SQLite metadata, object-store blobs).")
logger = logging.getLogger(__name__)


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Enable debug logging")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Only show warnings and errors")] = False,
) -> None:
    """mcp-fs server."""
    setup_logging(app_name="mcp-fs", verbose=verbose, quiet=quiet)
    configure_tracing(app_name="mcp-fs")


@app.command()
def serve(
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Path to the YAML configuration file")] = None,
) -> None:
    """Start the streamable-HTTP MCP server."""
    settings = Settings()
    if config is not None:
        settings.config = str(config)
    config_path = settings.resolve_config_path()
    server_config = load_server_config(config_path)
    logger.info("Serving mcp-fs %s on %s:%d", __version__, server_config.server.host, server_config.server.port)
    application = build_app(server_config)
    uvicorn.run(application, host=server_config.server.host, port=server_config.server.port, log_level="info")


@app.command()
def version() -> None:
    """Print the version and exit."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
