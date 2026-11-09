"""Release signatures (ed25519): the key the updater trusts and the signature of a release's SHA256SUMS.txt.

    uv run --no-sync python scripts/release_sign.py keygen <seed file>       # once; keep the file offline, never commit it
    uv run --no-sync python scripts/release_sign.py public <seed file>       # hex to put in omni/core/update.py (RELEASE_KEY)
    uv run --no-sync python scripts/release_sign.py sign <seed file> <file>  # writes <file>.sig (hex)
    uv run --no-sync python scripts/release_sign.py check <seed file>        # the seed matches RELEASE_KEY

The seed file holds the 32-byte secret in hex. ``build_release.ps1`` reads its path from ``OMNI_RELEASE_KEY``.
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omni.core import update
from omni.native import N


def _seed(path: str) -> bytes:
    seed = bytes.fromhex(Path(path).read_text(encoding="ascii").strip())
    if len(seed) != 32:
        raise SystemExit(f"{path}: 32 bytes expected, {len(seed)} found")
    return seed


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("keygen", "public", "sign", "check"):
        print(__doc__)
        return 2
    cmd, key = argv[0], argv[1]
    if cmd == "keygen":
        p = Path(key)
        if p.exists():
            raise SystemExit(f"{p} exists: refusing to overwrite a release key")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(secrets.token_bytes(32).hex() + "\n", encoding="ascii")
        print(N.release_public_key(_seed(key)).hex())
        return 0
    public = N.release_public_key(_seed(key)).hex()
    if cmd == "public":
        print(public)
        return 0
    if cmd == "check":
        if public != update.RELEASE_KEY:
            raise SystemExit("the release key does not match RELEASE_KEY in omni/core/update.py")
        return 0
    if len(argv) < 3:
        print(__doc__)
        return 2
    target = Path(argv[2])
    sig = N.sign_release(_seed(key), target.read_bytes())
    target.with_name(target.name + ".sig").write_text(sig.hex() + "\n", encoding="ascii")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
