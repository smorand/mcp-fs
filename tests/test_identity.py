"""Tests for identity extraction (verified RS256 bearer token)."""

from __future__ import annotations

from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError
from starlette.datastructures import Headers

from mcp_fs import identity
from mcp_fs.identity import IdentityResolver, current_person
from mcp_fs.models import AuthConfig, JwtConfig, ToolError


def test_current_person_requires_binding() -> None:
    with pytest.raises(ToolError, match="ERR_UNAUTHENTICATED"):
        current_person()
    token = identity._current_identity.set("alice")
    try:
        assert current_person() == "alice"
    finally:
        identity._current_identity.reset(token)


def _keypair(tmp_path: Path) -> tuple[bytes, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_file = tmp_path / "jwt.pub"
    public_file.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_pem, public_file


def test_jwt_resolver_happy_and_error_paths(tmp_path: Path) -> None:
    private_pem, public_file = _keypair(tmp_path)
    resolver = IdentityResolver(AuthConfig(jwt=JwtConfig(public_key_path=str(public_file))))
    good = jwt.encode({"email": "bob@example.com", "iss": "web-a2a"}, private_pem, algorithm="RS256")
    assert resolver.extract(Headers({"X-Forwarded-Authorization": f"Bearer {good}"})) == "bob@example.com"

    with pytest.raises(ToolError, match="ERR_UNAUTHENTICATED"):  # no bearer
        resolver.extract(Headers({}))
    with pytest.raises(ToolError, match="ERR_UNAUTHENTICATED"):  # the trust header is not honored
        resolver.extract(Headers({"X-Forwarded-User": "attacker"}))
    with pytest.raises(ToolError, match="ERR_UNAUTHENTICATED"):  # invalid token
        resolver.extract(Headers({"X-Forwarded-Authorization": "Bearer not-a-jwt"}))

    wrong_issuer = jwt.encode({"email": "x", "iss": "evil"}, private_pem, algorithm="RS256")
    with pytest.raises(ToolError, match="ERR_UNAUTHENTICATED"):  # issuer mismatch
        resolver.extract(Headers({"X-Forwarded-Authorization": f"Bearer {wrong_issuer}"}))

    no_claim = jwt.encode({"sub": "nobody", "iss": "web-a2a"}, private_pem, algorithm="RS256")
    with pytest.raises(ToolError, match="ERR_UNAUTHENTICATED"):  # missing email claim
        resolver.extract(Headers({"X-Forwarded-Authorization": f"Bearer {no_claim}"}))


def test_auth_config_requires_jwt() -> None:
    with pytest.raises(ValidationError):
        AuthConfig(admins=["alice"])  # type: ignore[call-arg]
