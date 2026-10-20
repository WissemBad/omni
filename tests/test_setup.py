"""Downloads of setup (core/setup.py): pinned URLs, SHA-256 checked, a wrong hash refused and nothing left behind."""
import hashlib
import io
import json
import re

import pytest

from omni.core import setup


class _Response(io.BytesIO):
    def __init__(self, body: bytes):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@pytest.fixture
def web(monkeypatch):
    """Fake network: ``web[url] = bytes``; the requested URLs are kept in ``web["seen"]``."""
    pages: dict = {"seen": []}

    def urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        pages["seen"].append(url)
        if url not in pages:
            raise OSError(f"no route to {url}")
        return _Response(pages[url])
    monkeypatch.setattr(setup.urllib.request, "urlopen", urlopen)
    return pages


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def test_download_checks_sha(web, tmp_path):
    web["https://x/f"] = b"payload"
    dest = tmp_path / "f"
    setup.download("https://x/f", dest, sha256=_sha(b"payload"))
    assert dest.read_bytes() == b"payload"


def test_download_refuses_wrong_sha(web, tmp_path):
    web["https://x/f"] = b"tampered"
    dest = tmp_path / "f"
    with pytest.raises(RuntimeError, match="refusé"):
        setup.download("https://x/f", dest, sha256=_sha(b"payload"))
    assert not dest.exists() and not (tmp_path / "f.part").exists()


def _seven_zip(tmp_path, text: bytes) -> bytes:
    import py7zr
    src = tmp_path / "src" / "hash_list.txt"
    src.parent.mkdir()
    src.write_bytes(text)
    out = tmp_path / "a.7z"
    with py7zr.SevenZipFile(out, "w") as z:
        z.write(src, "hash_list.txt")
    return out.read_bytes()


def test_hash_list_pinned_release(web, tmp_path):
    blob = _seven_zip(tmp_path, b"00ABCDEF,[assembly:/x.prim]\n")
    web[setup.release_url("o/r", "v1")] = blob
    dest = tmp_path / "names"
    assert setup.fetch_hash_list("o/r", ("v1", _sha(blob)), dest, say=lambda m: None) == "v1"
    assert (dest / "hash_list.txt").read_bytes().startswith(b"00ABCDEF")
    assert not (dest / setup.NAMES_ASSET).exists()
    assert not any("api.github.com" in u for u in web["seen"])     # the pinned release needs no API call


def test_hash_list_pinned_release_wrong_hash(web, tmp_path):
    web[setup.release_url("o/r", "v1")] = _seven_zip(tmp_path, b"x")
    dest = tmp_path / "names"
    with pytest.raises(RuntimeError, match="refusé"):
        setup.fetch_hash_list("o/r", ("v1", "0" * 64), dest, say=lambda m: None)
    assert not (dest / "hash_list.txt").exists() and not (dest / setup.NAMES_ASSET).exists()


def _latest(web, tag: str, digest: str | None):
    asset = {"name": setup.NAMES_ASSET} | ({"digest": digest} if digest is not None else {})
    web["https://api.github.com/repos/o/r/releases/latest"] = json.dumps({"tag_name": tag, "assets": [asset]}).encode()


def test_hash_list_latest_checks_published_hash(web, tmp_path):
    blob = _seven_zip(tmp_path, b"names")
    web[setup.release_url("o/r", "v2")] = blob
    _latest(web, "v2", "sha256:" + _sha(blob))
    assert setup.fetch_hash_list("o/r", ("v1", "0" * 64), tmp_path / "n", say=lambda m: None, latest=True) == "v2"


def test_hash_list_latest_wrong_published_hash(web, tmp_path):
    web[setup.release_url("o/r", "v2")] = _seven_zip(tmp_path, b"names")
    _latest(web, "v2", "sha256:" + "1" * 64)
    with pytest.raises(RuntimeError, match="refusé"):
        setup.fetch_hash_list("o/r", ("v1", "0" * 64), tmp_path / "n", say=lambda m: None, latest=True)


@pytest.mark.parametrize("digest", [None, "", "md5:abc", "sha256:short"])
def test_hash_list_latest_without_published_hash(web, tmp_path, digest):
    _latest(web, "v2", digest)
    with pytest.raises(RuntimeError, match="refusée"):
        setup.fetch_hash_list("o/r", ("v1", "0" * 64), tmp_path / "n", say=lambda m: None, latest=True)
    assert not any("releases/download" in u for u in web["seen"])


def test_pins_are_immutable():
    from omni.sources.glacier import hitman
    from omni.sources.unreal import game
    hexsha = re.compile(r"[0-9a-f]{64}")
    for tag, sha in (setup.NAMES_RELEASE, hitman.NAMES_RELEASE):
        assert re.fullmatch(r"v\d+", tag) and hexsha.fullmatch(sha)
    assert game.OODLE_COMMIT in game.OODLE_URL and re.fullmatch(r"[0-9a-f]{40}", game.OODLE_COMMIT)
    assert hexsha.fullmatch(game.OODLE_SHA256)
    m = game.archive_manifest()
    assert re.fullmatch(r"[0-9a-f]{40}", m["commit"]) and len(m["files"]) > 100
    assert all(p.lower().endswith(".usmap") and hexsha.fullmatch(s) for p, s in m["files"].items())


def test_archive_usmap_checked(web, tmp_path, monkeypatch):
    from omni.sources.unreal import game
    manifest = {"repository": "o/maps", "commit": "c" * 40,
                "files": {"Some Game/Mappings.usmap": _sha(b"usmap")}}
    monkeypatch.setattr(game, "archive_manifest", lambda: manifest)
    url = f"https://raw.githubusercontent.com/o/maps/{'c' * 40}/Some%20Game/Mappings.usmap"
    info = {"title": "Some Game"}
    web[url] = b"usmap"
    assert game._archive_usmap(info, tmp_path / "a.usmap").read_bytes() == b"usmap"
    web[url] = b"other"
    with pytest.raises(RuntimeError, match="refusé"):
        game._archive_usmap(info, tmp_path / "b.usmap")
    assert not (tmp_path / "b.usmap").exists()
    assert game._archive_usmap({"title": "Unknown"}, tmp_path / "c.usmap") is None
