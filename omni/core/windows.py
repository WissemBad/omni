"""Windows integration: Steam libraries, folders opened in Explorer, directory junctions, native dialogs."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

NOWINDOW = 0x08000000 if os.name == "nt" else 0       # CREATE_NO_WINDOW for child processes


def steam_root() -> Path | None:
    try:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    value = winreg.QueryValueEx(k, "SteamPath" if hive == winreg.HKEY_CURRENT_USER else "InstallPath")[0]
                    if value and Path(value).is_dir():
                        return Path(value)
            except OSError:
                continue
    except ImportError:
        pass
    default = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam"
    return default if default.is_dir() else None


def steam_libraries() -> list[Path]:
    """Every Steam library folder (the Steam folder itself + those of ``libraryfolders.vdf``)."""
    root = steam_root()
    if root is None:
        return []
    libs = [root]
    vdf = root / "steamapps" / "libraryfolders.vdf"
    try:
        for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="replace")):
            p = Path(m.group(1).replace("\\\\", "\\"))
            if p.is_dir() and p not in libs:
                libs.append(p)
    except OSError:
        pass
    return libs


def find_steam_game(*names: str) -> Path | None:
    """The install folder of a Steam game by (part of) its manifest name or folder name."""
    wanted = [n.lower() for n in names]
    for lib in steam_libraries():
        apps = lib / "steamapps"
        for acf in apps.glob("appmanifest_*.acf"):
            try:
                text = acf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            name = re.search(r'"name"\s+"([^"]*)"', text)
            folder = re.search(r'"installdir"\s+"([^"]*)"', text)
            if not folder:
                continue
            hay = f"{name.group(1) if name else ''} {folder.group(1)}".lower()
            path = apps / "common" / folder.group(1)
            if any(w in hay for w in wanted) and path.is_dir():
                return path
    return None


def reveal(path: Path) -> None:
    """Show a file (selected) or a folder in Explorer."""
    if path.is_file():
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        os.startfile(str(path))                           # noqa: S606 - Windows only


def is_link(p: Path) -> bool:
    try:
        return bool(os.readlink(p))
    except OSError:
        return False


def make_junction(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True,
                       creationflags=NOWINDOW)
    if r.returncode != 0:
        raise RuntimeError((r.stdout + r.stderr).strip())


def remove_junction(link: Path) -> None:
    os.rmdir(link)                                        # removes the junction only, never the target's content


_picker = None


def set_picker(fn) -> None:
    """The desktop window registers its native dialog here (``fn(title) -> path``)."""
    global _picker
    _picker = fn


def pick_folder(title: str = "Choisir un dossier") -> str:
    """Native folder dialog: the app window's own when running as the desktop app, else the Windows dialog in a
    child process (the API runs on the user's PC; a fault in the dialog cannot take the server down)."""
    if _picker is not None:
        return _picker(title) or ""
    import sys
    cmd = [sys.executable, "pick-folder", title] if getattr(sys, "frozen", False) else [sys.executable, "-m", "omni", "pick-folder", title]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, creationflags=NOWINDOW)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "folder dialog failed").strip())
    return r.stdout.strip()


_JOB = None


def tie_children_to_this_process() -> bool:
    """Put this process in a Job Object that kills every process of the job when its last handle closes: the
    processes it starts (studiomdl) then die with it, even when it is killed (a cancelled or crashed batch worker
    left studiomdl running, sometimes for ever on a hull it cannot build). Windows only; False if not possible."""
    global _JOB
    if os.name != "nt" or _JOB is not None:
        return _JOB is not None
    import ctypes
    from ctypes import wintypes

    class _Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                   "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", _IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return False
    info = _Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000                 # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)) \
            or not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
        k32.CloseHandle(job)
        return False
    _JOB = job                                                     # kept open for the life of the process
    return True
