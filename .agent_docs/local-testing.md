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
uv run moto_server -p 5001

# 2) mcp-fs on the moto profile
uv run mcp-fs serve --config config/moto.yaml            # /mcp and /health on :5002

# 3) end-to-end smoke test (mints tokens locally, drives real HTTP)
uv run python scripts/smoke.py http://127.0.0.1:5002 seb.morand@gmail.com
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

## Full stack: web-a2a UI to config-a2a to mcp-fs (no MinIO, no Postgres)

The whole chain runs on SQLite plus moto: pure Python, no MinIO, no Postgres, no
Docker. Assumed layout: the three repos sit side by side (`../config-a2a`,
`../web-a2a`). Generate the shared keys once from mcp-fs (it distributes them):

```bash
uv run python scripts/gen_jwt_keys.py
```

Ports are sequential so nothing collides with common local apps (5000 stays free
for macOS AirPlay); `test-local.sh` / `test-local.bat` wire the same numbers:

| Service | Port | URL |
|---------|------|-----|
| moto (S3 emulator) | 5001 | `http://127.0.0.1:5001` |
| mcp-fs (`/mcp`, `/api/fs`, UI) | 5002 | `http://127.0.0.1:5002` |
| config-a2a agent | 5003 | `http://127.0.0.1:5003/agents/files` |
| web-a2a UI | 5004 | `http://localhost:5004` |

**Terminal 1, moto (S3 emulator):**
```bash
uv run moto_server -p 5001
```

**Terminal 2, mcp-fs on moto, then provision a project for your login email:**
```bash
uv run mcp-fs serve --config config/moto.yaml               # :5002
# once, in another shell (from the mcp-fs repo):
uv run python scripts/provision.py perso-seb seb.morand@gmail.com
```

**Terminal 3, config-a2a simple agent (needs the LLM key):**
```bash
cd ../config-a2a
OPENROUTER_API_KEY=<your key> uv run agent --config config_examples/mcp-fs-moto/agents.yaml
# serves the agent A2A endpoint at http://127.0.0.1:5003/agents/files
```
On another host (E-I) where the mcp-fs URL or the LLM endpoint/key differ, use
`agents.template.yaml` instead: it has `${...}` placeholders for `MCP_FS_URL`,
`MCP_FS_MOUNT`, `LLM_MODEL`, `LLM_BASE_URL` (plus the api-key env var), set them
in the environment or edit them inline.
```bash
MCP_FS_URL=http://<host>:5002/mcp MCP_FS_MOUNT=perso-seb \
LLM_MODEL=openrouter/auto LLM_BASE_URL=https://openrouter.ai/api/v1 OPENROUTER_API_KEY=<key> \
  uv run agent --config config_examples/mcp-fs-moto/agents.template.yaml
```

**Terminal 4, web-a2a UI on SQLite.** Set these in `web-a2a/.env` (change the
default DATABASE_URL from Postgres to SQLite):
```
AGENT_CHAT_DATABASE_URL=sqlite+aiosqlite:///./state/web-a2a.db
AGENT_CHAT_ENCRYPTION_KEY=<fernet key>
AGENT_CHAT_SECRET_KEY=change-me-local
AGENT_CHAT_A2A_JWT_SIGNING_KEY_PATH=./.keys/jwt.key
```
Generate the Fernet key with
`uv run python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`.
Then:
```bash
cd ../web-a2a
mkdir state                     # if missing (SQLite needs the directory)
uv run alembic upgrade head     # SQLite schema
uv run uvicorn agent_chat.app:app --host 0.0.0.0 --port 5004
```

**Browser (http://localhost:5004):**
1. Login with the email that owns the project (`seb.morand@gmail.com`).
2. Agents, add a remote agent by URL: `http://127.0.0.1:5003/agents/files` (auth: none).
   Use `127.0.0.1` (not the bind address `0.0.0.0`) and note the plural `/agents/`
   with an `s`. The agent card at that URL is public, so discovery needs no token.
3. Open a conversation with it and ask, for example, "list my files" or "read /notes.md".

Flow: web-a2a signs a per-user RS256 token, config-a2a verifies it and passes it
through, mcp-fs verifies it and applies the ACL (the login email must own or be a
member of the project, matched case-insensitively). An email off the ACL yields
`ERR_FORBIDDEN`.

Notes:
- Model: the agent uses `openrouter/auto`; a free model may not call tools
  reliably, so swap the `model` in `agents.yaml` if the agent does not act.
- config-a2a and web-a2a both run on SQLite here; no Postgres.
- The demo agent auto-approves destructive tools; set
  `confirmations.destructive_hint: prompt` in `agents.yaml` for a stricter run.
