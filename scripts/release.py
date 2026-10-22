"""Bump the version everywhere and roll the changelog: ``uv run python scripts/release.py 0.4.0``.

Updates pyproject.toml, omni/__init__.py, native/Cargo.toml, native/pyproject.toml, web/package.json and the version
resource of the executable (packaging/version.txt), and turns the ``Unreleased`` section of CHANGELOG.md into the new
version (dated today) with a fresh empty ``Unreleased`` above it.

``uv run python scripts/release.py --check`` (CI) fails when the versions disagree or the executable's version
resource is out of date.
"""
from __future__ import annotations

import datetime
import json
import re
import subprocess
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


VERSION_FILES = {
    "pyproject.toml": r'^version = "(.+)"',
    "omni/__init__.py": r'^__version__ = "(.+)"',
    "native/Cargo.toml": r'^version = "(.+)"',
    "native/pyproject.toml": r'^version = "(.+)"',
}


def read_versions() -> dict[str, str]:
    out = {}
    for path, pattern in VERSION_FILES.items():
        m = re.search(pattern, (ROOT / path).read_text(encoding="utf-8"), flags=re.M)
        out[path] = m.group(1) if m else "?"
    out["web/package.json"] = json.loads((ROOT / "web" / "package.json").read_text(encoding="utf-8")).get("version", "?")
    return out


def version_info(version: str) -> str:
    """The VSVERSIONINFO PyInstaller embeds in Omni.exe (file properties: version, publisher, product)."""
    a, b, c = (int(x) for x in version.split("."))
    return f"""# UTF-8
# Written by scripts/release.py: do not edit.
VSVersionInfo(
  ffi=FixedFileInfo(filevers=({a}, {b}, {c}, 0), prodvers=({a}, {b}, {c}, 0), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040C04B0', [
      StringStruct('CompanyName', "Wissem's Industries"),
      StringStruct('FileDescription', 'Omni'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'Omni'),
      StringStruct('OriginalFilename', 'Omni.exe'),
      StringStruct('ProductName', 'Omni'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1036, 1200])])
  ]
)
"""


def check() -> int:
    versions = read_versions()
    problems = []
    if len(set(versions.values())) != 1:
        problems.append("versions disagree: " + ", ".join(f"{k}={v}" for k, v in versions.items()))
    current = next(iter(versions.values()))
    info = ROOT / "packaging" / "version.txt"
    if not info.exists() or info.read_text(encoding="utf-8") != version_info(current):
        problems.append(f"packaging/version.txt is not the version resource of {current}: run scripts/release.py {current}")
    if "## [Unreleased]" not in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"):
        problems.append("CHANGELOG.md has no Unreleased section")
    for p in problems:
        print("FAIL:", p)
    if not problems:
        print(f"versions agree: {current}")
    return 1 if problems else 0


def main(version: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit("usage: release.py <major.minor.patch>")
    sub("pyproject.toml", r'^version = ".*"', f'version = "{version}"')
    sub("omni/__init__.py", r'^__version__ = ".*"', f'__version__ = "{version}"')
    sub("native/Cargo.toml", r'^version = ".*"', f'version = "{version}"')
    sub("native/pyproject.toml", r'^version = ".*"', f'version = "{version}"')
    (ROOT / "packaging" / "version.txt").write_text(version_info(version), encoding="utf-8", newline="\n")
    pkg =ROOT / "web" / "package.json"
    data = json.loads(pkg.read_text(encoding="utf-8"))
    data = {k: version if k == "version" else v for k, v in data.items()} if "version" in data else {
        k2: v2 for k, v in data.items() for k2, v2 in (((k, v), ("version", version)) if k == "name" else ((k, v),))}
    pkg.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    log = ROOT / "CHANGELOG.md"
    s = log.read_text(encoding="utf-8")
    if "## [Unreleased]" not in s:
        raise SystemExit("CHANGELOG.md has no Unreleased section")
    today = datetime.date.today().isoformat()
    s = s.replace("## [Unreleased]", f"## [Unreleased]\n\n## [{version}] - {today}", 1)
    log.write_text(s, encoding="utf-8", newline="\n")
    subprocess.run(["uv", "lock"], cwd=ROOT, check=True)
    subprocess.run(["cargo", "update", "-p", "omni_native", "--offline", "--manifest-path", "native/Cargo.toml"],
                   cwd=ROOT, check=True)
    print(f"omni {version}: pyproject.toml, omni/__init__.py, native/Cargo.toml, native/pyproject.toml, web/package.json, "
          "packaging/version.txt, CHANGELOG.md, uv.lock, native/Cargo.lock")
    print("next: commit as 'chore(release): v" + version + "', open the pull request, then push the tag v" + version)


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        raise SystemExit(check())
    if len(sys.argv) != 2:
        raise SystemExit("usage: release.py <major.minor.patch> | --check")
    main(sys.argv[1])
