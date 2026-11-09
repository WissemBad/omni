"""Web API contract (omni/ui/api.py): what the Nuxt front-end relies on, on the synthetic HITMAN 3 game (tests/synthetic.py),
so it runs everywhere, CI included."""
import time

import pytest

pytest.importorskip("httpx2")

SID = "hitman3"
CRATE = "0011223344556601"


@pytest.fixture
def client(api_client):
    return api_client


def test_sources_declare_capabilities(client):
    src = {s["id"]: s for s in client.get("/api/sources").json()}
    assert {"id", "title", "capabilities", "deployed"} <= set(src[SID])
    assert "props" in src[SID]["capabilities"]


def test_unknown_source_is_404(client):
    assert client.get("/api/nope/info").status_code == 404
    assert client.get("/api/nope/props").status_code == 404


def test_the_api_needs_the_session_cookie(synthetic_game):
    from fastapi.testclient import TestClient

    from omni.ui.api import create_app
    bare = TestClient(create_app(jobs_db=None), base_url="http://127.0.0.1:8770")
    assert bare.get(f"/api/{SID}/props").status_code == 403


def test_props_search_and_keys(client):
    r = client.get(f"/api/{SID}/props?q=crate&limit=3").json()
    assert r["total"] == 1 and len(r["items"]) == 1
    assert {"key", "rel", "cat", "converted"} <= set(r["items"][0])
    assert r["items"][0]["key"] == CRATE
    assert client.get(f"/api/{SID}/props/keys?q=crate").json() == [CRATE]
    assert client.get(f"/api/{SID}/props?q=nothing-like-this").json()["total"] == 0


def test_select_all_follows_the_converted_filter(client):
    """"Tout sélectionner" asks for the keys with the same filters as the list (a converted prop is not selected by 'to convert')."""
    assert client.get(f"/api/{SID}/props/keys?done=no").json() == [CRATE]
    assert client.get(f"/api/{SID}/props/keys?done=yes").json() == []
    assert client.get(f"/api/{SID}/props?done=yes").json()["total"] == 0


def test_category_filter_includes_subfolders(client):
    cats = client.get(f"/api/{SID}/props/categories").json()
    assert cats and cats[0]["n"] == 1
    top = cats[0]["cat"].split("/")[0]
    assert client.get(f"/api/{SID}/props?cat={top}&limit=1").json()["total"] >= cats[0]["n"]


def test_characters_of_a_game_without_outfits_are_empty(client):
    r = client.get(f"/api/{SID}/characters?limit=5").json()
    assert r["total"] == 0 and r["items"] == []
    assert {"mission", "role", "body", "kind"} == set(r["facets"])


def test_sound_file_rejects_path_traversal(client):
    assert client.get(f"/api/{SID}/sounds/file?path=../../README.md").status_code in (400, 404)


def test_jobs_listing(client):
    assert isinstance(client.get("/api/jobs").json(), list)
    assert client.get("/api/jobs/doesnotexist").status_code == 404


def test_system_reports_rust_and_tools(client):
    s = client.get("/api/system").json()
    assert {"rust", "tools", "workspace"} <= set(s)
    assert {"native", "native_version"} <= set(s["rust"])


def test_textures_search_detail_and_image(client):
    job = client.post(f"/api/{SID}/textures/rebuild").json()
    for _ in range(100):
        if client.get(f"/api/{SID}/textures/status").json()["ready"]:
            break
        time.sleep(0.1)
    st = client.get(f"/api/{SID}/textures/status").json()
    assert st["ready"], (job, st)
    r = client.get(f"/api/{SID}/textures?q=diffuse&limit=2").json()
    assert r["total"] == 1 and {"key", "fmt", "width", "role"} <= set(r["items"][0])
    k = r["items"][0]["key"]
    assert k == "0011223344556603" and r["items"][0]["fmt"] == "BC1"
    d = client.get(f"/api/{SID}/textures/{k}").json()
    assert {"materials", "models", "model_count"} <= set(d)
    img = client.get(f"/api/{SID}/textures/{k}/image?size=64")
    assert img.status_code == 200 and img.content[:4] == b"\x89PNG"
    assert client.get(f"/api/{SID}/textures/ZZZ/image").status_code == 400


def test_overview_has_every_capability(client):
    o = client.get(f"/api/{SID}/overview").json()
    for cap in o["capabilities"]:
        if cap in ("props", "characters", "textures", "sounds"):
            assert cap in o
    assert "addon" in o
