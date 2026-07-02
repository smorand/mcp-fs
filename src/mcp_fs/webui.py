"""Human web UI plus the wiring for the data-plane API.

A minimal, themed file manager (one page) served by the same FastAPI app as
``/mcp`` and ``/health``. Authentication is a declarative email login stored in a
signed cookie (the sibling web-a2a pattern); the ``/api/fs`` endpoints accept
that cookie or a Bearer JWT, so the API is usable on its own. Everything goes
through the project ACL.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

from mcp_fs.dataplane import build_dataplane_router
from mcp_fs.identity import IdentityResolver
from mcp_fs.models import ToolError, normalize_identity
from mcp_fs.version import __version__

if TYPE_CHECKING:
    from fastapi import FastAPI

    from mcp_fs.context import ToolContext

_HERE = Path(__file__).resolve().parent
_STATIC = _HERE / "static"
_TEMPLATES = _HERE / "templates"
_THEMES = frozenset({"carbon", "ei"})


def mount_web(app: FastAPI, ctx: ToolContext) -> None:
    """Mount the static assets, the UI pages, and the ``/api/fs`` data plane."""
    webui = ctx.config.webui
    serializer = URLSafeSerializer(webui.secret_key, salt="mcp-fs-session")
    resolver = IdentityResolver(ctx.config.auth)
    templates = Jinja2Templates(directory=str(_TEMPLATES))
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

    def _cookie_email(request: Request) -> str | None:
        raw = request.cookies.get(webui.cookie_name)
        if not raw:
            return None
        try:
            return normalize_identity(str(serializer.loads(raw)))
        except BadSignature:
            return None

    async def identity(request: Request) -> str:
        """Resolve the caller from the session cookie or a Bearer JWT, else 401."""
        email = _cookie_email(request)
        if email:
            return email
        try:
            return resolver.extract(request.headers)
        except ToolError:
            raise HTTPException(status_code=401, detail="authentication required") from None

    def _theme(request: Request) -> tuple[str, bool]:
        theme = request.cookies.get("mcpfs_theme") or webui.theme
        if theme not in _THEMES:
            theme = webui.theme
        dark_cookie = request.cookies.get("mcpfs_dark")
        dark = webui.dark_mode if dark_cookie is None else dark_cookie == "1"
        return theme, dark

    def _page(request: Request, name: str, **extra: Any) -> Response:
        theme, dark = _theme(request)
        context = {"theme": theme, "dark_mode": dark, "asset_version": __version__, **extra}
        return templates.TemplateResponse(request, name, context)

    # -- pages ---------------------------------------------------------------
    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request) -> Any:
        if _cookie_email(request):
            return RedirectResponse("/", status_code=303)
        return _page(request, "login.html")

    @app.post("/login")
    async def login(email: str = Form(...)) -> RedirectResponse:
        person = email.strip()
        if not person:
            return RedirectResponse("/login", status_code=303)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            webui.cookie_name,
            serializer.dumps(normalize_identity(person)),
            max_age=webui.session_ttl_seconds,
            httponly=True,
            samesite="lax",
        )
        return response

    @app.post("/logout")
    async def logout() -> RedirectResponse:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(webui.cookie_name)
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> Any:
        email = _cookie_email(request)
        if not email:
            return RedirectResponse("/login", status_code=303)
        projects = await ctx.store.list_projects_for(email)
        roots = [{"mount_id": p.id, "owner": p.owner, "is_owner": p.owner == email} for p in projects]
        return _page(request, "index.html", email=email, roots=roots)

    @app.post("/preferences/theme")
    async def set_theme(theme: str = Form(...)) -> RedirectResponse:
        response = RedirectResponse("/", status_code=303)
        if theme in _THEMES:
            response.set_cookie("mcpfs_theme", theme, max_age=webui.session_ttl_seconds, samesite="lax")
        return response

    @app.post("/preferences/toggle-dark")
    async def toggle_dark(request: Request) -> RedirectResponse:
        _theme_name, dark = _theme(request)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie("mcpfs_dark", "0" if dark else "1", max_age=webui.session_ttl_seconds, samesite="lax")
        return response

    app.include_router(build_dataplane_router(ctx, identity))
