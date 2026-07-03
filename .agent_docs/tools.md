# MCP tools reference

41 tools: **33 `fs.*`** and **8 `admin.*`**. Every `fs.*` tool takes a required
`mount_id` (the target project). Tools return JSON dicts; errors carry a stable
`ErrorCode` (`ERR_*`). This surface is identical to `mcp-juicefs`; only the
storage backend differs.

## admin.* (projects and members)

Authority: **platform admin** (`auth.admins` in config) > **owner** > **member**.

| Tool | Args | Gate | Effect |
|------|------|------|--------|
| `admin.create_project` | `project_id, owner` | admin | create project + provision volume (SQLite db + MinIO bucket) |
| `admin.delete_project` | `project_id` | owner or admin | tear down the volume (delete db files + empty/remove bucket) + delete project |
| `admin.list_projects` | | authenticated | projects accessible to the caller |
| `admin.list_all_projects` | | admin | every project |
| `admin.list_users` | | admin | every known person (+ `is_admin` flag) |
| `admin.add_member` | `project_id, person` | owner or admin | add a person |
| `admin.remove_member` | `project_id, person` | owner or admin | remove a person (not the owner) |
| `admin.list_members` | `project_id` | member or admin | list members |

`project_id`: 3 to 32 chars, lowercase letters/digits/hyphens, alphanumeric bounds.

## fs.* Read (8)

| Tool | Args |
|------|------|
| `fs.read` | `path, offset_lines=0, limit_lines=2000, line_numbered=true` |
| `fs.read_bytes` | `path, offset_bytes=0, length_bytes=65536` (base64 + mime; uses an S3 Range read) |
| `fs.read_lines` | `path, start_line, end_line` (inclusive) |
| `fs.read_section` | `path, anchor_line, max_lines=200` (indentation block) |
| `fs.read_many` | `paths[], per_file_cap_lines=500` (per file error isolation) |
| `fs.head` / `fs.tail` | `path, lines=20` |
| `fs.count_lines` | `path` |

## fs.* Write (3)

| Tool | Args | Notes |
|------|------|-------|
| `fs.write` | `path, content, overwrite=false, create_parents=true` | no clobber + atomic; diff if overwrite |
| `fs.append` | `path, content, create=false` | |
| `fs.create_empty` | `path, exist_ok=false` | touch (stores no blob) |

## fs.* Edit (5)

| Tool | Args | Notes |
|------|------|-------|
| `fs.edit` | `path, old_string, new_string, replace_all=false, dry_run=false` | unique string guard; read before write |
| `fs.multi_edit` | `path, edits[], dry_run=false` | atomic (all or nothing) |
| `fs.search_replace` | `path, search_block, replace_block, fuzzy=false` | multi line block; fuzzy difflib |
| `fs.insert_at_line` | `path, line, content` | insertion |
| `fs.apply_patch` | `patch_text` | V4A multi file (Add/Update/Delete/Move) |

## fs.* Search (4)

| Tool | Args |
|------|------|
| `fs.glob` | `pattern, root="/", exclude_patterns=[]` (mtime desc, cap 100) |
| `fs.grep` | `pattern, root, include_glob, exclude_glob, regex=true, case_sensitive=true, output_mode=content\|files\|count, context_lines=0, max_matches=100` |
| `fs.find_definition` | `name, root="/", kind?` (tree sitter) |
| `fs.find_references` | `name, root="/"` (tree sitter) |

## fs.* List (2)

| Tool | Args |
|------|------|
| `fs.list_dir` | `path="/", include_hidden=false, sort_by=name\|size, with_sizes=false` |
| `fs.tree` | `path="/", max_depth=3, exclude_patterns=[], with_sizes=false` |

## fs.* Metadata (3)

| Tool | Args |
|------|------|
| `fs.stat` | `path` (size/mode/kind/mtime/ctime/atime/uid/gid) |
| `fs.exists` | `path` |
| `fs.hash` | `path, algo=sha256` (md5/sha1/sha256/sha512) |

## fs.* Lifecycle (6)

| Tool | Args | Notes |
|------|------|-------|
| `fs.mkdir` | `path, parents=true, exist_ok=true` | |
| `fs.delete` | `path, recursive=false, trash=true` | trash by default; hard delete gated |
| `fs.move` | `source, destination, overwrite=false` | no clobber |
| `fs.copy` | `source, destination, overwrite=false, recursive=false` | no clobber |
| `fs.list_allowed_roots` | | volumes the caller can access |
| `fs.audit_log` | `since?, limit=20` | session mutations |

## fs.* Documents (2)

| Tool | Args | Notes |
|------|------|-------|
| `fs.extract_text` | `path, max_chars=200000, preview_chars=4000, ocr=true, refresh=false` | extract PDF/DOCX/PPTX/XLSX/HTML/CSV/image (CPU OCR)/text to Markdown; stores a companion `.md` next to the source (`report.pdf`->`report.md`), reused if up to date; returns `{md_path, preview, chars, cached}` (read the `.md` for full content) |
| `fs.write_docx` | `path, markdown, title?, overwrite=false` | render a Markdown subset (headings, lists, pipe tables, bold/italic) to a `.docx` |

## Error codes (`ErrorCode`)

`ERR_UNAUTHENTICATED`, `ERR_FORBIDDEN`, `ERR_PROJECT_NOT_FOUND`, `ERR_PROJECT_EXISTS`,
`ERR_PATH_OUT_OF_BOUNDS`, `ERR_EDIT_WITHOUT_PRIOR_READ`, `ERR_NO_CLOBBER`, `ERR_NOT_FOUND`,
`ERR_AMBIGUOUS_MATCH`, `ERR_NO_MATCH`, `ERR_WRITE_QUOTA_EXCEEDED`, `ERR_INVALID_ARGUMENT`,
`ERR_NOT_SUPPORTED`.
