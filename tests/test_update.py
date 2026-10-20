"""Update check: version comparison, release parsing, SHA-256 sums, no network."""
from __future__ import annotations

import urllib.error

from omni.core import update


def test_versions_compare_numerically():
    assert update.version_tuple("v0.10.0") > update.version_tuple("0.9.9")
    assert update.version_tuple("1.0") == (1, 0)
    assert update.version_tuple("") == (0,)


def test_sums_file_is_parsed():
    h = "a" * 64
    assert update.parse_sums(f"{h}  Omni-Setup-1.0.0.exe\n{'b' * 64} *Omni-1.0.0-windows.zip\nnoise\n") == {
        "Omni-Setup-1.0.0.exe": h, "Omni-1.0.0-windows.zip": "b" * 64}


def test_check_reports_a_newer_release(monkeypatch):
    monkeypatch.setattr(update, "__version__", "0.3.1")
    monkeypatch.setattr(update, "_json", lambda url: {
        "tag_name": "v0.4.0", "body": "notes", "html_url": "https://x",
        "assets": [{"browser_download_url": "u1", "name": "Omni-Setup-0.4.0.exe", "size": 5}, {"browser_download_url": "u2", "name": "SHA256SUMS.txt", "size": 1}]})
    info = update.check(force=True)
    assert info["available"] and info["latest"] == "0.4.0" and info["asset"]["sums"] == "u2"


def test_check_same_version_or_missing_installer_is_not_an_update(monkeypatch):
    monkeypatch.setattr(update, "__version__", "0.4.0")
    monkeypatch.setattr(update, "_json", lambda url: {"tag_name": "v0.4.0", "assets": []})
    assert update.check(force=True)["available"] is False
    monkeypatch.setattr(update, "__version__", "0.3.0")
    assert update.check(force=True)["available"] is False          # newer, but nothing to install


def test_missing_release_message(monkeypatch):
    def deny(url):
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)
    monkeypatch.setattr(update, "_json", deny)
    assert "Aucune" in update.check(force=True)["error"]


# ------------------------------------------------------------------------------------------- signed download
import hashlib  # noqa: E402

import pytest  # noqa: E402

from omni.native import AVAILABLE, N  # noqa: E402

SEED = bytes(range(32))


def _release(monkeypatch, tmp_path, *, sums=True, sig=True, tamper_sig=False, tamper_exe=False, listed=True):
    """A release served by a fake ``_asset_bytes``: installer, sums and their signature by the test key."""
    from omni.core.config import CONFIG
    monkeypatch.setattr(CONFIG, "workspace", tmp_path / "workspace")
    monkeypatch.setattr(update, "RELEASE_KEY", N.release_public_key(SEED).hex())
    exe = b"MZ installer"
    name = "Omni-Setup-9.9.9.exe"
    body = (f"{hashlib.sha256(exe).hexdigest()}  {name if listed else 'other.exe'}\n").encode()
    signature = N.sign_release(SEED, body)
    if tamper_sig:
        signature = bytes([signature[0] ^ 1]) + signature[1:]
    files = {"u-exe": exe + (b"!" if tamper_exe else b""), "u-sums": body, "u-sig": signature.hex().encode()}

    def fake(url, write, cancel=None, progress=None):
        write(files[url])
    monkeypatch.setattr(update, "_asset_bytes", fake)
    return {"asset": {"url": "u-exe", "name": name, "size": len(exe), "sums": "u-sums" if sums else None,
                      "sig": "u-sig" if sig else None}}


needs_native = pytest.mark.skipif(not AVAILABLE, reason="Rust core not built")


@needs_native
def test_signed_release_downloads(monkeypatch, tmp_path):
    path = update.download(_release(monkeypatch, tmp_path))
    assert path.read_bytes() == b"MZ installer" and not path.with_name(path.name + ".part").exists()


@needs_native
@pytest.mark.parametrize("kw, why", [
    ({"sums": False}, "pas signée"), ({"sig": False}, "pas signée"), ({"tamper_sig": True}, "invalide"),
    ({"listed": False}, "pas listé"), ({"tamper_exe": True}, "somme de contrôle"),
])
def test_unsigned_or_altered_release_is_refused(monkeypatch, tmp_path, kw, why):
    info = _release(monkeypatch, tmp_path, **kw)
    with pytest.raises(RuntimeError, match=why):
        update.download(info)
    assert not any((tmp_path / "workspace" / "updates").glob("*"))


@needs_native
def test_no_release_key_refuses_updates(monkeypatch, tmp_path):
    info = _release(monkeypatch, tmp_path)
    monkeypatch.setattr(update, "RELEASE_KEY", "")
    with pytest.raises(RuntimeError, match="clé"):
        update.download(info)
