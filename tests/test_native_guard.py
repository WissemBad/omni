"""The Rust core must answer corrupt input with an exception a handler can catch, never abort or raise a
BaseException (``except Exception`` in the batch code would let it end the whole run)."""
from __future__ import annotations

import struct

import numpy as np
import pytest

from omni.native import N, NativeError

pytestmark = pytest.mark.skipif(N is None, reason="native module not built")


def test_panics_become_native_error(monkeypatch):
    class Raw:
        @staticmethod
        def boom():
            raise type("PanicException", (BaseException,), {})("index out of bounds")

    from omni.native import _Guarded
    with pytest.raises(NativeError, match="boom"):
        _Guarded(Raw).boom()
    try:
        _Guarded(Raw).boom()
    except Exception as e:                                       # must be catchable by ordinary handlers
        assert isinstance(e, NativeError)


@pytest.mark.parametrize("w,h", [(0, 4), (4, 0), (0, 0)])
def test_empty_images_are_errors(w, h):
    with pytest.raises(Exception):
        N.to_rgba("BC1", w, h, b"\0" * 16)
    with pytest.raises(Exception):
        N.png_rgba(np.zeros((max(h, 1), max(w, 1), 4), np.uint8)[:0], "rgba", 0)


def test_rpkg_with_absurd_counts_is_rejected(tmp_path):
    f = tmp_path / "chunk0.rpkg"
    f.write_bytes(b"2KPR" + struct.pack("<IBBBBBH", 0, 0, 0, 0, 0, 0, 0)[:11] + struct.pack("<III", 0xFFFFFFFF, 0xFFFFFFFF, 0) + b"\0" * 64)
    with pytest.raises(Exception):
        N.rpkg_info(str(f))


def test_truncated_wem_does_not_panic():
    riff = b"RIFF" + struct.pack("<I", 0xFFFFFFF0) + b"WAVE" + b"fmt " + struct.pack("<I", 0xFFFFFFFF) + b"\0" * 8
    try:
        N.wem_info(riff)
        N.convert_wem(riff, "auto", [])
    except Exception:
        pass                                                      # an error is fine; a panic or an abort is not
