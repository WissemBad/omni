"""Compare the numpy decoder with the legacy add-on decoder (pure-python part, bpy stubbed)."""
import sys, types, time, pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]


class _Any:
    def __init__(self, *a, **k): pass
    def __getattr__(self, n): return _Any()
    def __call__(self, *a, **k): return _Any()


for name in ["bpy", "bpy_extras", "bpy_extras.io_utils", "bpy.props", "bpy.types", "mathutils"]:
    sys.modules.setdefault(name, types.ModuleType(name))
for n in "Vector Quaternion Matrix".split():
    setattr(sys.modules["mathutils"], n, _Any)
sys.modules["bpy_extras.io_utils"].ImportHelper = object
sys.modules["bpy_extras.io_utils"].ExportHelper = object
for n in "StringProperty BoolProperty CollectionProperty IntProperty FloatProperty FloatVectorProperty EnumProperty PointerProperty".split():
    setattr(sys.modules["bpy.props"], n, lambda *a, **k: None)
sys.modules["bpy.types"].Operator = object
sys.modules["bpy.types"].PropertyGroup = object
for n in ("types", "props"):
    setattr(sys.modules["bpy"], n, sys.modules["bpy." + n])
for n in ("utils", "app", "path", "data"):
    setattr(sys.modules["bpy"], n, _Any())

src = (ROOT / "Tools/io_scene_glacier2_007_original.py").read_text(encoding="utf-8")
src = src.replace("(bpy.types.Operator)", "(object)").replace("(bpy.types.PropertyGroup)", "(object)")
src = src.replace("(bpy.types.UIList)", "(object)")
mod = types.ModuleType("legacy")
mod.__file__ = "legacy.py"
try:
    exec(compile(src, "legacy.py", "exec"), mod.__dict__)
except Exception as e:  # classes below the reader may fail to define; reader code is defined first
    print("legacy import note:", type(e).__name__, e)

sys.path.insert(0, str(ROOT / "omni"))
from omni.sources.glacier.prim import parse_prim  # noqa: E402

for h in sys.argv[1:]:
    p = ROOT / "Assets/Sorted/chunk0/PRIM" / f"{h}.PRIM"
    data = p.read_bytes()
    t = time.perf_counter(); old = mod.read_prim_bytes(data); t_old = time.perf_counter() - t
    t = time.perf_counter(); new = parse_prim(data); t_new = time.perf_counter() - t
    ok = len(old.header.object_table) == len(new.meshes)
    for a, b in zip(old.header.object_table, new.meshes):
        vs = a.vertexBuffer.vertices
        pa = np.array([v.position[:3] for v in vs], np.float32)
        ua = np.array([v.uv[0] for v in vs], np.float32)
        ok &= np.allclose(pa, b.positions, atol=1e-5) and np.allclose(ua, b.uvs, atol=1e-5)
        ok &= bool((np.array(a.indices) == b.indices).all())
    print(h, "meshes", len(new.meshes), "weighted", new.weighted, "match", ok,
          "old %.3fs new %.4fs" % (t_old, t_new))
