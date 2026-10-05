"""Updates: the latest GitHub release of omni, downloaded, checked against its SHA-256 sums and run.

The repository can be private: a token with read access (settings, ``OMNI_UPDATE_TOKEN``/``GH_TOKEN``/``GITHUB_TOKEN``,
or the GitHub CLI's) is then needed to read the release and download its assets. Nothing is installed without the user
asking: ``check`` only reports, ``download`` + ``install`` run when they press the button.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import __version__
from .config import CONFIG
from .windows import NOWINDOW

log = logging.getLogger("omni.update")

REPO = "Wissem-Industries/omni"
API = f"https://api.github.com/repos/{REPO}"
CACHE_SECONDS = 6 * 3600
_cache: dict = {"at": 0.0, "value": None}


def version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def token(configured: str = "") -> str:
    """Setting first, then the environment, then the GitHub CLI when it is installed."""
    for t in (configured, os.environ.get("OMNI_UPDATE_TOKEN"), os.environ.get("GH_TOKEN"), os.environ.get("GITHUB_TOKEN")):
        if t:
            return t.strip()
    gh = shutil.which("gh")
    if gh:
        try:
            out = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=10, creationflags=NOWINDOW)
            if out.returncode == 0:
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return ""


def _headers(tok: str, accept: str = "application/vnd.github+json") -> dict:
    h = {"User-Agent": f"omni/{__version__}", "Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _json(url: str, tok: str):
    req = urllib.request.Request(url, headers=_headers(tok))
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def check(configured_token: str = "", force: bool = False) -> dict:
    """``{current, latest, available, notes, url, asset, error}``; cached for six hours."""
    if not force and _cache["value"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["value"]
    out = {"current": __version__, "latest": __version__, "available": False, "notes": "", "url": "", "asset": None,
           "error": ""}
    try:
        rel = _json(f"{API}/releases/latest", token(configured_token))
        latest = rel["tag_name"].lstrip("v")
        out.update(latest=latest, notes=rel.get("body") or "", url=rel.get("html_url", ""))
        asset = next((a for a in rel.get("assets", []) if re.fullmatch(r"Omni-Setup-[\d.]+\.exe", a["name"])), None)
        sums = next((a for a in rel.get("assets", []) if a["name"] == "SHA256SUMS.txt"), None)
        if asset:
            out["asset"] = {"id": asset["id"], "name": asset["name"], "size": asset["size"],
                            "sums": sums["id"] if sums else None}
        out["available"] = version_tuple(latest) > version_tuple(__version__) and asset is not None
    except urllib.error.HTTPError as e:
        out["error"] = ("Dépôt privé : renseigne un jeton GitHub (réglages › Mises à jour)." if e.code in (401, 403, 404)
                        else f"GitHub : {e.code}")
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        out["error"] = f"Vérification impossible : {e}"
    _cache.update(at=time.time(), value=out)
    return out


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _asset_bytes(asset_id: int, tok: str, write, cancel=None, progress=None) -> None:
    """Asset content to ``write(chunk)``. GitHub answers with a redirect to a signed storage URL, which must be
    followed *without* the token (storage refuses a second credential)."""
    url = f"{API}/releases/assets/{asset_id}"
    req = urllib.request.Request(url, headers=_headers(tok, "application/octet-stream"))
    try:
        resp = urllib.request.build_opener(_NoRedirect).open(req, timeout=30)
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise
        resp = urllib.request.urlopen(urllib.request.Request(e.headers["Location"], headers={"User-Agent": f"omni/{__version__}"}),
                                      timeout=60)
    with resp:
        total, done = int(resp.headers.get("Content-Length") or 0), 0
        while True:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("annulé")
            chunk = resp.read(1 << 20)
            if not chunk:
                return
            write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)


def parse_sums(text: str) -> dict[str, str]:
    """``<sha256>  <file>`` lines (sha256sum format) -> {file: sha256}."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if m:
            out[m.group(2)] = m.group(1).lower()
    return out


def download(info: dict, configured_token: str = "", progress=None, cancel=None) -> Path:
    """The installer into ``<workspace>/updates``, verified against the release's SHA256SUMS.txt."""
    asset = info.get("asset")
    if not asset:
        raise RuntimeError("aucun installeur dans cette version")
    tok = token(configured_token)
    dest = CONFIG.workspace / "updates" / asset["name"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    expected = ""
    if asset.get("sums"):
        buf = bytearray()
        _asset_bytes(asset["sums"], tok, buf.extend)
        expected = parse_sums(buf.decode("utf-8", "replace")).get(asset["name"], "")
    tmp = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    try:
        with open(tmp, "wb") as f:
            _asset_bytes(asset["id"], tok, lambda c: (f.write(c), h.update(c)), cancel, progress)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if expected and h.hexdigest() != expected:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("somme de contrôle de l'installeur inattendue : téléchargement refusé")
    os.replace(tmp, dest)
    return dest


def install(path: Path) -> None:
    """Run the installer silently; it closes omni and starts the new version (Inno Setup restart manager)."""
    DETACHED = 0x00000008
    subprocess.Popen([str(path), "/SILENT", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"], creationflags=DETACHED | NOWINDOW,
                     close_fds=True)
