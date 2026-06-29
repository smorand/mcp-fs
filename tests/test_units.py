"""Unit tests: safety contract, config helpers, backend factories, manager."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_fs.backends import build_admin_store, build_blob_store, build_meta_store
from mcp_fs.config import admin_db_path, load_server_config, volume_bucket, volume_meta_path
from mcp_fs.manager import StoreManager
from mcp_fs.minio_blob import MinioBlobStore
from mcp_fs.models import ErrorCode, ToolError
from mcp_fs.safety import SafetyConfig, SafetyManager
from mcp_fs.sqlite_admin import SqliteAdminStore
from mcp_fs.sqlite_meta import SqliteMetaStore
from tests.conftest import make_config
from tests.test_sqlite_meta import InMemoryBlob


def test_normalize_path_valid() -> None:
    safety = SafetyManager(SafetyConfig())
    assert safety.normalize_path("a/b") == "/a/b"
    assert safety.normalize_path("/a/../b") == "/b"
    assert safety.normalize_path("/") == "/"


def test_normalize_path_jails_and_rejects_nul() -> None:
    safety = SafetyManager(SafetyConfig())
    # normpath collapses leading "/.." to "/", so traversal stays jailed (not an escape)
    assert safety.normalize_path("/../etc/passwd") == "/etc/passwd"
    assert safety.normalize_path("../../x") == "/x"
    with pytest.raises(ToolError, match="ERR_PATH_OUT_OF_BOUNDS"):
        safety.normalize_path("a\x00b")


def test_write_quota_and_audit() -> None:
    safety = SafetyManager(SafetyConfig(write_quota_bytes=10))
    safety.charge_write("alice", "p", 6)
    with pytest.raises(ToolError, match="ERR_WRITE_QUOTA_EXCEEDED"):
        safety.charge_write("alice", "p", 6)
    safety.record_audit("alice", "p", "write", "/x", "6 bytes")
    assert safety.session("alice", "p").audit[-1].op == "write"
    assert safety.trash_path("/a/b.txt").startswith("/.mcp_trash/")


def test_config_helpers_and_load() -> None:
    config = load_server_config(Path("config/local.yaml"))
    assert config.infra.meta.backend == "sqlite"
    assert config.infra.blob.backend == "minio"
    assert volume_bucket(config, "proj-a") == "mcpfs-proj-a"
    assert volume_meta_path(config, "proj-a") == Path("state/volumes/proj-a.db")
    assert admin_db_path(config) == Path("state/admin.db")


def test_backend_factories() -> None:
    config = make_config()
    assert isinstance(build_admin_store(config), SqliteAdminStore)
    meta = build_meta_store(config, "proj-a")
    assert isinstance(meta, SqliteMetaStore)
    meta.close()
    assert isinstance(build_blob_store(config, "proj-a"), MinioBlobStore)


def test_backend_factories_reject_unknown() -> None:
    config = make_config()
    config.infra.meta.backend = "bogus"  # type: ignore[assignment]
    config.infra.blob.backend = "bogus"  # type: ignore[assignment]
    config.infra.admin.backend = "bogus"  # type: ignore[assignment]
    for builder in (
        lambda: build_meta_store(config, "p"),
        lambda: build_blob_store(config, "p"),
        lambda: build_admin_store(config),
    ):
        with pytest.raises(ToolError, match="ERR_NOT_SUPPORTED"):
            builder()
    assert ErrorCode.NOT_SUPPORTED.value == "ERR_NOT_SUPPORTED"


async def test_manager_provision_and_teardown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config()
    config.infra.meta.dir = str(tmp_path / "volumes")
    monkeypatch.setattr("mcp_fs.manager.build_blob_store", lambda _config, _pid: InMemoryBlob())

    manager = StoreManager(config)
    await manager.provision_volume("proj-a")
    db_file = Path(config.infra.meta.dir) / "proj-a.db"
    assert db_file.exists()

    client = await manager.get_client("proj-a")
    assert client is await manager.get_client("proj-a")  # cached
    await client.write_bytes_atomic("/hello.txt", b"hi")
    assert await client.read_text("/hello.txt") == "hi"

    manager.forget("proj-a")
    await manager.deprovision_volume("proj-a")
    assert not db_file.exists()
