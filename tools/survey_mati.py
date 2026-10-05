"""Survey of material classes (MATE refs) and texture slot names across all MATI."""
import sys, pathlib, collections, time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "omni"))
from omni.sources.glacier.meta import parse_meta, load_names  # noqa: E402
from omni.sources.glacier.mati import parse_mati  # noqa: E402

t0 = time.perf_counter()
names = load_names(ROOT / "workspace/names/hash_list.txt")
print("names", len(names), "%.1fs" % (time.perf_counter() - t0))
classes = collections.Counter()
slots = collections.Counter()
by_class_slots = collections.defaultdict(collections.Counter)
bad = 0
tot = 0
for chunk in ("chunk0", "chunk1"):
    d = ROOT / "Assets/Sorted" / chunk / "MATI"
    if not d.exists():
        continue
    for p in d.glob("*.MATI"):
        tot += 1
        try:
            m = parse_meta(Path(str(p) + ".meta").read_bytes()) if False else parse_meta(open(str(p) + ".meta", "rb").read())
            mt = parse_mati(p.read_bytes())
        except Exception:
            bad += 1
            continue
        if not mt.ok:
            bad += 1
            continue
        cls = "?"
        for h, f in m.refs:
            n = names.get(h, "")
            if ".materialclass" in n or n.endswith(".mate"):
                cls = n.split("materialclasses/")[-1][:70]
                break
        classes[cls] += 1
        for t in mt.textures:
            slots[t.name] += 1
            by_class_slots[cls][t.name] += 1
print("total", tot, "bad", bad)
print("--- classes")
for k, v in classes.most_common(40):
    print("%6d %s" % (v, k))
print("--- slots")
for k, v in slots.most_common(40):
    print("%6d %s" % (v, k))
print("--- per class slot signature (top classes)")
for cls, _ in classes.most_common(18):
    sig = collections.Counter()
    print("##", cls, classes[cls])
    for s, c in by_class_slots[cls].most_common(12):
        print("      %5d %s" % (c, s))
