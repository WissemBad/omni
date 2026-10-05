"""Read back a VTF written by this tool: used to preview the real output."""
from __future__ import annotations

from pathlib import Path

import numpy as np


def read_vtf(path: Path, max_dim: int = 1024) -> np.ndarray:
    """RGBA of the largest mip not exceeding ``max_dim`` (Rust core)."""
    from ...native import N
    return N.decode_vtf(path.read_bytes(), max_dim)
