"""The local API refuses other web pages: DNS rebinding (Host) and cross-site writes (Origin)."""
from __future__ import annotations

import pytest

pytest.importorskip("httpx2")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from omni.ui import security  # noqa: E402


@pytest.fixture()
def client():
    app = FastAPI()
    security.install(app)

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    @app.post("/api/shutdown")
    def shutdown():
        return {"stopped": True}
    return TestClient(app, base_url="http://127.0.0.1:8770")


def test_own_window_is_accepted(client):
    assert client.get("/api/ping").status_code == 200
    r = client.post("/api/shutdown", headers={"Origin": "http://127.0.0.1:8770"})
    assert r.status_code == 200
    assert client.post("/api/shutdown").status_code == 200           # no Origin: curl, the CLI, same-origin fetch


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


def test_dev_server_origin_only_with_the_flag(client, monkeypatch):
    dev = {"Origin": "http://localhost:3000"}
    assert client.post("/api/shutdown", headers=dev).status_code == 403
    monkeypatch.setenv("OMNI_DEV", "1")
    assert client.post("/api/shutdown", headers=dev).status_code == 200
