"""The local server must only answer the omni window, never a web page the user happens to have open.

A page on any site can send ``POST http://127.0.0.1:8770/api/shutdown`` (a "simple" request needs no CORS
preflight), and a page on a hostname that resolves to 127.0.0.1 (DNS rebinding) is even same-origin for the browser.
Two checks close both:

* ``Host`` must be a loopback name, so a rebound hostname is refused;
* a request that changes something (POST/PUT/PATCH/DELETE) must come from omni's own origin: an ``Origin`` header from
  another site, or a cross-site ``Sec-Fetch-Site``, is refused.

``OMNI_DEV=1`` also accepts the Nuxt dev server (``bun run dev``, port 3000).
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
SAFE = {"GET", "HEAD", "OPTIONS"}


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


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "")
        if not host_ok(host):
            return JSONResponse({"detail": "Hôte refusé"}, status_code=403)
        if request.method not in SAFE:
            origin = request.headers.get("origin")
            if origin is not None:
                if not origin_ok(origin, host):
                    return JSONResponse({"detail": "Origine refusée"}, status_code=403)
            elif request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
                return JSONResponse({"detail": "Origine refusée"}, status_code=403)
        return await call_next(request)
