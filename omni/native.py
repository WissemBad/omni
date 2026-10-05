"""Access to the Rust core (crate in ``native/``).

The same Rust code is shipped two ways:

* ``N``: the CPython extension ``omni_native`` (built by maturin), fastest and multi-threaded. On Windows with Smart
  App Control, a freshly built extension can be refused by the system: the previous build keeps working, newer
  entry points are then missing from it.
* ``W``: the WebAssembly build of the core (``omni/omni_core.wasm``) run by wasmtime: portable, sandboxed, never
  blocked. One instance per thread; wasmtime releases the GIL, so threads run in parallel.

``R`` is what callers use for the core entry points: for each function, the native one when the loaded extension
has it, else the WebAssembly one. Old call sites keep using ``N`` directly (``None`` when the extension is absent),
with their pure-Python fallbacks. ``OMNI_NATIVE=0`` disables both.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np

ENABLED = os.environ.get("OMNI_NATIVE", "1") != "0"
N = None
NATIVE_ERROR = ""
if ENABLED:
    try:
        import omni_native as N  # noqa: N812
    except ImportError as e:  # pragma: no cover - depends on the local build
        N = None
        NATIVE_ERROR = str(e)

AVAILABLE = N is not None
CRATE = Path(__file__).resolve().parents[1] / "native"
WASM = Path(__file__).with_name("omni_core.wasm")


# ------------------------------------------------------------------------------------------------ WebAssembly
class WasmError(ValueError):
    pass


def _pack(args) -> bytes:
    out = bytearray()
    for a in args:
        if isinstance(a, (bytes, bytearray, memoryview)):
            tag, p = 0, bytes(a)
        elif isinstance(a, np.ndarray):
            tag, p = 0, np.ascontiguousarray(a).tobytes()
        elif isinstance(a, str):
            tag, p = 1, a.encode("utf-8")
        elif isinstance(a, bool) or isinstance(a, (int, np.integer)):
            tag, p = 2, struct.pack("<q", int(a))
        elif isinstance(a, (float, np.floating)):
            tag, p = 3, struct.pack("<d", float(a))
        else:
            raise TypeError(f"cannot pass {type(a).__name__} to the core")
        out += struct.pack("<BI", tag, len(p))
        out += p
    return bytes(out)


def _unpack(b: bytes) -> list:
    out, o = [], 0
    while o < len(b):
        tag, n = struct.unpack_from("<BI", b, o)
        p = b[o + 5:o + 5 + n]
        o += 5 + n
        out.append(bytes(p) if tag == 0 else p.decode("utf-8", "replace") if tag == 1
                   else struct.unpack("<q", p)[0] if tag == 2 else struct.unpack("<d", p)[0])
    return out


class Wasm:
    """The WebAssembly core: compiled once (cached on disk), one instance per thread."""

    OPS = {"version": 0, "wem_info": 1, "convert_wem": 2, "sha1": 3, "bank_links": 4, "encode_vtf": 5,
           "decode_vtf": 6, "png_rgba": 7, "vtf_png": 8, "texture_rgba": 9, "texture_png": 10,
           "texture_header": 11, "decode_texture": 12, "to_rgba": 13, "encode_dxt": 14, "parse_collision": 15,
           "wem_pcm": 16, "mip_chain": 17}
    MAX_MEMORY = 768 << 20          # an instance that grew past this is dropped after the call

    def __init__(self, path: Path, cache_dir: Path | None = None):
        import wasmtime
        self._wt = wasmtime
        cfg = wasmtime.Config()
        cfg.cranelift_opt_level = "speed"
        self.engine = wasmtime.Engine(cfg)
        data = path.read_bytes()
        digest = hashlib.sha1(data + wasmtime.__name__.encode()).hexdigest()[:16]
        self.module = None
        cache = (cache_dir / f"omni_core_{digest}.cwasm") if cache_dir else None
        if cache and cache.exists():
            try:
                self.module = wasmtime.Module.deserialize(self.engine, cache.read_bytes())
            except Exception:  # noqa: BLE001 - other wasmtime version: compile again
                self.module = None
        if self.module is None:
            self.module = wasmtime.Module(self.engine, data)
            if cache:
                try:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_bytes(self.module.serialize())
                except OSError:
                    pass
        self._local = threading.local()

    def _inst(self):
        st = getattr(self._local, "st", None)
        if st is None:
            store = self._wt.Store(self.engine)
            inst = self._wt.Instance(store, self.module, [])
            ex = inst.exports(store)
            st = (store, ex["memory"], ex["omni_alloc"], ex["omni_call"], ex["omni_free"], ex["omni_last_panic"])
            self._local.st = st
        return st

    def call(self, op: str, *args) -> list:
        store, mem, alloc, call, free, _lp = self._inst()
        payload = _pack(args)
        try:
            ptr = alloc(store, len(payload))
            mem.write(store, payload, ptr)
            r = call(store, self.OPS[op], ptr, len(payload)) & 0xFFFFFFFFFFFFFFFF
            optr, olen = r >> 32, r & 0xFFFFFFFF
            out = bytes(mem.read(store, optr, optr + olen))
            free(store, optr, olen)
        except self._wt.Trap as e:
            msg = str(e).splitlines()[0]
            try:                                      # the panic message the core kept before aborting
                r = _lp(store) & 0xFFFFFFFFFFFFFFFF
                msg = bytes(mem.read(store, r >> 32, (r >> 32) + (r & 0xFFFFFFFF))).decode("utf-8", "replace") or msg
            except Exception:  # noqa: BLE001
                pass
            self._local.st = None                     # a trapped instance is unusable: a new one next time
            raise WasmError(f"core error in {op}: {msg}") from None
        finally:
            if getattr(self._local, "st", None) is not None and mem.data_len(store) > self.MAX_MEMORY:
                self._local.st = None
        res = _unpack(out)
        if res and res[0]:
            raise WasmError(res[0])
        return res[1:]


_wasm: Wasm | None = None
_wasm_lock = threading.Lock()
WASM_ERROR = ""


def wasm() -> Wasm | None:
    global _wasm, WASM_ERROR
    if _wasm is None and ENABLED and WASM.exists() and not WASM_ERROR:
        with _wasm_lock:
            if _wasm is None and not WASM_ERROR:
                try:
                    from .core.config import CONFIG
                    _wasm = Wasm(WASM, CONFIG.cache)
                except Exception as e:  # noqa: BLE001
                    WASM_ERROR = f"{type(e).__name__}: {e}"
    return _wasm


# ------------------------------------------------------------------------------------------------ facade
def _info(f: list) -> dict | None:
    if not f:
        return None
    codec, ch, rate, samples, label, size, align = f[:7]
    return {"codec": codec, "channels": ch, "rate": rate, "samples": None if samples < 0 else samples,
            "label": label, "data_size": size, "block_align": align}


def _rgba(b: bytes, w: int, h: int) -> np.ndarray:
    return np.frombuffer(b, np.uint8).reshape(h, w, 4).copy()


def _write_atomic(path: str | Path, data: bytes) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, p)


class _WasmCore:
    """The core entry points implemented over the WebAssembly module (signatures of the native ones)."""

    def __init__(self, w: Wasm):
        self.w = w

    def version(self) -> str:
        return self.w.call("version")[0]

    def wem_info(self, data):
        return _info(self.w.call("wem_info", data))

    def convert_wem(self, data, fmt="auto", tags=()):
        f = self.w.call("convert_wem", data, fmt, "\n".join(f"{k}={v}" for k, v in tags))
        return f[0], f[1], _info(f[2:])

    def wem_pcm(self, data):
        pcm, ch, rate = self.w.call("wem_pcm", data)
        return pcm, ch, rate

    def bank_links(self, banks, known):
        args = [np.asarray(sorted(set(known)), "<u4").tobytes(), len(banks)]
        for h, d in banks:
            args += [int(h) - (1 << 64) if h >= 1 << 63 else int(h), d]
        j = json.loads(self.w.call("bank_links", *args)[0])
        ids = [(int(h, 16), bid) for h, bid, _m in j["banks"]]
        media = [(int(h, 16), m) for h, _bid, m in j["banks"]]
        links = [(int(k), [(e, p) for e, p in v]) for k, v in j["media"].items()]
        return ids, media, links, j["sourced"], j["events"]

    def encode_vtf(self, path, rgba, format="dxt1", kind="srgb", max_size=0, flags=0, quality=1, coverage=0.0):
        a = np.ascontiguousarray(rgba, np.uint8)
        if N is not None and hasattr(N, "encode_dxt") and format != "bgra8888":
            # hybrid: mip chain from the core, DXT blocks from the (multi-threaded) native encoder
            f = self.w.call("mip_chain", a, a.shape[1], a.shape[0], kind, max_size, float(coverage))
            refl, levels = tuple(f[:3]), [(f[i], f[i + 1], f[i + 2]) for i in range(3, len(f), 3)]
            alpha = format == "dxt5"
            mips = [(w, h, N.encode_dxt(np.frombuffer(px, np.uint8).reshape(h, w, 4), alpha, quality, kind == "normal"))
                    for w, h, px in levels]
            flags |= (0x2000 if alpha else 0) | (0x80 if kind == "normal" else 0)
            _write_atomic(path, vtf_bytes(15 if alpha else 13, mips, flags, refl))
            return levels[0][0], levels[0][1]
        data, w, h = self.w.call("encode_vtf", a, a.shape[1], a.shape[0], format, kind, max_size, flags, quality,
                                 float(coverage))
        _write_atomic(path, data)
        return w, h

    def write_vtf(self, path, fmt, mips, flags=0, reflectivity=(0.5, 0.5, 0.5)):
        _write_atomic(path, vtf_bytes(fmt, mips, flags, reflectivity))

    def decode_vtf(self, data, max_dim=1024):
        px, w, h = self.w.call("decode_vtf", data, max_dim)
        return _rgba(px, w, h)

    def png_rgba(self, rgba, channel="rgb", max_dim=0):
        a = np.ascontiguousarray(rgba, np.uint8)
        return self.w.call("png_rgba", a, a.shape[1], a.shape[0], channel, max_dim)[0]

    def vtf_png(self, data, channel="rgb", max_dim=1024):
        return self.w.call("vtf_png", data, channel, max_dim)[0]

    def texture_rgba(self, text, texd=None, max_dim=0, normal=False):
        px, w, h = self.w.call("texture_rgba", text, texd or b"", max_dim, normal)
        return _rgba(px, w, h)

    def texture_png(self, text, texd=None, channel="rgb", max_dim=1024, normal=False):
        png, w, h = self.w.call("texture_png", text, texd or b"", channel, max_dim, normal)
        return png, (w, h)

    def texture_header(self, text):
        w, h, fmt, mips, ftm = self.w.call("texture_header", text[:0x98])
        return {"width": w, "height": h, "format": fmt, "mips": mips, "first_text_mip": ftm}

    def decode_texture(self, text, texd=None):
        f = self.w.call("decode_texture", text, texd or b"")
        hd = {"format": f[0]}
        return hd, [(f[i], f[i + 1], f[i + 2]) for i in range(1, len(f), 3)]

    def to_rgba(self, fmt, w, h, data):
        return _rgba(self.w.call("to_rgba", fmt, w, h, data)[0], w, h)

    def encode_dxt(self, rgba, alpha, quality=1, normal=False):
        a = np.ascontiguousarray(rgba, np.uint8)
        return self.w.call("encode_dxt", a, a.shape[1], a.shape[0], alpha, quality, normal)[0]

    def parse_collision(self, data):
        return json.loads(self.w.call("parse_collision", data)[0])


def vtf_bytes(fmt: int, mips, flags: int = 0, reflectivity=(0.5, 0.5, 0.5)) -> bytes:
    """VTF 7.2 file from already-encoded mips (largest first), the layout vtf.rs writes."""
    w, h = mips[0][0], mips[0][1]
    hdr = struct.pack("<4sIIIHHIHH4s3f4sfIBIBBH", b"VTF\0", 7, 2, 80, w, h, flags, 1, 0, b"\0" * 4,
                      *reflectivity, b"\0" * 4, 1.0, fmt, len(mips), 0xFFFFFFFF, 0, 0, 1)
    return hdr + b"\0" * (80 - len(hdr)) + b"".join(bytes(m[2]) for m in reversed(mips))


class _Core:
    """``R.<function>``: the native implementation when the loaded extension has it, else the WebAssembly one."""

    def __getattr__(self, name: str):
        if N is not None and hasattr(N, name):
            return getattr(N, name)
        w = wasm()
        if w is not None and hasattr(_WasmCore, name):
            return getattr(_WasmCore(w), name)
        raise AttributeError(f"core function {name} unavailable ({status()})")

    def has(self, name: str) -> bool:
        return (N is not None and hasattr(N, name)) or (wasm() is not None and hasattr(_WasmCore, name))

    def backend(self, name: str) -> str:
        if N is not None and hasattr(N, name):
            return "native"
        return "wasm" if wasm() is not None and hasattr(_WasmCore, name) else "python"


R = _Core()


def status() -> dict:
    w = wasm()
    return {
        "enabled": ENABLED,
        "native": bool(N),
        "native_version": N.version() if N is not None else "",
        "native_error": NATIVE_ERROR,
        "native_functions": sorted(x for x in dir(N) if not x.startswith("_")) if N is not None else [],
        "wasm": w is not None,
        "wasm_version": _WasmCore(w).version() if w is not None else "",
        "wasm_error": WASM_ERROR or ("" if WASM.exists() else "omni_core.wasm absent (python -m omni native --build)"),
    }


def status_line() -> str:
    s = status()
    parts = [f"natif {s['native_version']}" if s["native"] else "natif absent",
             f"wasm {s['wasm_version']}" if s["wasm"] else "wasm absent"]
    return "Rust : " + ", ".join(parts)


def build(release: bool = True, wasm_only: bool = False) -> int:
    """Build the WebAssembly core (always possible) and the CPython extension (maturin develop)."""
    env = dict(os.environ)
    cargo_bin = Path.home() / ".cargo" / "bin"
    env["PATH"] = str(cargo_bin) + os.pathsep + env.get("PATH", "")
    cargo = shutil.which("cargo", path=env["PATH"]) or "cargo"
    subprocess.call([str(cargo_bin / "rustup"), "target", "add", "wasm32-unknown-unknown"], env=env)
    rc = subprocess.call([cargo, "build", "--release", "--target", "wasm32-unknown-unknown", "--no-default-features"],
                         cwd=CRATE, env=env)
    built = CRATE / "target" / "wasm32-unknown-unknown" / "release" / "omni_native.wasm"
    if rc == 0 and built.exists():
        shutil.copyfile(built, WASM)
    if wasm_only or rc != 0:
        return rc
    exe = Path(sys.executable).with_name("maturin.exe" if os.name == "nt" else "maturin")
    base = [str(exe)] if exe.exists() else ["uvx", "maturin"]       # no pip in a uv venv: run maturin through uvx
    env["VIRTUAL_ENV"] = str(Path(sys.executable).parents[1])
    # keep the extension that loads today: a new build may be refused by the system (Smart App Control)
    import importlib.util
    spec = importlib.util.find_spec("omni_native")
    old = Path(spec.submodule_search_locations[0]) / "omni_native.pyd" if spec and spec.submodule_search_locations else None
    backup = old.with_suffix(".pyd.bak") if old and old.exists() else None
    if backup:
        shutil.copyfile(old, backup)
    cmd = base + ["develop", "--uv"] + (["--release"] if release else [])
    rc = subprocess.call(cmd, cwd=CRATE, env=env)
    ok = subprocess.call([sys.executable, "-c", "import omni_native"], env=env) == 0
    if not ok and backup:
        shutil.copyfile(backup, old)
        print("the new extension cannot be loaded on this machine (blocked?): previous one restored; "
              "the WebAssembly core provides the new functions")
    return rc if ok else 0
