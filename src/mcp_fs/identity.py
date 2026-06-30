"""Caller identity extraction (verified RS256 bearer token) via ASGI middleware.

The resolved person is stored in a :class:`~contextvars.ContextVar` so tool
handlers can read it without threading request objects through every call.
Pure ASGI middleware is used (not ``BaseHTTPMiddleware``) so the context var
propagates correctly into the request task.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

import jwt
from starlette.datastructures import Headers

from mcp_fs.models import AuthConfig, ErrorCode, ToolError, normalize_identity

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

_current_identity: ContextVar[str | None] = ContextVar("current_identity", default=None)


def current_person() -> str:
    """Return the authenticated person for the in-flight request.

    Raises:
        ToolError: ``ERR_UNAUTHENTICATED`` if no identity is bound (should not
            happen behind the middleware, but guards direct tool calls in tests).
    """
    person = _current_identity.get()
    if not person:
        raise ToolError(ErrorCode.UNAUTHENTICATED, "no authenticated identity in context")
    return person


class IdentityResolver:
    """Extracts the caller's person from request headers per the auth config."""

    __slots__ = ("_config", "_public_key")

    def __init__(self, config: AuthConfig) -> None:
        self._config = config
        self._public_key = Path(config.jwt.public_key_path).read_text(encoding="utf-8")

    def extract(self, headers: Headers) -> str:
        """Return the person identified by the request, or raise ``ERR_UNAUTHENTICATED``."""
        return self._from_jwt(headers)

    def _from_jwt(self, headers: Headers) -> str:
        jwt_config = self._config.jwt
        authorization = headers.get(jwt_config.header, "")
        if not authorization.lower().startswith("bearer "):
            raise ToolError(ErrorCode.UNAUTHENTICATED, f"missing Bearer token in {jwt_config.header}")
        token = authorization[len("Bearer ") :].strip()
        try:
            claims = jwt.decode(
                token,
                self._public_key,
                algorithms=jwt_config.algorithms,
                audience=jwt_config.audience,
                issuer=jwt_config.issuer,
                options={"verify_aud": jwt_config.audience is not None},
            )
        except jwt.PyJWTError as exc:
            raise ToolError(ErrorCode.UNAUTHENTICATED, f"invalid token: {exc}") from exc
        person = str(claims.get(jwt_config.username_claim, "")).strip()
        if not person:
            raise ToolError(
                ErrorCode.UNAUTHENTICATED,
                f"token missing '{jwt_config.username_claim}' claim",
            )
        return normalize_identity(person)


class IdentityMiddleware:
    """ASGI middleware binding the resolved identity for protected paths."""

    __slots__ = ("_app", "_prefix", "_resolver")

    def __init__(self, app: ASGIApp, resolver: IdentityResolver, protected_prefix: str) -> None:
        self._app = app
        self._resolver = resolver
        self._prefix = protected_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self._prefix):
            await self._app(scope, receive, send)
            return
        try:
            person = self._resolver.extract(Headers(scope=scope))
        except ToolError as exc:
            await _send_unauthorized(send, exc.message)
            return
        token = _current_identity.set(person)
        try:
            await self._app(scope, receive, send)
        finally:
            _current_identity.reset(token)


async def _send_unauthorized(send: Send, detail: str) -> None:
    """Emit a minimal 401 JSON response."""
    body = f'{{"error":"{ErrorCode.UNAUTHENTICATED.value}","detail":"{detail}"}}'.encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
