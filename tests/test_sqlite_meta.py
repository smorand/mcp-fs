"""Tests for the real SQLite metadata store and the VolumeClient composition.

These run without MinIO by pairing the real :class:`SqliteMetaStore` with an
in-memory blob store, so the content-addressing, reference counting, rename and
subtree logic are exercised for real.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mcp_fs.sqlite_admin import SqliteAdminStore
from mcp_fs.sqlite_meta import SqliteMetaStore
from mcp_fs.volume import VolumeClient


class InMemoryBlob:
    """In-memory BlobBackend for exercising the metadata store without MinIO."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, sha256: str, data: bytes) -> None:
        self.objects[sha256] = data

    async def get(self, sha256: str, offset: int = 0, length: int | None = None) -> bytes:
        data = self.objects[sha256][offset:]
        return data if length is None else data[:length]

    async def exists(self, sha256: str) -> bool:
        return sha256 in self.objects

    async def delete(self, sha256: str) -> None:
        self.objects.pop(sha256, None)

    async def ensure_bucket(self) -> None:
        return None

    async def remove_bucket(self) -> None:
        self.objects.clear()


def _make_volume(tmp_path: Path) -> tuple[VolumeClient, InMemoryBlob]:
    meta = SqliteMetaStore(tmp_path / "vol.db")
    blob = InMemoryBlob()
    return VolumeClient("vol", meta, blob), blob


async def test_write_read_roundtrip_and_parents(tmp_path: Path) -> None:
    volume, _blob = _make_volume(tmp_path)
    await volume.write_bytes_atomic("/dir/sub/file.txt", b"hello\nworld\n")
    assert await volume.read_text("/dir/sub/file.txt") == "hello\nworld\n"
    # parents were auto-created as directories
    assert await volume.is_dir("/dir")
    assert await volume.is_dir("/dir/sub")
    listing = await volume.listdir("/dir/sub")
    assert listing == [("file.txt", "file", 12, listing[0][3])]


async def test_content_dedup_and_refcount_gc(tmp_path: Path) -> None:
    volume, blob = _make_volume(tmp_path)
    sha = hashlib.sha256(b"same content").hexdigest()
    await volume.write_bytes_atomic("/a.txt", b"same content")
    await volume.write_bytes_atomic("/b.txt", b"same content")
    # identical content stored once, referenced twice
    assert list(blob.objects) == [sha]
    # deleting one path keeps the blob (still referenced by the other)
    await volume.remove("/a.txt")
    assert sha in blob.objects
    # deleting the last reference garbage-collects the blob
    await volume.remove("/b.txt")
    assert blob.objects == {}


async def test_overwrite_gcs_previous_blob(tmp_path: Path) -> None:
    volume, blob = _make_volume(tmp_path)
    await volume.write_bytes_atomic("/f.txt", b"first")
    await volume.write_bytes_atomic("/f.txt", b"second")
    assert list(blob.objects) == [hashlib.sha256(b"second").hexdigest()]


async def test_empty_file_stores_no_blob(tmp_path: Path) -> None:
    volume, blob = _make_volume(tmp_path)
    await volume.create_empty("/empty.txt")
    assert await volume.read_bytes("/empty.txt") == b""
    assert blob.objects == {}
    stat = await volume.stat("/empty.txt")
    assert stat.st_size == 0


async def test_append_and_partial_read(tmp_path: Path) -> None:
    volume, _blob = _make_volume(tmp_path)
    await volume.write_bytes_atomic("/log.txt", b"abc")
    await volume.append_bytes("/log.txt", b"defgh")
    assert await volume.read_bytes("/log.txt") == b"abcdefgh"
    assert await volume.read_bytes("/log.txt", 3, 2) == b"de"


async def test_rename_file_and_directory_subtree(tmp_path: Path) -> None:
    volume, _blob = _make_volume(tmp_path)
    await volume.write_bytes_atomic("/d/e/f.txt", b"deep")
    await volume.write_bytes_atomic("/d/g.txt", b"shallow")
    await volume.rename("/d", "/renamed")
    assert not await volume.exists("/d")
    assert await volume.read_text("/renamed/e/f.txt") == "deep"
    assert await volume.read_text("/renamed/g.txt") == "shallow"


async def test_rmtree_gcs_all_blobs(tmp_path: Path) -> None:
    volume, blob = _make_volume(tmp_path)
    await volume.write_bytes_atomic("/t/a.txt", b"aaa")
    await volume.write_bytes_atomic("/t/sub/b.txt", b"bbb")
    assert len(blob.objects) == 2
    await volume.rmtree("/t")
    assert blob.objects == {}
    assert not await volume.exists("/t")


async def test_walk_structure(tmp_path: Path) -> None:
    volume, _blob = _make_volume(tmp_path)
    await volume.write_bytes_atomic("/x/y.txt", b"y")
    await volume.makedirs("/x/z", exist_ok=True)
    walked = {dirpath: (sorted(dirs), sorted(files)) for dirpath, dirs, files in await volume.walk("/")}
    assert walked["/"][0] == ["x"]
    assert walked["/x"] == (["z"], ["y.txt"])


async def test_mkdir_requires_parent(tmp_path: Path) -> None:
    volume, _blob = _make_volume(tmp_path)
    with pytest.raises(FileNotFoundError):
        await volume.mkdir("/missing/child")
    await volume.makedirs("/missing", exist_ok=True)
    await volume.mkdir("/missing/child")
    assert await volume.is_dir("/missing/child")


async def test_admin_store_roundtrip(tmp_path: Path) -> None:
    store = SqliteAdminStore(tmp_path / "admin.db")
    await store.connect()
    try:
        project = await store.create_project("proj-a", "alice")
        assert project.owner == "alice"
        await store.add_member("proj-a", "bob", added_by="alice")
        assert await store.is_member("proj-a", "bob")
        await store.require_member("proj-a", "bob")
        await store.require_owner("proj-a", "alice")
        members = {m.person for m in await store.list_members("proj-a")}
        assert members == {"alice", "bob"}
        assert [p.id for p in await store.list_projects_for("bob")] == ["proj-a"]
        await store.remove_member("proj-a", "bob")
        assert not await store.is_member("proj-a", "bob")
        await store.delete_project("proj-a")
        assert await store.get_project("proj-a") is None
    finally:
        await store.close()
