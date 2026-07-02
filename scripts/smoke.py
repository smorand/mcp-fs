"""Cross-platform end-to-end smoke test against a running mcp-fs server (Windows too).

Assumes the server is already running (see the moto instructions in
.agent_docs/local-testing.md). Mints tokens locally, then exercises the JWT
identity chain and the filesystem round trip over real HTTP, printing one line
per check. Exits non-zero on the first failure, so it is easy to run remotely and
paste the output.

Usage:
  uv run python scripts/smoke.py            # defaults: http://127.0.0.1:8080, seb.morand@gmail.com
  uv run python scripts/smoke.py <base_url> <admin_email>
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
import jwt

FS_ROOT = Path(__file__).resolve().parent.parent
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
ADMIN = sys.argv[2] if len(sys.argv) > 2 else "seb.morand@gmail.com"
MOUNT = "smoke-proj"
OUTSIDER = "nobody@example.com"
_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
_KEY = (FS_ROOT / ".keys" / "jwt.key").read_text(encoding="utf-8")

_failures = 0


def _mint(email: str) -> str:
    now = int(time.time())
    return jwt.encode({"email": email, "iss": "web-a2a", "iat": now, "exp": now + 3600}, _KEY, algorithm="RS256")


def _post(token: str | None, method: str, params: dict | None = None) -> httpx.Response:
    headers = dict(_HEADERS)
    if token is not None:
        headers["X-Forwarded-Authorization"] = f"Bearer {token}"
    body: dict = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return httpx.post(f"{BASE}/mcp", headers=headers, json=body, timeout=30)


def _result(response: httpx.Response) -> dict:
    for line in response.text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: ") :])["result"]
    raise AssertionError(f"no SSE data frame: {response.text[:200]}")


def _call(token: str | None, tool: str, **arguments: object) -> dict:
    return _result(_post(token, "tools/call", {"name": tool, "arguments": arguments}))


def check(label: str, ok: bool, detail: str = "") -> None:
    global _failures  # noqa: PLW0603
    mark = "PASS" if ok else "FAIL"
    if not ok:
        _failures += 1
    print(f"[{mark}] {label}{(' - ' + detail) if detail else ''}")


def main() -> None:
    admin = _mint(ADMIN)

    health = httpx.get(f"{BASE}/health", timeout=10).json()
    check("health", health.get("status") == "ok", str(health))

    created = _call(admin, "admin.create_project", project_id=MOUNT, owner=ADMIN)
    check("admin.create_project", created.get("structuredContent", {}).get("project_id") == MOUNT)

    wrote = _call(admin, "fs.write", mount_id=MOUNT, path="/notes.md", content="# bonjour EI\n")
    check("fs.write", wrote.get("structuredContent", {}).get("bytes_written") == len("# bonjour EI\n"))

    read = _call(admin, "fs.read", mount_id=MOUNT, path="/notes.md")
    check("fs.read", "bonjour EI" in read.get("structuredContent", {}).get("content", ""))

    # caseless: the same admin email in a different case is still admin/owner
    upper = _mint(ADMIN.upper())
    roots = _call(upper, "fs.list_allowed_roots", mount_id=MOUNT)
    ids = [r["mount_id"] for r in roots.get("structuredContent", {}).get("roots", [])]
    check("caseless identity", MOUNT in ids, f"roots={ids}")

    # anti-bypass: no token, and the old trust header, both rejected
    check("401 without token", _post(None, "tools/list").status_code == 401)
    no_bearer = httpx.post(
        f"{BASE}/mcp",
        headers={**_HEADERS, "X-Forwarded-User": ADMIN},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        timeout=10,
    )
    check("401 with X-Forwarded-User only", no_bearer.status_code == 401)

    # outsider: a valid token whose identity is not on the ACL is forbidden
    forbidden = _call(_mint(OUTSIDER), "fs.read", mount_id=MOUNT, path="/notes.md")
    text = json.dumps(forbidden)
    check("outsider forbidden", forbidden.get("isError") is True and "ERR_FORBIDDEN" in text)

    print("ALL OK" if _failures == 0 else f"{_failures} FAILURE(S)")
    sys.exit(1 if _failures else 0)


if __name__ == "__main__":
    main()
