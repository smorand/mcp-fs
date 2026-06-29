"""SQLite metadata backend: the directory tree plus blob reference counts.

One database file per volume. The ``nodes`` table is the directory tree, keyed
by normalized path; each file node points at a content sha256. The ``blob_refs``
table reference-counts those shas so the volume can deduplicate identical content
and tell the caller when an object is safe to delete from the blob store.

The store never touches bytes; it only manages metadata. The blob bytes live in
the :class:`~mcp_fs.protocols.BlobBackend`. ``VolumeClient`` wires the two.
"""

from __future__ import annotations

import posixpath
import stat as stat_module
import time
from typing import TYPE_CHECKING

from mcp_fs.protocols import NodeRow
from mcp_fs.sqlite_db import SqliteDb

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

_DIR_MODE = stat_module.S_IFDIR | 0o755
_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    path   TEXT PRIMARY KEY,
    parent TEXT,
    name   TEXT NOT NULL,
    kind   TEXT NOT NULL,
    size   INTEGER NOT NULL DEFAULT 0,
    mode   INTEGER NOT NULL,
    mtime  REAL NOT NULL,
    ctime  REAL NOT NULL,
    atime  REAL NOT NULL,
    sha256 TEXT
);
CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent);
CREATE TABLE IF NOT EXISTS blob_refs (
    sha256   TEXT PRIMARY KEY,
    refcount INTEGER NOT NULL,
    size     INTEGER NOT NULL
);
"""


def _parent_of(path: str) -> str | None:
    if path == "/":
        return None
    return posixpath.dirname(path) or "/"


def _name_of(path: str) -> str:
    return "" if path == "/" else posixpath.basename(path)


def _row(record: sqlite3.Row) -> NodeRow:
    return NodeRow(
        path=record["path"],
        parent=record["parent"],
        name=record["name"],
        kind=record["kind"],
        size=record["size"],
        mode=record["mode"],
        mtime=record["mtime"],
        ctime=record["ctime"],
        atime=record["atime"],
        sha256=record["sha256"],
    )


class SqliteMetaStore:
    """Metadata tree for one volume, backed by a single SQLite file."""

    __slots__ = ("_db",)

    def __init__(self, path: Path) -> None:
        self._db = SqliteDb(path)
        self._db.run_sync(self._init_schema)

    @staticmethod
    def _init_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(_SCHEMA)
        now = time.time()
        conn.execute(
            "INSERT OR IGNORE INTO nodes(path,parent,name,kind,size,mode,mtime,ctime,atime,sha256) "
            "VALUES('/',NULL,'','dir',0,?,?,?,?,NULL)",
            (_DIR_MODE, now, now, now),
        )

    def close(self) -> None:
        self._db.close()

    # ------------------------------------------------------------------ reads
    async def get(self, path: str) -> NodeRow | None:
        def _fn(conn: sqlite3.Connection) -> NodeRow | None:
            record = conn.execute("SELECT * FROM nodes WHERE path=?", (path,)).fetchone()
            return _row(record) if record else None

        return await self._db.run(_fn)

    async def list_children(self, parent: str) -> list[NodeRow]:
        def _fn(conn: sqlite3.Connection) -> list[NodeRow]:
            rows = conn.execute("SELECT * FROM nodes WHERE parent=? ORDER BY name", (parent,)).fetchall()
            return [_row(r) for r in rows]

        return await self._db.run(_fn)

    async def subtree(self, root: str) -> list[NodeRow]:
        def _fn(conn: sqlite3.Connection) -> list[NodeRow]:
            prefix = (root.rstrip("/") + "/") + "%"
            rows = conn.execute(
                "SELECT * FROM nodes WHERE path=? OR path LIKE ? ORDER BY path",
                (root, prefix),
            ).fetchall()
            return [_row(r) for r in rows]

        return await self._db.run(_fn)

    # ----------------------------------------------------------------- writes
    async def put_file(self, path: str, sha256: str | None, size: int, *, mode: int) -> str | None:
        def _fn(conn: sqlite3.Connection) -> str | None:
            _ensure_parents(conn, path)
            existing = conn.execute("SELECT kind, ctime, sha256 FROM nodes WHERE path=?", (path,)).fetchone()
            if existing is not None and existing["kind"] == "dir":
                raise IsADirectoryError(path)
            old_sha = existing["sha256"] if existing is not None else None
            ctime = existing["ctime"] if existing is not None else time.time()
            now = time.time()
            gc_sha: str | None = None
            if old_sha != sha256:
                _incref(conn, sha256, size)
                if _decref(conn, old_sha):
                    gc_sha = old_sha
            conn.execute(
                "INSERT OR REPLACE INTO nodes(path,parent,name,kind,size,mode,mtime,ctime,atime,sha256) "
                "VALUES(?,?,?,'file',?,?,?,?,?,?)",
                (path, _parent_of(path), _name_of(path), size, mode, now, ctime, now, sha256),
            )
            return gc_sha

        return await self._db.run(_fn)

    async def delete_file(self, path: str) -> str | None:
        def _fn(conn: sqlite3.Connection) -> str | None:
            record = conn.execute("SELECT kind, sha256 FROM nodes WHERE path=?", (path,)).fetchone()
            if record is None:
                raise FileNotFoundError(path)
            if record["kind"] == "dir":
                raise IsADirectoryError(path)
            gc_sha = record["sha256"] if _decref(conn, record["sha256"]) else None
            conn.execute("DELETE FROM nodes WHERE path=?", (path,))
            return gc_sha

        return await self._db.run(_fn)

    async def remove_subtree(self, path: str) -> list[str]:
        def _fn(conn: sqlite3.Connection) -> list[str]:
            prefix = (path.rstrip("/") + "/") + "%"
            rows = conn.execute(
                "SELECT path, sha256 FROM nodes WHERE path=? OR path LIKE ?",
                (path, prefix),
            ).fetchall()
            gc: list[str] = []
            for record in rows:
                if record["sha256"] and _decref(conn, record["sha256"]):
                    gc.append(record["sha256"])
            conn.execute("DELETE FROM nodes WHERE path=? OR path LIKE ?", (path, prefix))
            return gc

        return await self._db.run(_fn)

    async def mkdirs(self, path: str, *, exist_ok: bool = True) -> None:
        def _fn(conn: sqlite3.Connection) -> None:
            existing = conn.execute("SELECT kind FROM nodes WHERE path=?", (path,)).fetchone()
            if existing is not None:
                if existing["kind"] != "dir":
                    raise FileExistsError(path)
                if not exist_ok:
                    raise FileExistsError(path)
                return
            _mkdirs_chain(conn, path)

        await self._db.run(_fn)

    async def mkdir(self, path: str) -> None:
        def _fn(conn: sqlite3.Connection) -> None:
            parent = _parent_of(path)
            if parent is not None and conn.execute("SELECT 1 FROM nodes WHERE path=?", (parent,)).fetchone() is None:
                raise FileNotFoundError(parent)
            if conn.execute("SELECT 1 FROM nodes WHERE path=?", (path,)).fetchone() is not None:
                raise FileExistsError(path)
            _insert_dir(conn, path)

        await self._db.run(_fn)

    async def rmdir(self, path: str) -> None:
        def _fn(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM nodes WHERE path=? AND kind='dir'", (path,))

        await self._db.run(_fn)

    async def rename(self, src: str, dst: str) -> None:
        def _fn(conn: sqlite3.Connection) -> None:
            src_node = conn.execute("SELECT path FROM nodes WHERE path=?", (src,)).fetchone()
            if src_node is None:
                raise FileNotFoundError(src)
            if conn.execute("SELECT 1 FROM nodes WHERE path=?", (dst,)).fetchone() is not None:
                raise FileExistsError(dst)
            _ensure_parents(conn, dst)
            prefix = (src.rstrip("/") + "/") + "%"
            rows = conn.execute(
                "SELECT path FROM nodes WHERE path=? OR path LIKE ? ORDER BY length(path)",
                (src, prefix),
            ).fetchall()
            for record in rows:
                old = record["path"]
                new = dst if old == src else dst + old[len(src) :]
                conn.execute(
                    "UPDATE nodes SET path=?, parent=?, name=? WHERE path=?",
                    (new, _parent_of(new), _name_of(new), old),
                )

        await self._db.run(_fn)


# --------------------------------------------------------------------------- #
# Synchronous helpers (run inside a transaction by the caller)
# --------------------------------------------------------------------------- #
def _incref(conn: sqlite3.Connection, sha256: str | None, size: int) -> None:
    if sha256 is None:
        return
    conn.execute(
        "INSERT INTO blob_refs(sha256,refcount,size) VALUES(?,1,?) "
        "ON CONFLICT(sha256) DO UPDATE SET refcount=refcount+1",
        (sha256, size),
    )


def _decref(conn: sqlite3.Connection, sha256: str | None) -> bool:
    """Decrement a blob's refcount; return True if it reached zero (delete it)."""
    if sha256 is None:
        return False
    record = conn.execute("SELECT refcount FROM blob_refs WHERE sha256=?", (sha256,)).fetchone()
    if record is None:
        return False
    remaining = record["refcount"] - 1
    if remaining <= 0:
        conn.execute("DELETE FROM blob_refs WHERE sha256=?", (sha256,))
        return True
    conn.execute("UPDATE blob_refs SET refcount=? WHERE sha256=?", (remaining, sha256))
    return False


def _insert_dir(conn: sqlite3.Connection, path: str) -> None:
    now = time.time()
    conn.execute(
        "INSERT INTO nodes(path,parent,name,kind,size,mode,mtime,ctime,atime,sha256) "
        "VALUES(?,?,?,'dir',0,?,?,?,?,NULL)",
        (path, _parent_of(path), _name_of(path), _DIR_MODE, now, now, now),
    )


def _mkdirs_chain(conn: sqlite3.Connection, path: str) -> None:
    current = ""
    for part in [segment for segment in path.strip("/").split("/") if segment]:
        current = f"{current}/{part}"
        node = conn.execute("SELECT kind FROM nodes WHERE path=?", (current,)).fetchone()
        if node is None:
            _insert_dir(conn, current)
        elif node["kind"] != "dir":
            raise FileExistsError(current)


def _ensure_parents(conn: sqlite3.Connection, path: str) -> None:
    parent = _parent_of(path)
    if parent is not None and parent != "/":
        _mkdirs_chain(conn, parent)
