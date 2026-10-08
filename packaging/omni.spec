# PyInstaller spec: ``uv run pyinstaller packaging/omni.spec --noconfirm`` -> dist/Omni/Omni.exe
# Needs the web build (web/.output/public) and the native module (omni_native) installed.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
datas, binaries, hidden = [], [], []

for pkg in ("webview", "py7zr", "coacd", "scipy", "pygltflib", "vpk", "omni_native"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hidden += h
# sources are imported by name (sources/registry.py): list them, a collection that fails on one would drop it silently
hidden += ["omni.sources.unreal.adapter", "omni.sources.glacier.hitman", "omni.sources.glacier.adapter"]
hidden += collect_submodules("uvicorn") + collect_submodules("omni") + [
    "webview.platforms.edgechromium", "clr_loader", "pythonnet", "multiprocessing", "pystray._win32",
    "pyppmd", "pybcj", "brotli", "zstandard", "inflate64", "multivolumefile", "texttable", "Cryptodome",
]
datas += [
    (str(ROOT / "web" / ".output" / "public"), "web/.output/public"),
    (str(ROOT / "omni" / "assets"), "omni/assets"),
]

a = Analysis(
    [str(ROOT / "packaging" / "omni_app.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "pandas", "PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy", "gi"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Omni",
    console=False,
    icon=str(ROOT / "omni" / "assets" / "omni.ico"),
    version=str(ROOT / "packaging" / "version.txt") if (ROOT / "packaging" / "version.txt").exists() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Omni")
