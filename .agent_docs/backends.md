# Adding a storage backend

The storage layer is three Protocols in `src/mcp_fs/protocols.py`. Each is chosen
at runtime by a `backend` discriminator in the config, resolved by a factory in
`src/mcp_fs/backends.py`. Adding a backend is: implement the Protocol, add one
branch to the factory, add the config shape. Nothing in `fs_tools/` or
`admin_tools.py` changes.

## The three seams

| Protocol | Scope | v1 implementation | Config |
|----------|-------|-------------------|--------|
| `MetaBackend` | per volume directory tree + blob refcounts | `SqliteMetaStore` | `infra.meta` |
| `BlobBackend` | content addressed bytes (sha256) | `MinioBlobStore` | `infra.blob` |
| `AdminBackend` | ACL (projects + members) | `SqliteAdminStore` | `infra.admin` |

`VolumeClient` (`volume.py`) composes a `MetaBackend` and a `BlobBackend`.
`StoreManager` (`manager.py`) builds and caches them per project and provisions /
tears down volumes. `build_app` (`server.py`) builds the `AdminBackend`.

## MetaBackend contract

```python
get(path) -> NodeRow | None
list_children(parent) -> list[NodeRow]
subtree(root) -> list[NodeRow]                    # used to build walk()
put_file(path, sha256, size, *, mode) -> str|None # returns a sha to GC, or None
delete_file(path) -> str | None                   # returns a sha to GC, or None
remove_subtree(path) -> list[str]                 # returns shas to GC
mkdirs(path, *, exist_ok=True); mkdir(path); rmdir(path); rename(src, dst)
close()
```

Reference counting lives here (it must be transactional with node changes): a
write increfs the new sha and decrefs the old one; the method returns any sha
whose count reached zero so `VolumeClient` can delete it from the blob store.

To add a **PostgreSQL** `MetaBackend`: reuse the same two tables (`nodes`,
`blob_refs`) under a schema `vol_<id>` (the convention from `../juicefs`), and
back the async methods with asyncpg instead of `SqliteDb`.

## BlobBackend contract

```python
put(sha256, data); get(sha256, offset=0, length=None) -> bytes
exists(sha256) -> bool; delete(sha256)
ensure_bucket(); remove_bucket()
```

Deliberately a dumb content addressed key value store, so a new backend is tiny.
To add a **local filesystem** blob backend (the zero service option), store each
object at `<root>/<sha256>` and implement `get` with `seek`/`read` for the range.
To add **GCS**, swap aioboto3 for the GCS client. No metadata logic lives here.

## AdminBackend contract

The methods used by `ToolContext` and `admin_tools` (see the Protocol). To add a
PostgreSQL ACL store, back the same `project` / `project_member` tables with
asyncpg.

## Wiring a new backend

1. Implement the Protocol in a new module.
2. Add a config model (a `backend` Literal plus its settings) under `InfraConfig`
   in `models.py`.
3. Add a branch in the matching `build_*` factory in `backends.py`.

That is the whole change surface. The in-memory fakes in `tests/conftest.py`
(`FakeVolume`, `FakeStore`) and `tests/test_sqlite_meta.py` (`InMemoryBlob`) are
themselves Protocol implementations and double as references.
