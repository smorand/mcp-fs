# Integration: consuming mcp-fs from an agent

mcp-fs exposes the **same MCP `fs.*` surface and the same identity contract** as
`mcp-juicefs`, so any consumer of mcp-juicefs is a drop in: point it at the
mcp-fs `/mcp` endpoint. mcp-fs itself adds nothing consumer specific.

## Identity propagation (required)

Every request to `/mcp` must carry the identity of the **person** being acted
for, as a **verified RS256 bearer token** on `X-Forwarded-Authorization`
(`Bearer <jwt>`, claim `email`, issuer `web-a2a`). There is no trust header: a
missing or invalid token yields 401, and mcp-fs verifies the signature with the
public key (no bare decode). mcp-fs derives rights from the email claim (platform
admin > owner > member).

The consumer sets this header **per request** (per end user), never a static
shared value. The upstream signer (web-a2a, or later a real IdP) holds the
private key; mcp-fs and any intermediate gateway (config-a2a) only verify with
the public key. Multi-issuer verification against Azure AD / Google JWKS is
tracked in `specs/BACKLOG.md`.

## mount_id (a user usually has several volumes)

`mount_id` is an explicit parameter of every `fs.*` tool: the choice belongs to
the user/agent. Discovery is via `fs.list_allowed_roots` (returns exactly the
volumes accessible to the current identity). A consumer may also surface a
`default_mount_id` as the current project. Safety net: a wrong `mount_id` yields
`ERR_FORBIDDEN`, so letting the model pick is safe as long as the identity is
correct.

## config-a2a example (the simulation)

`config-a2a` already has a native `juicefs:` agent block that desugars into a
streamable HTTP MCP server with per request identity forwarding. Because mcp-fs
speaks the identical surface, that block works pointed at mcp-fs. A runnable
example lives in `config-examples/mcp-fs/`:

```bash
# 1. start mcp-fs (this repo)
make serve                                # serves /mcp on :8080

# 2. point config-a2a at it and validate the wiring (config-a2a verifies the
#    bearer and re-forwards it; a service token covers tool discovery)
cd ../config-a2a
MCP_FS_URL=http://localhost:8080/mcp \
MCP_FS_DEFAULT_MOUNT_ID=demo-ei \
  uv run agent --config config_examples/09-juicefs/agents-jwt.yaml --check
```

`--check` loads the config, desugars the `juicefs:` block into an MCP server
reference and validates it without serving. To actually chat with the agent
(needs `OPENROUTER_API_KEY`), drop `--check`; web-a2a (or any gateway) signs a
per-user RS256 token, config-a2a verifies it and re-forwards the same Bearer to
mcp-fs on every `fs.*` call.

## Verified end to end

The HTTP path was exercised live against this repo's server and the colima MinIO:
`tools/list` (39 tools), 401 without identity, `admin.create_project` (provisioned
a SQLite db plus a `mcpfs-<id>` bucket), `fs.write` (the blob landed in MinIO
keyed by its sha256), `fs.read`, content dedup (two paths, one object), and
`admin.delete_project` (bucket removed). See `tests/integration/test_live_stack.py`
for the automated version (`make test-integration`).
