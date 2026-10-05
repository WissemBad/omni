"""Audiokinetic Wwise media (.wem): header facts. The conversion itself is done by the Rust core (``native/src/audio.rs``).

Formats of an export (see export.py):
  auto  Wwise Vorbis -> .ogg: the game's Vorbis packets rewrapped as a standard Ogg Vorbis file (exact granules, tags),
        bit-identical audio. Any other codec (Platinum ADPCM, PCM) is decoded to lossless .flac.
  ogg   everything as .ogg: Vorbis rewrapped, the rest encoded to Vorbis (ffmpeg).
  flac  everything decoded, lossless FLAC.
  wav   everything decoded, 16-bit PCM WAV.
  mp3   everything decoded and encoded MP3 V0 (ffmpeg).
"""
from __future__ import annotations

from dataclasses import dataclass

from ...native import N

FORMATS = ("auto", "ogg", "flac", "wav", "mp3")
VORBIS = 0xFFFF


@dataclass
class WemInfo:
    codec: int = -1
    channels: int = 0
    rate: int = 0
    samples: int | None = None          # Wwise Vorbis / ADPCM / PCM: exact count from the header
    label: str = ""                     # original file name kept by Wwise (LIST/labl), may be empty
    data_size: int = 0


def wem_info(b: bytes) -> WemInfo | None:
    d = N.wem_info(b)
    if not d:
        return None
    return WemInfo(d["codec"], d["channels"], d["rate"], d["samples"], d["label"], d["data_size"])
