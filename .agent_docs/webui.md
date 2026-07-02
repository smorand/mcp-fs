# Web UI and data-plane API

Optional. Enabled with `webui.enabled: true` in the config. When on, the same
FastAPI process that serves `/mcp` and `/health` also serves a small themed file
manager (the browser UI) and a REST **data plane** under `/api/fs`.

## Two planes, one storage

- **Agent plane** (`/mcp`, the 31 `fs.*` tools): an LLM manipulates existing
  content through tool calls. Not suited to uploading bytes.
- **Human / app plane** (`/api/fs` plus the UI): upload, download, browse and
  organize files. This is how you **bootstrap** a volume with real files.

Both go through the same `VolumeClient` (content addressed, deduplicated) and the
same project ACL. The UI is a thin client of the API.

## Authentication (two front doors)

- **Browser**: a declarative email login (no password) stored in a signed cookie
  (the sibling web-a2a pattern), signed with `webui.secret_key`.
- **Programmatic**: a Bearer JWT on `X-Forwarded-Authorization` (the same token
  the MCP surface verifies).

Every `/api/fs` endpoint resolves the identity (cookie or JWT), then checks
project membership (`require_member`, caseless email). A non-member gets `403`,
an unknown project `404`, no identity `401`, an out-of-bounds path `400`.

## API (`/api/fs`)

| Method | Path | Body / query | Effect |
|--------|------|--------------|--------|
| GET | `/roots` | | projects the caller can access `[{mount_id, owner}]` |
| GET | `/{mount}/list` | `?path=/` | entries `[{name, kind, size, mtime}]` (dirs first) |
| POST | `/{mount}/mkdir` | `{path}` | create a directory (parents ok) |
| POST | `/{mount}/delete` | `{path}` | delete a file, or a folder and its whole subtree |
| POST | `/{mount}/move` | `{source, destination}` | move / rename a file or a folder and its subtree |
| POST | `/{mount}/upload` | multipart `files[]`, `directory`, `paths[]` | upload files; per-file relative `paths` do a recursive folder upload |
| GET | `/{mount}/download` | `?path=` | download one file (right MIME, attachment) |
| GET | `/{mount}/download-zip` | `?path=/dir` | download a folder as a streamed zip |

## UI

One page: a project selector (only projects you may access), a breadcrumb file
browser, a toolbar (new folder, upload files, upload folder, download zip),
per-row actions (download, move / rename, delete), and drag-and-drop upload. Two
themes (`carbon`, `ei`) plus a dark-mode toggle, switchable at runtime; the
choice rides a cookie. The look reuses the Carbon `--cds-*` tokens (the theme CSS
is shared with web-a2a).

## Config

```yaml
webui:
  enabled: true
  secret_key: "set-a-strong-value"   # signs the session cookie
  theme: "carbon"                     # default theme: carbon | ei
  dark_mode: true
  session_ttl_seconds: 86400
  cookie_name: "mcpfs_session"
```

## Code

- `src/mcp_fs/dataplane.py`: the `/api/fs` router (`build_dataplane_router`),
  reusing `VolumeClient` and the ACL; converts `ToolError` to HTTP status codes.
- `src/mcp_fs/webui.py`: `mount_web(app, ctx)` wires static assets, the Jinja2
  pages (login, index, theme), the cookie/JWT identity dependency, and the router.
- `src/mcp_fs/templates/`, `src/mcp_fs/static/`: templates, theme CSS, and the JS.
- Wired in `server.py` before the catch-all MCP mount, only when `webui.enabled`.
