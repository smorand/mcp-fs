"""CLI smoke tests."""

from __future__ import annotations

from typer.testing import CliRunner

from mcp_fs.mcp_fs import app
from mcp_fs.version import __version__

runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_serve_missing_config_fails() -> None:
    result = runner.invoke(app, ["serve", "--config", "does-not-exist.yaml"])
    assert result.exit_code != 0
