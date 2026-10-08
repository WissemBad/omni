"""Updates: the latest GitHub release of omni, downloaded, checked against its SHA-256 sums and run.

The repository is public, so everything is read anonymously. Nothing is installed without the user
asking: ``check`` only reports, ``download`` + ``install`` run when they press the button.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import __version__
from .config import CONFIG
from .windows import NOWINDOW

log = logging.getLogger("omni.update")

REPO = "WissemBad/omni"
API = f"https://api.github.com/repos/{REPO}"
CACHE_SECONDS = 6 * 3600
_cache: dict = {"at": 0.0, "value": None}


def version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def _headers(accept: str = "application/vnd.github+json") -> dict:
    return {"User-Agent": f"omni/{__version__}", "Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}


def _json(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers=_headers()), timeout=15) as r:
        return json.loads(r.read())


def check(force: bool = False) -> dict:
    """``{current, latest, available, notes, url, asset, error}``; cached for six hours."""
    if not force and _cache["value"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["value"]
    out = {"current": __version__, "latest": __version__, "available": False, "notes": "", "url": "", "asset": None,
           "error": ""}
    try:
        rel = _json(f"{API}/releases/latest")
        latest = rel["tag_name"].lstrip("v")
        out.update(latest=latest, notes=rel.get("body") or "", url=rel.get("html_url", ""))
        asset = next((a for a in rel.get("assets", []) if re.fullmatch(r"Omni-Setup-[\d.]+\.exe", a["name"])), None)
        sums = next((a for a in rel.get("assets", []) if a["name"] == "SHA256SUMS.txt"), None)
        if asset:
            out["asset"] = {"url": asset["browser_download_url"], "name": asset["name"], "size": asset["size"],
                            "sums": sums["browser_download_url"] if sums else None}
        out["available"] = version_tuple(latest) > version_tuple(__version__) and asset is not None
    except urllib.error.HTTPError as e:
        out["error"] = "Aucune version publiée." if e.code == 404 else f"GitHub : {e.code}"
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        out["error"] = f"Vérification impossible : {e}"
    _cache.update(at=time.time(), value=out)
    return out


def _asset_bytes(url: str, write, cancel=None, progress=None) -> None:
    """Content of a release file to ``write(chunk)``."""
    with urllib.request.urlopen(urllib.request.Request(url, headers=_headers("application/octet-stream")), timeout=60) as resp:
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


def download(info: dict, progress=None, cancel=None) -> Path:
    """The installer into ``<workspace>/updates``, verified against the release's SHA256SUMS.txt."""
    asset = info.get("asset")
    if not asset:
        raise RuntimeError("aucun installeur dans cette version")
    dest = CONFIG.workspace / "updates" / asset["name"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    expected = ""
    if asset.get("sums"):
        buf = bytearray()
        _asset_bytes(asset["sums"], buf.extend)
        expected = parse_sums(buf.decode("utf-8", "replace")).get(asset["name"], "")
    tmp = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    try:
        with open(tmp, "wb") as f:
            _asset_bytes(asset["url"], lambda c: (f.write(c), h.update(c)), cancel, progress)
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
