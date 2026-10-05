"""Audiokinetic Wwise media (.wem) -> standard audio files.

  auto  Wwise Vorbis -> .ogg: the game's Vorbis packets rewrapped as a standard Ogg Vorbis file (ww2ogg + exact
        granules), no re-encoding, bit-identical audio. Any other codec (Platinum ADPCM, PCM, ...) cannot be
        rewrapped and is decoded (vgmstream) to lossless .flac.
  flac  everything decoded, lossless FLAC (heavier than the game's lossy data, same sound).
  wav   everything decoded, 16-bit PCM WAV (heaviest).
  mp3   everything decoded and encoded MP3 V0 (a second lossy generation: only for tools that need MP3).
"""
from __future__ import annotations

import os
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ...core.config import CONFIG
from .oggfix import fix_granules

FORMATS = ("auto", "ogg", "flac", "wav", "mp3")
VORBIS = 0xFFFF
_NOWIN = 0x08000000 if os.name == "nt" else 0       # CREATE_NO_WINDOW


@dataclass
class WemInfo:
    codec: int = -1
    channels: int = 0
    rate: int = 0
    samples: int | None = None          # Wwise Vorbis only (exact count in the header)
    label: str = ""                     # original file name kept by Wwise (LIST/labl), may be empty
    data_size: int = 0


def wem_info(b: bytes) -> WemInfo | None:
    if b[:4] != b"RIFF" or b[8:12] != b"WAVE":
        return None
    info = WemInfo()
    o = 12
    while o + 8 <= len(b):
        cid, size = b[o:o + 4], struct.unpack_from("<I", b, o + 4)[0]
        body = b[o + 8:o + 8 + size]
        if cid == b"fmt " and len(body) >= 8:
            info.codec, info.channels, info.rate = struct.unpack_from("<HHI", body, 0)
            if info.codec == VORBIS and len(body) >= 0x1C:
                info.samples = struct.unpack_from("<I", body, 0x18)[0]
        elif cid == b"data":
            info.data_size = size
        elif cid == b"LIST":
            i = body.find(b"labl")
            if i >= 0 and i + 12 <= len(body):
                n = struct.unpack_from("<I", body, i + 4)[0]
                info.label = body[i + 12:i + 8 + n].split(b"\0")[0].decode("latin-1").strip()
        o += 8 + size + (size & 1)
    return info


def _run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True, creationflags=_NOWIN, **kw)


def wem_to_ogg(wem: bytes, tmp: Path, info: WemInfo) -> bytes:
    src, dst = tmp / "in.wem", tmp / "out.ogg"
    src.write_bytes(wem)
    r = _run([CONFIG.ww2ogg, src, "--pcb", CONFIG.ww2ogg_codebooks, "-o", dst])
    if r.returncode != 0 or not dst.exists():
        raise RuntimeError("ww2ogg: " + (r.stdout + r.stderr).decode("latin-1", "replace").strip()[-200:])
    return fix_granules(dst.read_bytes(), info.samples)


def wem_to_wav(wem: bytes, tmp: Path) -> bytes:
    src, dst = tmp / "in.wem", tmp / "out.wav"
    src.write_bytes(wem)
    r = _run([CONFIG.vgmstream, "-o", dst, src])
    if r.returncode != 0 or not dst.exists():
        raise RuntimeError("vgmstream: " + (r.stdout + r.stderr).decode("latin-1", "replace").strip()[-200:])
    return dst.read_bytes()


def _ffmpeg(wav: bytes, args: list[str], out: Path) -> bytes:
    """Encode through a real file, not a pipe: FLAC and MP3 headers (total samples, duration) are rewritten
    once encoding ends, which a non-seekable output cannot do."""
    r = _run([CONFIG.ffmpeg, "-v", "error", "-y", "-f", "wav", "-i", "pipe:0", *args, out], input=wav)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError("ffmpeg: " + r.stderr.decode("latin-1", "replace").strip()[-200:])
    return out.read_bytes()


def convert(wem: bytes, fmt: str = "auto") -> tuple[str, bytes, WemInfo]:
    """-> (extension, file bytes, info)."""
    info = wem_info(wem) or WemInfo()
    with tempfile.TemporaryDirectory(prefix="omni_snd_") as t:
        tmp = Path(t)
        if fmt == "auto" and info.codec == VORBIS:
            try:
                return ".ogg", wem_to_ogg(wem, tmp, info), info
            except Exception:
                pass                                  # unusual Vorbis variant: decode instead (lossless FLAC)
        wav = wem_to_wav(wem, tmp)
        i = wav.find(b"data")
        if i >= 0 and info.channels:
            info.samples = struct.unpack_from("<I", wav, i + 4)[0] // (2 * info.channels)
        if fmt == "wav":
            return ".wav", wav, info
        if fmt == "mp3":
            # MP3 holds at most 2 channels: surround (4/6 ch) ambiences are downmixed to stereo
            down = ["-ac", "2"] if info.channels > 2 else []
            return ".mp3", _ffmpeg(wav, [*down, "-c:a", "libmp3lame", "-q:a", "0", "-map_metadata", "-1"], tmp / "out.mp3"), info
        return ".flac", _ffmpeg(wav, ["-c:a", "flac", "-compression_level", "12", "-map_metadata", "-1"], tmp / "out.flac"), info
