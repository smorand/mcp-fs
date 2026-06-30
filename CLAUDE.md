# mcp-fs

## Overview
Pure Python **MCP streamable HTTP** server exposing a **simulated multi project
filesystem** (`fs.*` tools), **no FUSE, no JuiceFS**. Forked from `mcp-juicefs`:
identical tool surface and safety contract, but the storage seam is two small
pluggable backends. Metadata tree = **SQLite** (one db file per volume), file
bytes = **MinIO / S3** content addressed by sha256, ACL (projects + members) =
**SQLite**. Only running service required: an S3 store (MinIO). No PostgreSQL, no
native libraries. Stack: Python 3.13+, FastAPI, FastMCP, aioboto3, tree-sitter.

## Key commands
```
make sync              # install deps (uv)
make serve             # uv run mcp-fs serve --config config/local.yaml
make test-cov          # unit + functional tests, coverage >= 80% (no live stack)
make test-integration  # MCP_FS_INTEGRATION=1, real SQLite + MinIO
make check             # full gate: lint, format-check, mypy strict, bandit, test-cov
make docker-build      # build the Docker image
```
`make` is the single interface for all operations; see `.agent_docs/makefile.md`.

## Project structure
- `src/mcp_fs/mcp_fs.py` : Typer entry (`serve`, `version`).
- `config.py` : Settings (`MCP_FS_*`) + path helpers (`volume_meta_path`, `volume_bucket`, `admin_db_path`).
- `models.py` : config (pydantic), domain (Project/Member), `ErrorCode`/`ToolError`.
- `protocols.py` : `MetaBackend`, `BlobBackend`, `AdminBackend` Protocols + `NodeRow`.
- `backends.py` : factories selecting the implementation from the `backend` field.
- `sqlite_db.py` : tiny async wrapper over a WAL sqlite3 connection.
- `sqlite_meta.py` : `SqliteMetaStore` (nodes tree + blob_refs reference counts).
- `sqlite_admin.py` : `SqliteAdminStore` (projects + members).
- `minio_blob.py` : `MinioBlobStore` (content addressed objects, S3 Range reads).
- `volume.py` : `VolumeClient` composing meta + blob (the fs_tools contract).
- `manager.py` : `StoreManager` (cache clients, provision / teardown volumes).
- `safety.py` : path normalization, read before write, quota, audit, trash.
- `identity.py` / `authz` (in sqlite_admin) : verified RS256 bearer (X-Forwarded-Authorization), owner/members.
- `context.py` : `ToolContext` (services injected into each tool).
- `fs_tools/` : read, write, edit (+ `patch_v4a`), search, listing, metadata, lifecycle.
- `treesitter.py` : find_definition / find_references.
- `admin_tools.py` : create/delete/list projects, add/remove/list members.
- `server.py` : `build_app` (FastMCP `/mcp` + FastAPI `/health` + identity middleware + lifespan).

## Conventions
- `mount_id` = required parameter on **every** `fs.*` tool. Errors = stable `ErrorCode` (`ERR_*`).
- SQLite is **synchronous**: always go through `SqliteDb.run` (`asyncio.to_thread`); never block the loop.
- Files are **content addressed**: write puts the blob then the node; copy is metadata only; delete GCs the blob at refcount 0.
- Backend choice via config `backend` field; add a backend by implementing a Protocol + a branch in `backends.py`.
- Config via `Settings`, never `os.environ` directly.

## Quality gate
`make check` before any commit: lint, format, mypy strict, bandit, tests + coverage >= 80%.
Integration tests (live MinIO) are deselected unless `MCP_FS_INTEGRATION=1`.

## Coding standards
This project follows the `python` skill. Reload it for the full reference.

## Documentation index
- `.agent_docs/python.md` : Python coding standards (from the `python` skill).
- `.agent_docs/makefile.md` : Makefile targets and developer interface.
- `.agent_docs/architecture.md` : storage model, write/read/delete flow, the blocks question, safety.
- `.agent_docs/tools.md` : reference of the 39 MCP tools (31 `fs.*` + 8 `admin.*`).
- `.agent_docs/backends.md` : how to add a metadata, blob or ACL backend.
- `.agent_docs/authorization.md` : how identity is received and verified, the ACL model (admin/owner/member), caseless email matching, managing authorization.
- `.agent_docs/integration.md` : consuming mcp-fs from an agent (config-a2a), identity, mount_id.
