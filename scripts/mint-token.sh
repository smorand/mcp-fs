#!/usr/bin/env bash
# Mint a short-lived RS256 bearer token for local testing against mcp-fs (jwt mode).
# The private key is web-a2a's (the signer); mcp-fs only verifies. Generate keys
# first with scripts/gen-jwt-demo-keys.sh.
#
# Usage:
#   scripts/mint-token.sh <email> [ttl_seconds]
# Example (curl):
#   TOK=$(scripts/mint-token.sh seb.morand@gmail.com)
#   curl -s localhost:5002/mcp -H "X-Forwarded-Authorization: Bearer $TOK" ...
set -euo pipefail

EMAIL="${1:?usage: mint-token.sh <email> [ttl_seconds]}"
TTL="${2:-3600}"
KEY="$(cd "$(dirname "$0")/.." && pwd)/../web-a2a/.keys/jwt.key"

uv run python - "$EMAIL" "$KEY" "$TTL" <<'PY'
import sys, time, jwt
email, key_path, ttl = sys.argv[1], sys.argv[2], int(sys.argv[3])
now = int(time.time())
print(jwt.encode({"email": email, "iss": "web-a2a", "iat": now, "exp": now + ttl}, open(key_path).read(), algorithm="RS256"))
PY
