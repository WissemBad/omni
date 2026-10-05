"""Log files and crash capture of the packaged (console-less) app, checked in a child process."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

CODE = textwrap.dedent("""
    import sys, threading, logging
    sys.stderr = sys.stdout = None          # a windowed process has no console
    from omni.core import log
    path = log.setup()
    logging.getLogger("omni.test").error("visible error")
    print("stray print")
    t = threading.Thread(target=lambda: 1 / 0, name="boom"); t.start(); t.join()
    sys.__stdout__.write(str(path))
""")


def test_errors_prints_and_thread_crashes_reach_the_log(tmp_path):
    env = {**os.environ, "OMNI_HOME": str(tmp_path)}
    root = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = str(root)
    r = subprocess.run([sys.executable, "-c", CODE], capture_output=True, text=True, env=env, cwd=root, timeout=60)
    assert r.returncode == 0, r.stderr
    text = (tmp_path / "workspace" / "logs" / "omni.log").read_text(encoding="utf-8")
    assert "visible error" in text
    assert "stray print" in text
    assert "ZeroDivisionError" in text and "thread boom" in text
