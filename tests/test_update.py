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
    monkeypatch.setattr(update, "_json", lambda url, tok: {
        "tag_name": "v0.4.0", "body": "notes", "html_url": "https://x",
        "assets": [{"id": 1, "name": "Omni-Setup-0.4.0.exe", "size": 5}, {"id": 2, "name": "SHA256SUMS.txt", "size": 1}]})
    info = update.check("t", force=True)
    assert info["available"] and info["latest"] == "0.4.0" and info["asset"]["sums"] == 2


def test_check_same_version_or_missing_installer_is_not_an_update(monkeypatch):
    monkeypatch.setattr(update, "__version__", "0.4.0")
    monkeypatch.setattr(update, "_json", lambda url, tok: {"tag_name": "v0.4.0", "assets": []})
    assert update.check("t", force=True)["available"] is False
    monkeypatch.setattr(update, "__version__", "0.3.0")
    assert update.check("t", force=True)["available"] is False          # newer, but nothing to install


def test_private_repository_message(monkeypatch):
    def deny(url, tok):
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)
    monkeypatch.setattr(update, "_json", deny)
    assert "jeton" in update.check("", force=True)["error"]
