"""Cross-platform: mint a short-lived RS256 bearer token for testing (Windows too).

The private key is read from mcp-fs/.keys/jwt.key by default (written by
gen_jwt_keys.py). mcp-fs only verifies in production; this local key is a testing
convenience so you can mint tokens without web-a2a running.

Usage:  uv run python scripts/mint_token.py <email> [ttl_seconds]
Print only the token, so it composes:  set/export it, then send it as
"X-Forwarded-Authorization: Bearer <token>".
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import jwt

FS_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: mint_token.py <email> [ttl_seconds]")
    email = sys.argv[1]
    ttl = int(sys.argv[2]) if len(sys.argv) > 2 else 3600
    key_path = FS_ROOT / ".keys" / "jwt.key"
    if not key_path.is_file():
        sys.exit(f"private key not found at {key_path}; run: uv run python scripts/gen_jwt_keys.py")
    now = int(time.time())
    token = jwt.encode(
        {"email": email, "iss": "web-a2a", "iat": now, "exp": now + ttl},
        key_path.read_text(encoding="utf-8"),
        algorithm="RS256",
    )
    print(token)


if __name__ == "__main__":
    main()
