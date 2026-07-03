"""Cross-platform: create a project in a running mcp-fs (admin call over HTTP).

Mints a platform-admin token locally and calls admin.create_project, so you do
not need curl quoting on Windows. The admin identity must be listed in the
server config's auth.admins.

Usage:
  uv run python scripts/provision.py <mount_id> [owner_email]
Environment:
  MCP_FS_URL    base URL of the server (default http://127.0.0.1:8080/mcp)
  MCP_FS_ADMIN  admin identity used as the caller (default seb.morand@gmail.com)
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx
import jwt

FS_ROOT = Path(__file__).resolve().parent.parent
URL = os.environ.get("MCP_FS_URL", "http://127.0.0.1:8080/mcp")
ADMIN = os.environ.get("MCP_FS_ADMIN", "seb.morand@gmail.com")
_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: provision.py <mount_id> [owner_email]")
    mount_id = sys.argv[1]
    owner = sys.argv[2] if len(sys.argv) > 2 else ADMIN
    key = (FS_ROOT / ".keys" / "jwt.key").read_text(encoding="utf-8")
    now = int(time.time())
    token = jwt.encode({"email": ADMIN, "iss": "web-a2a", "iat": now, "exp": now + 3600}, key, algorithm="RS256")

    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "admin.create_project", "arguments": {"project_id": mount_id, "owner": owner}},
    }
    response = httpx.post(
        URL, headers={**_HEADERS, "X-Forwarded-Authorization": f"Bearer {token}"}, json=body, timeout=30
    )
    for line in response.text.splitlines():
        if line.startswith("data: "):
            result = json.loads(line[len("data: ") :])["result"]
            if result.get("isError"):
                text = result["content"][0]["text"]
                if "ERR_PROJECT_EXISTS" in text:  # idempotent: already provisioned is fine
                    print(f"already exists: {mount_id}")
                    return
                print("error:", text)
                sys.exit(1)
            print("provisioned:", result.get("structuredContent", result))
            return
    sys.exit(f"unexpected response ({response.status_code}): {response.text[:200]}")


if __name__ == "__main__":
    main()
