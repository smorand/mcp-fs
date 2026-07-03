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
| GET | `/{mount}/read` | `?path=&offset_lines=&limit_lines=&line_numbered=` | line-numbered, paged text read |
| GET | `/{mount}/read-bytes` | `?path=&offset=&length=` | raw bytes as base64 + MIME |
| GET | `/{mount}/stat` | `?path=` | POSIX metadata |
| GET | `/{mount}/exists` | `?path=` | existence + kind |
| GET | `/{mount}/hash` | `?path=&algo=` | content hash (md5/sha1/sha256/sha512) |
| GET | `/{mount}/count-lines` | `?path=` | line count |
| GET | `/{mount}/glob` | `?pattern=&root=&exclude_patterns=` | find files by glob, newest first |
| GET | `/{mount}/grep` | `?pattern=&root=&output_mode=&...` | search contents (files/content/count) |
| POST | `/{mount}/copy` | `{source, destination, overwrite, recursive}` | copy a file or tree |
| POST | `/{mount}/extract-text` | `{path, max_chars, preview_chars, ocr, refresh}` | extract to a `.md` companion; returns `{md_path, preview, cached}` |
| POST | `/{mount}/write-docx` | `{path, markdown, title, overwrite}` | render Markdown to a `.docx` |
| GET | `/{mount}/audit-log` | `?since=&limit=` | recent session mutations |
| GET | `/{mount}/read-lines` | `?path=&start_line=&end_line=` | inclusive line range |
| GET | `/{mount}/read-section` | `?path=&anchor_line=&max_lines=` | indentation block around a line |
| GET | `/{mount}/head` / `/{mount}/tail` | `?path=&lines=` | first / last N lines |
| POST | `/{mount}/read-many` | `{paths, per_file_cap_lines}` | batch read, per-file error isolation |
| GET | `/{mount}/tree` | `?path=&max_depth=&exclude_patterns=&with_sizes=` | recursive JSON tree |
| POST | `/{mount}/write` | `{path, content, overwrite, create_parents}` | create/overwrite a text file |
| POST | `/{mount}/append` | `{path, content, create}` | append to a file |
| POST | `/{mount}/create-empty` | `{path, exist_ok}` | touch an empty file |
| POST | `/{mount}/edit` | `{path, old_string, new_string, replace_all, dry_run}` | replace a unique string |
| POST | `/{mount}/multi-edit` | `{path, edits[], dry_run}` | several edits atomically |
| POST | `/{mount}/search-replace` | `{path, search_block, replace_block, fuzzy}` | replace a multi-line block |
| POST | `/{mount}/insert-at-line` | `{path, line, content}` | insert before a 1-based line |
| POST | `/{mount}/apply-patch` | `{patch_text}` | apply a multi-file V4A patch |
| GET | `/{mount}/find-definition` | `?name=&root=&kind=` | tree-sitter symbol definition |
| GET | `/{mount}/find-references` | `?name=&root=` | tree-sitter identifier references |

Everything below the zip line mirrors the MCP `fs.*` tools: both planes are thin
adapters over the same `fs_ops` module and the same `VolumeClient`, so the API
and the agent tools are **iso** across all 33 `fs.*` operations. Only the
`admin.*` project/member operations stay off the data plane (they have their own
surface and auth). A downstream tool (e.g. a docx generator) can therefore do
everything over the API: read variants, write/edit/patch, extract, render.

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
