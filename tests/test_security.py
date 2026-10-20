"""The local API refuses other web pages: DNS rebinding (Host), calls without the session cookie, cross-site writes."""
from __future__ import annotations

import pytest

pytest.importorskip("httpx2")
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from omni.ui import security


def make_app() -> FastAPI:
    app = FastAPI()
    security.install(app)

    @app.get("/", response_class=HTMLResponse)
    def page():
        return "<html>omni</html>"

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    @app.get("/api/system")
    def system():
        return {"version": "x"}

    @app.post("/api/shutdown")
    def shutdown():
        return {"stopped": True}
    return app


@pytest.fixture()
def app():
    return make_app()


@pytest.fixture()
def bare(app):
    """A caller that never loaded the interface: another site's page, or a script."""
    return TestClient(app, base_url="http://127.0.0.1:8770")


@pytest.fixture()
def client(app):
    """The omni window: it opened the interface, which set the session cookie."""
    c = TestClient(app, base_url="http://127.0.0.1:8770")
    c.get("/")
    return c


def test_the_page_sets_a_strict_http_only_cookie(bare, app):
    r = bare.get("/")
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"omni_session_8770={app.state.session_token};")
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
    assert "set-cookie" not in bare.get("/api/system").headers


def test_every_launch_draws_another_token():
    assert make_app().state.session_token != make_app().state.session_token


def test_own_window_is_accepted(client):
    assert client.get("/api/ping").status_code == 200
    r = client.post("/api/shutdown", headers={"Origin": "http://127.0.0.1:8770"})
    assert r.status_code == 200
    assert client.post("/api/shutdown").status_code == 200           # no Origin: same-origin fetch


def test_no_cookie_no_api(bare, app):
    assert bare.get("/api/ping").status_code == 403                  # GET too: <img src> from any page
    assert bare.post("/api/shutdown").status_code == 403
    bare.cookies.set("omni_session_8770", "guess")
    assert bare.get("/api/ping").status_code == 403
    bare.cookies.clear()
    bare.cookies.set("omni_session_8771", app.state.session_token)   # the cookie of another omni server
    assert bare.get("/api/ping").status_code == 403


def test_tools_outside_the_browser(bare, app):
    assert bare.get("/api/system").status_code == 200                # instance detection, smoke test
    r = bare.post("/api/shutdown", headers=security.header(app.state.session_token, 8770))
    assert r.status_code == 200                                      # omni/app.py, token read in run.json


def test_another_site_cannot_write(client):
    for origin in ("https://evil.example", "http://evil.example:8770", "null", "http://127.0.0.1:9999",
                   "http://localhost.evil.example:8770"):
        r = client.post("/api/shutdown", headers={"Origin": origin})
        assert r.status_code == 403, origin
    assert client.post("/api/shutdown", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_rebound_hostname_is_refused(client):
    assert client.get("/api/ping", headers={"Host": "attacker.example:8770"}).status_code == 403
    assert client.get("/api/ping", headers={"Host": "localhost:8770"}).status_code == 200
    assert client.get("/api/ping", headers={"Host": "[::1]:8770"}).status_code == 200


def test_dev_server_only_with_the_flag(bare, monkeypatch):
    dev = {"Origin": "http://localhost:3000"}
    assert bare.post("/api/shutdown", headers=dev).status_code == 403
    monkeypatch.setenv("OMNI_DEV", "1")
    assert bare.get("/api/ping").status_code == 200                  # Nuxt serves the pages: no cookie then
    assert bare.post("/api/shutdown", headers=dev).status_code == 200
    assert bare.post("/api/shutdown", headers={"Origin": "https://evil.example"}).status_code == 403


def test_executable_paths_are_checked(tmp_path):
    from fastapi import HTTPException

    from omni.ui.routes_setup import clean_paths
    exe = tmp_path / "ffmpeg.exe"
    exe.write_bytes(b"MZ")
    (tmp_path / "evil.bat").write_text("calc", encoding="utf-8")
    assert clean_paths({"ffmpeg": f'"{exe}"', "blender": "", "gmod": None}) == {"ffmpeg": str(exe), "blender": ""}
    for bad in (str(tmp_path / "evil.bat"), str(tmp_path / "missing.exe"), "ffmpeg.exe", r"\\server\share\x.exe"):
        with pytest.raises(HTTPException):
            clean_paths({"studiomdl": bad})
    with pytest.raises(HTTPException):
        clean_paths({"gmod": str(tmp_path / "nowhere")})


def test_settings_put_checks_the_paths(tmp_path, monkeypatch):
    from omni.core import settings
    from omni.ui.api import create_app
    monkeypatch.setattr(settings, "_file", lambda: tmp_path / "settings.json")
    c = TestClient(create_app(jobs_db=None), base_url="http://127.0.0.1:8770")
    c.get("/")
    r = c.put("/api/settings", json={"paths": {"blender": r"C:\Windows\System32\cmd.bat"}})
    assert r.status_code == 400
    exe = tmp_path / "blender.exe"
    exe.write_bytes(b"MZ")
    r = c.put("/api/settings", json={"paths": {"blender": str(exe)}, "sounds": {"workers": 2}})
    assert r.status_code == 200 and r.json()["values"]["paths"]["blender"] == str(exe)
    exe.unlink()                     # a saved location that vanished does not block saving the other settings
    assert c.put("/api/settings", json={"paths": {"blender": str(exe)}, "sounds": {"workers": 3}}).status_code == 200
