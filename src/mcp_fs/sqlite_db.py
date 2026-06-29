"""Tiny async wrapper over a synchronous ``sqlite3`` connection.

SQLite is synchronous; every access runs in :func:`asyncio.to_thread` and is
serialized with a thread lock. The connection uses WAL mode so the file stays
consistent and readable by external tools. One :class:`SqliteDb` wraps one file
and is shared by the metadata store and the admin store implementations.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_T = TypeVar("_T")


class SqliteDb:
    """A single WAL-mode SQLite connection, accessed from async code."""

    __slots__ = ("_conn", "_lock", "_path")

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def run_sync(self, fn: Callable[[sqlite3.Connection], _T]) -> _T:
        """Run ``fn`` inside a transaction (commit on success, rollback on error)."""
        with self._lock, self._conn:
            return fn(self._conn)

    async def run(self, fn: Callable[[sqlite3.Connection], _T]) -> _T:
        """Async variant of :meth:`run_sync` offloaded to a worker thread."""
        return await asyncio.to_thread(self.run_sync, fn)

    def close(self) -> None:
        """Close the underlying connection."""
        with self._lock:
            self._conn.close()
