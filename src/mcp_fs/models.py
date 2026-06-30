"""Pydantic models: server configuration, domain entities, and tool error codes.

The storage layer is split into three independently pluggable backends, each
selected by a ``backend`` discriminator so new ones can be added without touching
the tool surface:

* ``infra.meta``  : the per-volume directory tree (v1: ``sqlite``).
* ``infra.blob``  : content-addressed file bytes (v1: ``minio`` / ``s3``).
* ``infra.admin`` : the ACL store of projects and members (v1: ``sqlite``).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Error codes (stable identifiers returned to the MCP client)
# --------------------------------------------------------------------------- #
class ErrorCode(StrEnum):
    """Stable error identifiers surfaced through MCP tool errors."""

    UNAUTHENTICATED = "ERR_UNAUTHENTICATED"
    FORBIDDEN = "ERR_FORBIDDEN"
    PROJECT_NOT_FOUND = "ERR_PROJECT_NOT_FOUND"
    PROJECT_EXISTS = "ERR_PROJECT_EXISTS"
    PATH_OUT_OF_BOUNDS = "ERR_PATH_OUT_OF_BOUNDS"
    EDIT_WITHOUT_PRIOR_READ = "ERR_EDIT_WITHOUT_PRIOR_READ"
    NO_CLOBBER = "ERR_NO_CLOBBER"
    NOT_FOUND = "ERR_NOT_FOUND"
    AMBIGUOUS_MATCH = "ERR_AMBIGUOUS_MATCH"
    NO_MATCH = "ERR_NO_MATCH"
    WRITE_QUOTA_EXCEEDED = "ERR_WRITE_QUOTA_EXCEEDED"
    INVALID_ARGUMENT = "ERR_INVALID_ARGUMENT"
    NOT_SUPPORTED = "ERR_NOT_SUPPORTED"


class ToolError(Exception):
    """Raised by tools and middleware; carries a stable :class:`ErrorCode`."""

    __slots__ = ("code", "message")

    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code.value}: {message}")


# --------------------------------------------------------------------------- #
# Configuration models (loaded from YAML)
# --------------------------------------------------------------------------- #
class JwtConfig(BaseModel):
    """Signed-JWT verification parameters (RS256 signature checked, claim read).

    The signature is verified with the public key; this is real verification, not
    a bare decode. The token is minted upstream by the holder of the private key.
    """

    public_key_path: str
    # The gateway consumes the standard Authorization header for itself, so the
    # forwarded end-user token rides X-Forwarded-Authorization.
    header: str = "X-Forwarded-Authorization"
    algorithms: list[str] = Field(default_factory=lambda: ["RS256"])
    audience: str | None = None
    issuer: str | None = "web-a2a"
    username_claim: str = "email"


class AuthConfig(BaseModel):
    """Authentication configuration. Identity is a verified RS256 bearer token only."""

    jwt: JwtConfig
    # Platform admins (by identity = the JWT username/email claim). Admins can create
    # projects (designating any owner), list every project/user, and act on any project.
    admins: list[str] = Field(default_factory=list)


class SqliteMetaConfig(BaseModel):
    """Metadata backend: per-volume SQLite database files under ``dir``."""

    backend: Literal["sqlite"] = "sqlite"
    # Directory holding one ``<project_id>.db`` per volume.
    dir: str = "state/volumes"


class BlobConfig(BaseModel):
    """Blob backend: content-addressed object storage (MinIO / S3 compatible)."""

    backend: Literal["minio", "s3"] = "minio"
    endpoint: str
    access_key: str
    secret_key: str
    bucket_prefix: str = "mcpfs-"
    region: str = "us-east-1"


class AdminConfig(BaseModel):
    """ACL backend: a single SQLite database for projects and memberships."""

    backend: Literal["sqlite"] = "sqlite"
    path: str = "state/admin.db"


class InfraConfig(BaseModel):
    """Pluggable storage backends used to provision and open volumes."""

    meta: SqliteMetaConfig = Field(default_factory=SqliteMetaConfig)
    blob: BlobConfig
    admin: AdminConfig = Field(default_factory=AdminConfig)


class SafetyConfig(BaseModel):
    """Safety-contract knobs (rapport mcp-files.md section 4)."""

    write_quota_bytes: int = 50 * 1024 * 1024
    trash_dir: str = ".mcp_trash"
    read_guard: bool = True
    allow_hard_delete: bool = False
    max_read_lines: int = 2000


class HttpConfig(BaseModel):
    """HTTP transport binding."""

    host: str = "0.0.0.0"  # nosec B104 - container binds all interfaces by design
    port: int = 8080
    mcp_path: str = "/mcp"


class ServerConfig(BaseModel):
    """Top-level server configuration (the parsed YAML document)."""

    server: HttpConfig = Field(default_factory=HttpConfig)
    auth: AuthConfig
    infra: InfraConfig
    safety: SafetyConfig = Field(default_factory=SafetyConfig)


# --------------------------------------------------------------------------- #
# Domain entities
# --------------------------------------------------------------------------- #
class Role(StrEnum):
    """Membership role within a project."""

    OWNER = "owner"
    MEMBER = "member"


class Project(BaseModel):
    """A managed volume (one metadata database + one blob bucket)."""

    id: str
    owner: str
    created_at: str


class Member(BaseModel):
    """A person authorized on a project."""

    project_id: str
    person: str
    role: Role
    added_by: str
    added_at: str
