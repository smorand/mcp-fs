"""Configuration loading: resolve the active YAML file and parse it into models.

The active file is selected through environment variables (serverless friendly):

* ``MCP_FS_CONFIG`` : absolute path to a YAML file (highest priority), or
* ``MCP_FS_CONFIG_DIR`` + ``MCP_FS_CONFIG_NAME`` : pick ``<dir>/<name>.yaml``.

A ``--config`` CLI flag overrides everything by setting ``MCP_FS_CONFIG``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

from mcp_fs.models import ServerConfig

logger = logging.getLogger(__name__)

APP_NAME = "mcp-fs"


class Settings(BaseSettings):
    """Environment-driven settings that locate the active configuration file."""

    model_config = SettingsConfigDict(
        env_prefix="MCP_FS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = APP_NAME
    config: str | None = None
    config_dir: str = "config"
    config_name: str = "local"

    def resolve_config_path(self) -> Path:
        """Return the path of the YAML configuration file to load."""
        if self.config:
            return Path(self.config)
        return Path(self.config_dir) / f"{self.config_name}.yaml"


def load_server_config(path: Path) -> ServerConfig:
    """Read and validate the YAML configuration file into a :class:`ServerConfig`."""
    if not path.is_file():
        msg = f"configuration file not found: {path}"
        raise FileNotFoundError(msg)
    logger.info("Loading configuration from %s", path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ServerConfig.model_validate(raw)


def volume_meta_path(config: ServerConfig, project_id: str) -> Path:
    """Return the SQLite database path backing ``project_id``'s metadata tree."""
    return Path(config.infra.meta.dir) / f"{project_id}.db"


def volume_bucket(config: ServerConfig, project_id: str) -> str:
    """Return the object-storage bucket name backing ``project_id``'s blobs."""
    return f"{config.infra.blob.bucket_prefix}{project_id}"


def admin_db_path(config: ServerConfig) -> Path:
    """Return the SQLite database path backing the ACL store."""
    return Path(config.infra.admin.path)
