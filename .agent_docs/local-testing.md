# Local testing (moto server, Windows friendly)

mcp-fs talks to its blob store through aioboto3, so **any S3 emulator works**.
Where MinIO is not available (a locked-down Windows host, for example), use
**moto server**, a pure Python S3 emulator shipped as a dev dependency. No native
libraries, no Docker, no MinIO.

All commands are `uv` (no `make`) and cross-platform. On Windows use the Python
helper scripts (`scripts/*.py`), not the shell ones (`scripts/*.sh`).

## Prerequisites (once)

```bash
uv sync                                   # installs moto[server] too (dev group)
uv run python scripts/gen_jwt_keys.py     # writes .keys/ (private, public, service token)
```

`gen_jwt_keys.py` also copies the keys into sibling repos (`web-a2a`, `config-a2a`)
when they are present, so the same command sets up the whole chain.

## The moto profile

`config/moto.yaml` is `config/local.yaml` with the blob endpoint pointed at a moto
server and dummy credentials. Use the IP `127.0.0.1` (not `localhost`) so boto3
selects path-style addressing, which moto expects. moto keeps objects in memory
(they are lost when it restarts); the SQLite metadata under `state/` persists.

## Ordered run (three terminals)

```bash
# 1) S3 emulator
uv run moto_server -p 5000

# 2) mcp-fs on the moto profile
uv run mcp-fs serve --config config/moto.yaml            # /mcp and /health on :8080

# 3) end-to-end smoke test (mints tokens locally, drives real HTTP)
uv run python scripts/smoke.py http://127.0.0.1:8080 seb.morand@gmail.com
```

Expected: every line `PASS`, then `ALL OK`. The smoke test covers `/health`,
`admin.create_project`, `fs.write`, `fs.read`, caseless identity, the two
anti-bypass 401s (no token; `X-Forwarded-User` alone), and the outsider
`ERR_FORBIDDEN`.

## Manual calls

Mint a token, then send it as `X-Forwarded-Authorization: Bearer <token>`:

```bash
uv run python scripts/mint_token.py seb.morand@gmail.com
```

## The rest of the chain

config-a2a and web-a2a do not use S3, so the moto swap does not affect them. For
the full chain (web-a2a to config-a2a to mcp-fs), see `.agent_docs/integration.md`
and the identity docs in those repos; only mcp-fs's blob endpoint changes here.
