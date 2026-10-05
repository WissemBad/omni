"""Web API contract (omni/ui/api.py): what the Nuxt front-end relies on. Runs against the real catalog."""
import pytest

pytest.importorskip("httpx2")
from fastapi.testclient import TestClient  # noqa: E402

from omni.core.config import CONFIG  # noqa: E402
from omni.ui.api import create_app  # noqa: E402

pytestmark = pytest.mark.skipif(not CONFIG.assets_sorted.exists(), reason="game assets not extracted")


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(jobs_db=None), base_url="http://127.0.0.1:8770")


def test_sources_declare_capabilities(client):
    src = client.get("/api/sources").json()
    assert src and {"id", "title", "capabilities", "deployed"} <= set(src[0])
    assert "props" in src[0]["capabilities"]


def test_unknown_source_is_404(client):
    assert client.get("/api/nope/info").status_code == 404
    assert client.get("/api/nope/props").status_code == 404


def test_props_search_and_keys(client):
    r = client.get("/api/007fl/props?q=chair&limit=3").json()
    assert r["total"] >= len(r["items"]) > 0
    assert {"key", "rel", "cat", "converted"} <= set(r["items"][0])
    keys = client.get("/api/007fl/props/keys?q=chair").json()
    assert len(keys) == r["total"]


def test_category_filter_includes_subfolders(client):
    cats = client.get("/api/007fl/props/categories").json()
    top = cats[0]["cat"].split("/")[0]
    assert client.get(f"/api/007fl/props?cat={top}&limit=1").json()["total"] >= cats[0]["n"]


def test_characters_facets_are_cross_filtered(client):
    r = client.get("/api/007fl/characters?body=fem_reg&limit=5").json()
    assert r["total"] > 0 and all(c["body"] == "fem_reg" for c in r["items"])
    assert {"mission", "role", "body", "kind"} == set(r["facets"])
    assert "male_reg" in {f["value"] for f in r["facets"]["body"]}      # a facet ignores its own filter


def test_sound_file_rejects_path_traversal(client):
    assert client.get("/api/007fl/sounds/file?path=../../README.md").status_code == 404


def test_jobs_listing(client):
    assert isinstance(client.get("/api/jobs").json(), list)
    assert client.get("/api/jobs/doesnotexist").status_code == 404


def test_system_reports_rust_and_tools(client):
    s = client.get("/api/system").json()
    assert {"rust", "tools", "workspace"} <= set(s)
    assert {"native", "wasm", "backends"} <= set(s["rust"])


def test_textures_search_detail_and_image(client):
    st = client.get("/api/007fl/textures/status").json()
    if not st["ready"]:
        pytest.skip("texture catalog not built yet")
    r = client.get("/api/007fl/textures?q=normal&limit=2").json()
    assert r["total"] > 0 and {"key", "fmt", "width", "role"} <= set(r["items"][0])
    k = r["items"][0]["key"]
    d = client.get(f"/api/007fl/textures/{k}").json()
    assert {"materials", "models", "model_count"} <= set(d)
    img = client.get(f"/api/007fl/textures/{k}/image?size=64")
    assert img.status_code == 200 and img.content[:4] == b"\x89PNG"
    assert client.get("/api/007fl/textures/ZZZ/image").status_code == 400


def test_overview_has_every_capability(client):
    o = client.get("/api/007fl/overview").json()
    for cap in o["capabilities"]:
        if cap in ("props", "characters", "textures", "sounds"):
            assert cap in o
    assert "addon" in o
