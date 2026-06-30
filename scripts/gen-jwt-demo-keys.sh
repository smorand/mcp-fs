#!/usr/bin/env bash
# Generate the shared RS256 keypair for the JWT identity chain, and mint a
# service token used by config-a2a for MCP tool discovery (no end user in
# context). web-a2a holds the private key and signs per-user tokens; config-a2a
# and mcp-fs hold the public key and verify.
#
# Contract (pinned across the three repos):
#   alg            RS256
#   identity claim email
#   issuer         web-a2a
#   forwarded as   X-Forwarded-Authorization: Bearer <jwt>   (jwt mode)
#                  X-Forwarded-User: <email>                  (debug mode)
#
# Re-run any time; keys live under each repo's gitignored .keys/ directory.
set -euo pipefail

PERSO="$(cd "$(dirname "$0")/../.." && pwd)"
WEB="$PERSO/web-a2a/.keys"
CFG="$PERSO/config-a2a/.keys"
FS="$PERSO/mcp-fs/.keys"
mkdir -p "$WEB" "$CFG" "$FS"

# 1. Keypair: private to web-a2a (signer), public to the two verifiers.
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$WEB/jwt.key" 2>/dev/null
openssl pkey -in "$WEB/jwt.key" -pubout -out "$WEB/jwt.pub" 2>/dev/null
cp "$WEB/jwt.pub" "$CFG/jwt.pub"
cp "$WEB/jwt.pub" "$FS/jwt.pub"

# 2. Long-lived service token for config-a2a tool discovery in jwt mode.
(cd "$PERSO/mcp-fs" && uv run python - "$WEB/jwt.key" "$CFG/service.jwt" <<'PY'
import sys, time, jwt
priv = open(sys.argv[1]).read()
now = int(time.time())
tok = jwt.encode(
    {"email": "service@web-a2a", "iss": "web-a2a", "iat": now, "exp": now + 315360000},
    priv, algorithm="RS256",
)
open(sys.argv[2], "w").write(tok)
print("service token bytes:", len(tok))
PY
)

echo "keys written:"
echo "  web-a2a (private + public): $WEB"
echo "  config-a2a (public + service token): $CFG"
echo "  mcp-fs (public): $FS"
