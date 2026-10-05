"""Bump the version everywhere and roll the changelog: ``uv run python scripts/release.py 0.4.0``.

Updates pyproject.toml, omni/__init__.py, native/Cargo.toml and web/package.json, and turns the ``Unreleased``
section of CHANGELOG.md into the new version (dated today) with a fresh empty ``Unreleased`` above it.
"""
from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sub(path: str, pattern: str, repl: str, count: int = 1) -> None:
    p = ROOT / path
    s = p.read_text(encoding="utf-8")
    new, n = re.subn(pattern, repl, s, count=count, flags=re.M)
    if not n:
        raise SystemExit(f"{path}: version not found")
    p.write_text(new, encoding="utf-8", newline="\n")


def main(version: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit("usage: release.py <major.minor.patch>")
    sub("pyproject.toml", r'^version = ".*"', f'version = "{version}"')
    sub("omni/__init__.py", r'^__version__ = ".*"', f'__version__ = "{version}"')
    sub("native/Cargo.toml", r'^version = ".*"', f'version = "{version}"')
    pkg = ROOT / "web" / "package.json"
    data = json.loads(pkg.read_text(encoding="utf-8"))
    data["version"] = version
    pkg.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    log = ROOT / "CHANGELOG.md"
    s = log.read_text(encoding="utf-8")
    if "## [Unreleased]" not in s:
        raise SystemExit("CHANGELOG.md has no Unreleased section")
    today = datetime.date.today().isoformat()
    s = s.replace("## [Unreleased]", f"## [Unreleased]\n\n## [{version}] - {today}", 1)
    log.write_text(s, encoding="utf-8", newline="\n")
    print(f"omni {version}: pyproject.toml, omni/__init__.py, native/Cargo.toml, web/package.json, CHANGELOG.md")
    print("next: commit as 'chore(release): v" + version + "', open the pull request, then push the tag v" + version)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: release.py <major.minor.patch>")
    main(sys.argv[1])
