"""Print the changelog section of a version (the body of the GitHub release): ``release_notes.py 0.3.0``."""
import re
import sys
from pathlib import Path

version = sys.argv[1]
text = (Path(__file__).resolve().parents[1] / "CHANGELOG.md").read_text(encoding="utf-8")
m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.S | re.M)
if not m:
    raise SystemExit(f"no changelog section for {version}")
print(m.group(1).strip())
