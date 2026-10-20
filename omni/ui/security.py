"""The local server must only answer the omni window, never a web page the user happens to have open.

A page on any site can send ``POST http://127.0.0.1:8770/api/shutdown`` (a "simple" request needs no CORS
preflight) or load ``<img src="http://127.0.0.1:8770/api/...">`` (a GET that builds a GLB or a catalog), and a page
on a hostname that resolves to 127.0.0.1 (DNS rebinding) is even same-origin for the browser. Three checks close it:

* ``Host`` must be a loopback name, so a rebound hostname is refused;
* every ``/api/*`` request, GET included, carries the session cookie: a random token drawn at each launch and set
  (``HttpOnly; SameSite=Strict``) when the server hands out the interface's pages. A browser never sends a Strict
  cookie with a request started by another site, and another site cannot read it;
* a request that changes something (POST/PUT/PATCH/DELETE) must come from omni's own origin: an ``Origin`` header from
  another site, or a cross-site ``Sec-Fetch-Site``, is refused.

Without a browser, ``/api/system`` (instance detection, the package smoke test) needs no token; ``/api/focus`` and the
other callers in ``omni/app.py`` read it from ``run.json`` (``header()``). ``OMNI_DEV=1`` also accepts the Nuxt dev
server (``bun run dev``, port 3000), which serves the pages itself: the token is not asked for then.
"""
from __future__ import annotations

import hmac
import os
import secrets
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
SAFE = {"GET", "HEAD", "OPTIONS"}
OPEN = {"/api/system"}                  # answered to anyone on the machine: version and tool status, nothing to change


def _split(value: str):
    try:
        u = urlsplit(value if "//" in value else "//" + value)
        return (u.hostname or "").lower(), u.port
    except ValueError:
        return "", None


def host_ok(host: str) -> bool:
    return _split(host)[0] in LOOPBACK


def origin_ok(origin: str, host: str) -> bool:
    if not origin or origin == "null":
        return False
    u = urlsplit(origin)
    name, port = (u.hostname or "").lower(), u.port
    if u.scheme != "http" or name not in LOOPBACK:
        return False
    if os.environ.get("OMNI_DEV") and port == 3000:
        return True
    return port == _split(host)[1]


def cookie_name(host: str) -> str:
    """One cookie per port: cookies ignore the port, and two omni servers (the app, ``omni ui``) may run side by side."""
    return f"omni_session_{_split(host)[1] or 80}"


def header(token: str, port: int) -> dict:
    """The ``Cookie`` header a caller outside the browser sends (``omni/app.py``, with the token of ``run.json``)."""
    return {"Cookie": f"omni_session_{port}={token}"} if token else {}


def install(app: FastAPI) -> str:
    """Guard ``app`` and return this launch's session token (also ``app.state.session_token``)."""
    token = secrets.token_urlsafe(32)
    app.state.session_token = token

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "")
        if not host_ok(host):
            return JSONResponse({"detail": "Hôte refusé"}, status_code=403)
        path = request.url.path
        api = path.startswith("/api/") or path == "/api"
        if api and path not in OPEN and not os.environ.get("OMNI_DEV"):
            sent = request.cookies.get(cookie_name(host), "")
            if not hmac.compare_digest(sent.encode(), token.encode()):
                return JSONResponse({"detail": "Session absente : ouvre omni depuis sa fenêtre"}, status_code=403)
        if request.method not in SAFE:
            origin = request.headers.get("origin")
            if origin is not None:
                if not origin_ok(origin, host):
                    return JSONResponse({"detail": "Origine refusée"}, status_code=403)
            elif request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
                return JSONResponse({"detail": "Origine refusée"}, status_code=403)
        response = await call_next(request)
        if not api and not path.startswith("/_nuxt/"):
            # the interface's pages hand out the session (a 304 too: the browser may hold a previous launch's cookie)
            response.set_cookie(cookie_name(host), token, httponly=True, samesite="strict", path="/")
        return response
    return token
