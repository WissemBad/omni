"""Rust core: audio and texture entry points on synthetic data."""
import io
import struct

import numpy as np
import pytest

from omni.native import AVAILABLE, N

pytestmark = pytest.mark.skipif(not AVAILABLE, reason="Rust core unavailable")


def _wem(codec: int, channels: int, rate: int, data: bytes, block_align: int, bits: int, label: str = "") -> bytes:
    fmt = struct.pack("<HHIIHH", codec, channels, rate, rate * channels * 2, block_align, bits)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    if label:
        lab = b"labl" + struct.pack("<II", 4 + len(label) + 1, 0) + label.encode() + b"\0"
        body = b"adtl" + lab
        chunks += b"LIST" + struct.pack("<I", len(body)) + body
    chunks += b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


def test_pcm_to_flac_and_wav_keep_every_sample():
    pcm = (np.sin(np.arange(9000) / 7.0) * 12000).astype("<i2")
    stereo = np.stack([pcm, pcm // 2], 1).reshape(-1)
    wem = _wem(1, 2, 48000, stereo.tobytes(), 4, 16, label="vox_test_line_001")
    info = N.wem_info(wem)
    assert info["channels"] == 2 and info["samples"] == 9000 and info["label"] == "vox_test_line_001"
    ext, flac, info = N.convert_wem(wem, "flac", [("title", "Test")])
    assert ext == ".flac" and flac[:4] == b"fLaC"
    total = int.from_bytes(flac[18:26], "big") & ((1 << 36) - 1)      # STREAMINFO: rate, channels, bits, total
    assert total == 9000
    assert b"TITLE=Test" in flac[:400]
    ext, wav, _ = N.convert_wem(wem, "wav", [])
    assert ext == ".wav" and wav[-len(stereo) * 2:] == stereo.tobytes()


def test_ptadpcm_frame_layout():
    # two silent 0x24-byte frames per channel: 64 samples each (2 history samples + 62 nibbles)
    frame = struct.pack("<hhB", 0, 0, 0) + bytes(0x24 - 5)
    wem = _wem(0x8311, 1, 48000, frame * 2, 0x24, 4)
    info = N.wem_info(wem)
    assert info["samples"] == 128
    ext, wav, _ = N.convert_wem(wem, "wav", [])
    assert ext == ".wav" and len(wav) - 44 == 128 * 2


def test_vtf_round_trip_and_mips():
    import tempfile
    from pathlib import Path
    rng = np.random.default_rng(1)
    img = np.zeros((128, 256, 4), np.uint8)
    img[..., 0] = np.linspace(0, 255, 256)[None, :]
    img[..., 1] = np.linspace(0, 255, 128)[:, None]
    img[..., 2] = rng.integers(100, 140, (128, 256))
    img[..., 3] = 255
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "x.vtf"
        assert tuple(N.encode_vtf(str(p), img, "dxt1", "srgb", 0, 0, 1, 0.0)) == (256, 128)
        b = p.read_bytes()
        assert b[:4] == b"VTF\0" and b[56] == 9                     # 256x128 -> 1x1: 9 levels
        top = N.decode_vtf(b, 4096)
        assert top.shape == (128, 256, 4)
        err = np.abs(top[..., :3].astype(int) - img[..., :3]).mean()
        assert err < 6
        small = N.decode_vtf(b, 32)
        assert small.shape == (16, 32, 4)


def test_png_is_readable():
    from PIL import Image
    img = np.zeros((20, 30, 4), np.uint8)
    img[..., 0] = 200
    img[..., 3] = 255
    png = N.png_rgba(img, "rgb", 0)
    assert Image.open(io.BytesIO(png)).size == (30, 20)


def test_settings_merge_ignores_bad_values():
    from omni.core.settings import DEFAULTS, _merge
    m = _merge(DEFAULTS, {"textures": {"quality": "high", "encoder": "fast", "nope": 1}, "nosection": {}})
    assert m["textures"]["quality"] == "high"
    assert m["textures"]["encoder"] == DEFAULTS["textures"]["encoder"]      # wrong type: default kept
    assert "nope" not in m["textures"] and "nosection" not in m


# ---- glTF writer and bulk readers -------------------------------------------------------------------------------
def _quad():
    return {"positions": np.array([[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]], np.float32),
            "normals": np.tile(np.float32([0, -1, 0]), (4, 1)), "uvs": np.zeros((4, 2), np.float32),
            "indices": np.array([0, 1, 2, 0, 2, 3], np.uint32)}


def test_glb_writer_makes_a_valid_file_and_rejects_bad_meshes(tmp_path):
    g = N.Glb("viewer")
    tex = g.texture(np.full((8, 8, 4), 200, np.uint8), 4)
    jpg = g.texture(np.full((8, 8, 4), 200, np.uint8), 0, 85)
    mat = g.material("m", base=jpg, normal=tex, alpha="MASK")
    g.node("quad", g.mesh("quad", [{**_quad(), "material": mat}]))
    out = tmp_path / "deep" / "quad.glb"
    g.save(out)
    data = out.read_bytes()
    assert data[:4] == b"glTF" and int.from_bytes(data[8:12], "little") == len(data)
    assert not list(out.parent.glob("*.part"))                    # written atomically
    assert data == g.to_bytes()
    bad = _quad()
    bad["indices"] = np.array([0, 1, 9], np.uint32)
    with pytest.raises(ValueError, match="index"):
        N.Glb().mesh("bad", [bad])
    nan = _quad()
    nan["positions"][0, 0] = np.nan
    with pytest.raises(ValueError, match="non-finite"):
        N.Glb().mesh("nan", [nan])
    with pytest.raises(ValueError):
        N.Glb("sideways")
    with pytest.raises(ValueError):
        N.Glb().texture(np.zeros((4, 4, 3), np.uint8))
    with pytest.raises(ValueError):
        N.Glb().material("m", base=3)                             # no such texture


def test_bulk_readers_follow_the_files(tmp_path):
    prim = tmp_path / "a.PRIM"
    prim.write_bytes((16).to_bytes(8, "little") + bytes(8) + bytes([0, 0, 0, 0, 0b1100, 0, 0, 0]))
    wem = tmp_path / "b.wem"
    wem.write_bytes(_wem(1, 1, 8000, bytes(16), 2, 16, label="vox_unit_test_001"))
    marker = tmp_path / "c.wem"
    marker.write_bytes(_wem(1, 1, 8000, bytes(16), 2, 16, label="Marker 2"))
    assert N.prim_headers([str(prim), str(tmp_path / "none")]) == [(prim.stat().st_size, 0b1100), None]
    assert N.wem_labels([(str(wem), 0, -1), (str(marker), 0, -1), (str(tmp_path / "none"), 0, -1)]) == ["vox_unit_test_001", "", ""]
    assert N.read_files([str(wem), str(tmp_path / "none")]) == [wem.read_bytes(), None]
    meta = bytearray(40)
    meta[24] = 1
    meta += (2).to_bytes(2, "little") + bytes([0, 0, 1, 7]) + (0xAA).to_bytes(8, "little") + (0xBB).to_bytes(8, "little")
    (tmp_path / "a.PRIM.meta").write_bytes(bytes(meta))
    assert N.meta_refs_flags([str(prim), str(tmp_path / "none")]) == [[(0xAA, 1), (0xBB, 7)], None]
