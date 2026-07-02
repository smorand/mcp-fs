"""Cross-platform key setup for the JWT identity chain (works on Windows too).

Generates an RS256 keypair and a service token used for MCP tool discovery, then
distributes them: the private key signs (web-a2a), the public key verifies
(config-a2a and mcp-fs), the service token lets discovery pass the downstream
auth. Everything is written under each repo's gitignored ``.keys/`` directory;
mcp-fs also keeps the private key locally so it can mint test tokens standalone.

Run from the mcp-fs repo:  uv run python scripts/gen_jwt_keys.py
Re-run any time. Contract: RS256, claim ``email``, issuer ``web-a2a``, forwarded
as ``X-Forwarded-Authorization: Bearer <jwt>``.
"""

from __future__ import annotations

import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

FS_ROOT = Path(__file__).resolve().parent.parent
PERSO = FS_ROOT.parent


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    print(f"  wrote {path}")


def main() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    now = int(time.time())
    service_token = jwt.encode(
        {"email": "service@web-a2a", "iss": "web-a2a", "iat": now, "exp": now + 315360000},
        private_pem,
        algorithm="RS256",
    ).encode("utf-8")

    # mcp-fs keeps public + service (verifier) and the private key for standalone minting.
    print("mcp-fs:")
    _write(FS_ROOT / ".keys" / "jwt.key", private_pem)
    _write(FS_ROOT / ".keys" / "jwt.pub", public_pem)
    _write(FS_ROOT / ".keys" / "service.jwt", service_token)

    # Distribute to sibling repos when present (full chain).
    web = PERSO / "web-a2a"
    if web.is_dir():
        print("web-a2a (signer):")
        _write(web / ".keys" / "jwt.key", private_pem)
        _write(web / ".keys" / "jwt.pub", public_pem)
    cfg = PERSO / "config-a2a"
    if cfg.is_dir():
        print("config-a2a (verifier + discovery):")
        _write(cfg / ".keys" / "jwt.pub", public_pem)
        _write(cfg / ".keys" / "service.jwt", service_token)

    print("done.")


if __name__ == "__main__":
    main()
