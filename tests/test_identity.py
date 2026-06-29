"""Tests for identity extraction (debug header and signed JWT)."""

from __future__ import annotations

from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.datastructures import Headers

from mcp_fs import identity
from mcp_fs.identity import IdentityResolver, current_person
from mcp_fs.models import AuthConfig, AuthMode, JwtConfig, ToolError


def test_current_person_requires_binding() -> None:
    with pytest.raises(ToolError, match="ERR_UNAUTHENTICATED"):
        current_person()
    token = identity._current_identity.set("alice")
    try:
        assert current_person() == "alice"
    finally:
        identity._current_identity.reset(token)


def test_debug_resolver_reads_forwarded_user() -> None:
    resolver = IdentityResolver(AuthConfig(mode=AuthMode.DEBUG))
    assert resolver.extract(Headers({"X-Forwarded-User": "alice"})) == "alice"
    with pytest.raises(ToolError, match="ERR_UNAUTHENTICATED"):
        resolver.extract(Headers({}))


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
    resolver = IdentityResolver(AuthConfig(mode=AuthMode.JWT, jwt=JwtConfig(public_key_path=str(public_file))))
    good = jwt.encode({"preferred_username": "bob"}, private_pem, algorithm="RS256")
    assert resolver.extract(Headers({"Authorization": f"Bearer {good}"})) == "bob"

    with pytest.raises(ToolError, match="ERR_UNAUTHENTICATED"):  # no bearer
        resolver.extract(Headers({}))
    with pytest.raises(ToolError, match="ERR_UNAUTHENTICATED"):  # invalid token
        resolver.extract(Headers({"Authorization": "Bearer not-a-jwt"}))

    no_claim = jwt.encode({"sub": "nobody"}, private_pem, algorithm="RS256")
    with pytest.raises(ToolError, match="ERR_UNAUTHENTICATED"):  # missing username claim
        resolver.extract(Headers({"Authorization": f"Bearer {no_claim}"}))


def test_jwt_mode_requires_jwt_config() -> None:
    with pytest.raises(ValueError, match=r"auth\.jwt"):
        IdentityResolver(AuthConfig(mode=AuthMode.JWT, jwt=None))
