"""Read Garry's Mod addon archives (``.gma``, the Workshop format written by ``gmad``).

Layout (version 3): ``GMAD``, a version byte, a SteamID and a timestamp (u64 each), a list of required content strings
ending with an empty one, then the name, description and author (NUL-terminated), the addon version (i32), the file
table (u32 index, path, i64 size, u32 CRC, ended by index 0), and the file contents in table order.

Old Workshop items are stored as ``*_legacy.bin``: the same archive compressed with LZMA ("alone" format).

An archive comes from anywhere (the Workshop): every path is checked to stay inside the destination and every size
against the archive's own length before anything is written.
"""
from __future__ import annotations

import io
import json
import lzma
import shutil
import struct
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

MAGIC = b"GMAD"
MAX_NAME = 1024
MAX_LEGACY = 4 << 30                                       # a legacy archive decompresses to 4 GiB at most


@dataclass
class Entry:
    path: str
    size: int
    offset: int


@dataclass
class Gma:
    name: str
    description: str
    author: str
    version: int
    entries: list[Entry] = field(default_factory=list)


def _cstr(f: io.BufferedIOBase) -> str:
    out = bytearray()
    while True:
        b = f.read(1)
        if not b:
            raise ValueError("archive .gma tronquée")
        if b == b"\0":
            return out.decode("utf-8", "replace")
        out += b
        if len(out) > 65536:
            raise ValueError("chaîne trop longue dans l’archive .gma")


def _read(f: io.BufferedIOBase, fmt: str):
    size = struct.calcsize(fmt)
    data = f.read(size)
    if len(data) != size:
        raise ValueError("archive .gma tronquée")
    return struct.unpack(fmt, data)[0]


def _safe(path: str) -> str:
    """A relative posix path inside the archive, or ``ValueError``."""
    p = path.replace("\\", "/").strip()
    pp = PurePosixPath(p)
    if not p or len(p) > MAX_NAME or pp.is_absolute() or ":" in p or any(part in ("", ".", "..") for part in pp.parts):
        raise ValueError(f"chemin refusé dans l’archive .gma : {path!r}")
    return pp.as_posix().lower()


def read_index(path: Path) -> Gma:
    """The header and the file table of ``path``; contents are not read."""
    total = path.stat().st_size
    with path.open("rb") as f:
        if f.read(4) != MAGIC:
            raise ValueError("ce n’est pas une archive .gma (en-tête GMAD absent)")
        version = _read(f, "<B")
        if version > 3:
            raise ValueError(f"version d’archive .gma inconnue : {version}")
        f.read(16)                                        # SteamID and timestamp
        if version > 1:
            while _cstr(f):                               # required content, ended by an empty string
                pass
        g = Gma(name=_cstr(f), description=_cstr(f), author=_cstr(f), version=_read(f, "<i"))
        rows: list[tuple[str, int]] = []
        while _read(f, "<I"):
            name, size = _cstr(f), _read(f, "<q")
            f.read(4)                                     # CRC32 of the file
            if size < 0:
                raise ValueError("taille négative dans l’archive .gma")
            rows.append((_safe(name), size))
        offset = f.tell()
        for name, size in rows:
            if offset + size > total:
                raise ValueError("archive .gma tronquée : un fichier dépasse la fin")
            g.entries.append(Entry(name, size, offset))
            offset += size
    return g


def is_legacy(path: Path) -> bool:
    with path.open("rb") as f:
        return f.read(3) == b"]\0\0"


def unpack_legacy(path: Path, out: Path) -> None:
    """Decompress a ``*_legacy.bin`` Workshop item into the ``.gma`` it holds."""
    d = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
    tmp = out.with_name(out.name + ".part")
    written = 0
    with path.open("rb") as f, tmp.open("wb") as w:
        while chunk := f.read(1 << 20):
            data = d.decompress(chunk)
            written += len(data)
            if written > MAX_LEGACY:
                raise ValueError("archive .gma trop grande une fois décompressée")
            w.write(data)
    with tmp.open("rb") as r:
        if r.read(4) != MAGIC:
            tmp.unlink(missing_ok=True)
            raise ValueError("ce fichier du Workshop ne contient pas d’archive .gma")
    tmp.replace(out)


def title(g: Gma, fallback: str) -> str:
    """The addon's title: gmad stores a JSON description whose title may be the name itself."""
    name = g.name.strip()
    if not name:
        try:
            name = str(json.loads(g.description).get("title", "")).strip()
        except (ValueError, AttributeError):
            name = ""
    return name or fallback


def extract(path: Path, dest: Path, prefixes: tuple[str, ...] = ("models/", "materials/")) -> int:
    """Write the files of ``path`` whose path starts with one of ``prefixes`` under ``dest``; returns how many."""
    g = read_index(path)
    dest = dest.resolve()
    tmp = dest.with_name(dest.name + ".part")
    shutil.rmtree(tmp, ignore_errors=True)
    n = 0
    with path.open("rb") as f:
        for e in g.entries:
            if not e.path.startswith(prefixes):
                continue
            out = (tmp / e.path).resolve()
            if not out.is_relative_to(tmp.resolve()):
                raise ValueError(f"chemin refusé dans l’archive .gma : {e.path!r}")
            out.parent.mkdir(parents=True, exist_ok=True)
            f.seek(e.offset)
            with out.open("wb") as w:
                left = e.size
                while left:
                    chunk = f.read(min(left, 1 << 20))
                    if not chunk:
                        raise ValueError("archive .gma tronquée")
                    w.write(chunk)
                    left -= len(chunk)
            n += 1
    shutil.rmtree(dest, ignore_errors=True)
    if tmp.exists():
        tmp.rename(dest)
    else:
        dest.mkdir(parents=True, exist_ok=True)
    return n
