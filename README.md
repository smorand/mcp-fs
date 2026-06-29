# mcp-fs

A pure Python **MCP (streamable HTTP)** server that exposes a **simulated multi
project filesystem** as a surface of `fs.*` tools, with **no FUSE mount and no
JuiceFS**. It is the deployment friendly sibling of `mcp-juicefs`: same tool
surface and same safety contract, but the storage layer is two small, pluggable
backends instead of `libjfs.so` + PostgreSQL + MinIO + Docker.

* **Metadata** (the directory tree): **SQLite**, one database file per volume.
* **Blobs** (file contents): **MinIO / S3**, content addressed by sha256.
* **ACL** (projects and members): **SQLite**, one shared database file.

The only running service required is an S3 compatible object store (MinIO). The
metadata and ACL live in local SQLite files. No PostgreSQL, no native libraries.

## Why

`mcp-juicefs` is mature but hard to deploy in some environments: it needs
`libjfs.so` (Linux only), a Docker runtime, PostgreSQL and MinIO. `mcp-fs` keeps
the **entire upper layer unchanged** (the 31 `fs.*` tools, the safety contract,
identity and authorization, tree sitter code search) and only swaps the storage
seam underneath.

## Architecture

```
HTTP streamable (/mcp) ── identity middleware (JWT | X-Forwarded-User) ── contextvar(person)
                                       │
   fs.read(mount_id, …)                │     admin.create_project(…)
        │ authz: person ∈ members      │          │ authz: person == owner / admin
        ▼                              │          ▼
   StoreManager.get_client(mount_id)   │     SqliteAdminStore (projects + members)
        │                              │
        ▼
   VolumeClient
     ├── MetaBackend  → SqliteMetaStore   (state/volumes/<id>.db : nodes + blob_refs)
     └── BlobBackend  → MinioBlobStore     (bucket mcpfs-<id> : objects keyed by sha256)
```

A file is stored by content: writing puts the blob (deduplicated by sha256) then
upserts the metadata node; copying is a metadata only operation (it references
the same blob); deleting drops the node, then the blob once its reference count
reaches zero. Partial reads use S3 `Range` requests, so there is no manual block
chunking.

## Pluggable backends

The storage layer is three Protocols (`src/mcp_fs/protocols.py`). To add a
backend (a PostgreSQL `MetaBackend`, a local filesystem `BlobBackend`, ...),
implement the Protocol and register it in `src/mcp_fs/backends.py`. Nothing in
`fs_tools/` or `admin_tools.py` changes. The `backend` field in `config.yaml`
selects the implementation.

## Quick start

```bash
make sync                                   # install dependencies (uv)
make test                                   # unit + functional tests (no live stack)
make run                                    # serve /mcp and /health on :8080

curl localhost:8080/health                  # {"status":"ok","version":"..."}
```

The default `config/local.yaml` points blobs at the colima MinIO
(`127.0.0.1:9000`) and writes SQLite files under `state/`.

## Configuration

The active YAML file is selected by environment variables (serverless friendly):

* `MCP_FS_CONFIG` : absolute path to a YAML file, or
* `MCP_FS_CONFIG_DIR` + `MCP_FS_CONFIG_NAME` : `<dir>/<name>.yaml`.

`--config` on the CLI overrides both. See `config/local.yaml` for the full schema
(auth, infra.meta, infra.blob, infra.admin, safety).

## Authentication

Identity is resolved per request, exactly like `mcp-juicefs`:

* **debug** (local): trust the `X-Forwarded-User: <person>` header.
* **jwt** (production): verify a signed RS256 bearer token, read the
  `preferred_username` claim. The bearer header is configurable.

The resolved person is matched against the project ACL: platform admin (in
`auth.admins`) > owner > member.

## Tools

37 tools total: 31 `fs.*` (read, write, edit, search, list, metadata, lifecycle,
plus tree sitter `find_definition` / `find_references`) and 6 `admin.*` (project
and membership management). Every `fs.*` tool takes a `mount_id`. See
`.agent_docs/tools.md` for the full reference.

## Live integration (real SQLite + MinIO)

```bash
make test-integration        # MCP_FS_INTEGRATION=1, needs MinIO reachable
```

This provisions a real volume (SQLite file + MinIO bucket), writes / reads /
greps / copies / deletes through the real tool surface, then tears it down.

## Consuming from an agent (config-a2a)

`mcp-fs` speaks the same MCP `fs.*` surface and identity contract as
`mcp-juicefs`, so it is a drop in for any consumer. `config-examples/mcp-fs/`
contains a runnable `config-a2a` server that attaches an agent to this server's
`/mcp` endpoint and forwards the end user identity per request. See
`.agent_docs/integration.md`.

## Development

```bash
make check        # lint + format + mypy strict + bandit + tests (coverage >= 80%)
```

## Documentation

* `CLAUDE.md` : compact index for AI agents.
* `.agent_docs/architecture.md` : storage model, write / read / delete flow, safety.
* `.agent_docs/tools.md` : reference of the 37 MCP tools.
* `.agent_docs/backends.md` : how to add a metadata, blob or ACL backend.
* `.agent_docs/integration.md` : consuming mcp-fs from an agent (config-a2a).
