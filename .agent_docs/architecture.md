# Architecture

## Layers

```
fs_tools/*  +  admin_tools  (the MCP surface; identical to mcp-juicefs)
        │  talk only to ToolContext + VolumeClient + AdminBackend
        ▼
ToolContext  (config, store=AdminBackend, manager=StoreManager, safety)
        │
        ▼
VolumeClient  (async filesystem facade, src/mcp_fs/volume.py)
   ├── MetaBackend   (the directory tree + blob reference counts)
   └── BlobBackend   (content addressed bytes, keyed by sha256)
```

The whole point of the fork: the tools never knew about JuiceFS, they only knew
`VolumeClient`. We reimplemented `VolumeClient` on top of two pluggable backends
and left everything above untouched.

## Storage model

Two concerns are split into two independently pluggable backends.

### MetaBackend (v1: SqliteMetaStore, one db file per volume)

Two tables in `state/volumes/<mount_id>.db`:

```sql
nodes(path PK, parent, name, kind, size, mode, mtime, ctime, atime, sha256)
blob_refs(sha256 PK, refcount, size)
```

* `nodes` is the directory tree, keyed by normalized path. `kind` is `dir` or
  `file`; `mode` carries the POSIX type bits so `fs.stat` returns a real
  `os.stat_result`. A file node points at a content `sha256` (NULL for an empty
  file or a directory). The root `/` is created at provision time.
* `blob_refs` reference counts the shas so the volume deduplicates identical
  content and knows when a blob is safe to delete.
* WAL mode; every access is serialized and offloaded with `asyncio.to_thread`
  (SQLite is synchronous, like the old JuiceFS SDK, so the upper layer that
  already used `to_thread` is reused unchanged).

### BlobBackend (v1: MinioBlobStore, one bucket per volume)

A deliberately dumb content addressed key value store, bucket `mcpfs-<mount_id>`,
objects keyed by sha256. `put` / `get(offset,length)` / `exists` / `delete` plus
`ensure_bucket` / `remove_bucket`. Partial reads use S3 `Range`.

### AdminBackend (v1: SqliteAdminStore, one shared db file)

`project` and `project_member` tables in `state/admin.db`. Authorization is by
person: platform admin (in `auth.admins`) > owner > member.

## Write / read / copy / delete flow

`VolumeClient` orchestrates the dance between the two backends:

| Operation | MetaBackend | BlobBackend |
|-----------|-------------|-------------|
| write     | upsert node, incref new sha, decref old sha (transactional) | `put(sha, data)` (idempotent dedup) |
| read      | look up node, get its sha | `get(sha, offset, length)` (Range) |
| copy      | new node, same sha, incref | nothing (zero bytes copied) |
| delete    | remove node, decref sha | `delete(sha)` only when refcount hit 0 |
| rename    | rewrite the path of the node and all descendants | nothing |

Ordering guarantees no dangling metadata: write puts the blob **before** the
node; delete drops the node, then the blob.

## The "should we chunk files into blocks?" question

No fixed size byte chunking in v1. Reasoning:

* The fast partial read comes from **S3 Range** requests, not manual chunks.
* Deduplication comes from **content addressing** (one object per sha256).
* The expensive operations (`grep`, `glob`) parallelize at the **file** level
  (the metadata store filters candidates first), not below the file.
* Line oriented reads (`head`, `tail`, `read_lines`, `count_lines`) want line
  boundaries; fixed byte blocks would fight them for no payoff at code / config
  file sizes (KB to low MB).

Documented v2 optimizations, none required for correctness: a cached line offset
index in `nodes` for O(1) line seeks, and SQLite FTS5 / Postgres `pg_trgm` behind
a `search_text` backend method for instant literal grep.

## Safety contract (src/mcp_fs/safety.py)

Per `(person, mount_id)` in memory session state, identical to mcp-juicefs:
path normalization (no escape, no NUL), must read before write, per session
write quota, an audit log, and a trash path helper. `fs.delete` soft deletes to
`.mcp_trash/` by default; hard delete is gated by `safety.allow_hard_delete`.

## Request lifecycle

`IdentityMiddleware` (pure ASGI) resolves the person from `X-Forwarded-User`
(debug) or a signed JWT (production) and binds it to a `ContextVar`. Each tool
calls `ctx.authorize(mount_id)` which checks project membership before returning
the `VolumeClient`. A wrong `mount_id` yields `ERR_FORBIDDEN`, so letting the
model choose the mount is safe as long as the identity is correct.
