"""studiomdl invocation (StudioMDL-CE by default) inside the isolated sandbox mod."""
from __future__ import annotations

import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ...core.config import CONFIG
from ...core.windows import NOWINDOW


@dataclass
class CompileResult:
    ok: bool
    log: str
    files: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timed_out: bool = False


def ensure_sandbox(sandbox: Path) -> None:
    """Isolated mod for studiomdl. GMod's own VPKs are mounted read-only so that $includemodel
    (e.g. the stock player animations m_anm.mdl) can be resolved."""
    sandbox.mkdir(parents=True, exist_ok=True)
    gm = (CONFIG.gmod / "garrysmod").as_posix()
    t = "\t"
    # the staging addons are mounted too (after the sandbox itself), only so that a model viewer (hlmv -game
    # <sandbox>) finds the converted materials; studiomdl writes into the sandbox and never reads them.
    addons = [f'{t}{t}{t}game{t}{t}{t}"{d.as_posix()}"' for d in sorted((CONFIG.workspace / "addons").glob("omni_*")) if d.is_dir()]
    lines = [
        '"GameInfo"', "{", f'{t}game{t}"omni sandbox"', f'{t}title{t}"omni"', f"{t}type{t}singleplayer_only",
        f"{t}FileSystem", f"{t}{{", f"{t}{t}SteamAppId{t}4000", f"{t}{t}SearchPaths", f"{t}{t}{{",
        f"{t}{t}{t}game+mod{t}{t}|gameinfo_path|.",
        *addons,
        f'{t}{t}{t}game{t}{t}{t}"{gm}/garrysmod.vpk"',
        f'{t}{t}{t}game{t}{t}{t}"{gm}/fallbacks.vpk"',
        f"{t}{t}}}", f"{t}}}", "}",
    ]
    text = "\n".join(lines) + "\n"
    gi = sandbox / "gameinfo.txt"
    if gi.exists() and gi.read_text() == text:
        return
    tmp = gi.with_name(f"gameinfo.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text)
    try:
        os.replace(tmp, gi)               # never a half-written file under a running studiomdl
    except OSError:
        tmp.unlink(missing_ok=True)       # another worker holds it open; it writes the same content


def compile_qc(qc: Path, sandbox: Path | None = None, studiomdl: Path | None = None, timeout: int = 60) -> CompileResult:
    sandbox = sandbox or CONFIG.sandbox
    studiomdl = studiomdl or CONFIG.studiomdl
    ensure_sandbox(sandbox)
    try:
        p = subprocess.run(
            [str(studiomdl), "-nop4", "-game", str(sandbox), str(qc)],
            capture_output=True, text=True, errors="replace", timeout=timeout, cwd=str(qc.parent),
            creationflags=NOWINDOW,
        )
    except subprocess.TimeoutExpired:
        return CompileResult(False, "", [], [f"studiomdl timed out after {timeout}s"], [], timed_out=True)
    log = (p.stdout or "") + (p.stderr or "")
    errors = [l.strip() for l in log.splitlines() if re.search(r"\bERROR\b", l, re.I)]
    warnings = [w.strip() for w in re.findall(r"WARNING:[^\n]*", log)
                if "Collision building" not in w and "no any vertex animations" not in w
                and "fastest tracing" not in w and "2-dimensional geometry" not in w
                and "bounding box out of range" not in w]            # harmless: a prop bigger than the idle box
    ok = p.returncode == 0 and "Completed" in log and not errors
    return CompileResult(ok, log, [], errors, warnings)
