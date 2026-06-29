"""Safety contract (rapport mcp-files.md section 4).

Path normalization keeps every operation inside the volume root (the metadata
store is itself jailed to the volume). We keep: path normalization,
must-read-before-write, per-session write quota, an audit log, and a trash path
helper. Session state is in-memory and keyed by ``(person, project_id)``.
"""

from __future__ import annotations

import posixpath
import time
from collections import deque
from dataclasses import dataclass, field

from mcp_fs.models import ErrorCode, SafetyConfig, ToolError

_AUDIT_CAP = 500


@dataclass(slots=True)
class AuditEntry:
    """A single recorded mutation."""

    timestamp: float
    op: str
    path: str
    detail: str


@dataclass(slots=True)
class SessionState:
    """Per ``(person, project)`` in-memory state."""

    read_paths: set[str] = field(default_factory=set)
    bytes_written: int = 0
    audit: deque[AuditEntry] = field(default_factory=lambda: deque(maxlen=_AUDIT_CAP))


class SafetyManager:
    """Enforces the safety contract and tracks per-session state."""

    __slots__ = ("_config", "_sessions")

    def __init__(self, config: SafetyConfig) -> None:
        self._config = config
        self._sessions: dict[tuple[str, str], SessionState] = {}

    @property
    def config(self) -> SafetyConfig:
        return self._config

    def session(self, person: str, project_id: str) -> SessionState:
        """Return (creating if needed) the session state for a person on a project."""
        key = (person, project_id)
        state = self._sessions.get(key)
        if state is None:
            state = SessionState()
            self._sessions[key] = state
        return state

    def normalize_path(self, path: str) -> str:
        """Return an absolute, normalized in-volume path or raise ``ERR_PATH_OUT_OF_BOUNDS``."""
        if "\x00" in path:
            raise ToolError(ErrorCode.PATH_OUT_OF_BOUNDS, "path contains a NUL byte")
        candidate = path if path.startswith("/") else f"/{path}"
        normalized = posixpath.normpath(candidate)
        if not normalized.startswith("/") or normalized.startswith("/.."):
            raise ToolError(ErrorCode.PATH_OUT_OF_BOUNDS, f"path escapes the volume root: {path}")
        return normalized

    def record_read(self, person: str, project_id: str, path: str) -> None:
        """Mark ``path`` as read in the session (enables later edits)."""
        self.session(person, project_id).read_paths.add(path)

    def ensure_read_before_write(self, person: str, project_id: str, path: str) -> None:
        """Raise ``ERR_EDIT_WITHOUT_PRIOR_READ`` if ``path`` was not read this session."""
        if not self._config.read_guard:
            return
        if path not in self.session(person, project_id).read_paths:
            raise ToolError(
                ErrorCode.EDIT_WITHOUT_PRIOR_READ,
                f"edit '{path}' requires reading it first in this session",
            )

    def charge_write(self, person: str, project_id: str, num_bytes: int) -> None:
        """Account ``num_bytes`` against the session quota or raise."""
        state = self.session(person, project_id)
        if state.bytes_written + num_bytes > self._config.write_quota_bytes:
            raise ToolError(
                ErrorCode.WRITE_QUOTA_EXCEEDED,
                f"session write quota of {self._config.write_quota_bytes} bytes exceeded",
            )
        state.bytes_written += num_bytes

    def record_audit(self, person: str, project_id: str, op: str, path: str, detail: str = "") -> None:
        """Append a mutation to the session audit log."""
        self.session(person, project_id).audit.append(
            AuditEntry(timestamp=time.time(), op=op, path=path, detail=detail)
        )

    def trash_path(self, path: str) -> str:
        """Return the in-volume destination for a soft-deleted ``path``."""
        flat = path.strip("/").replace("/", "__")
        return f"/{self._config.trash_dir}/{int(time.time() * 1000)}__{flat}"
