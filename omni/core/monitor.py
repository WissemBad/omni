"""Machine load for the home page: CPU, memory, and the NVIDIA GPU when ``nvidia-smi`` is there.

CPU and memory come from Windows itself (no extra dependency). The CPU temperature is not exposed by Windows without a
hardware driver, so it is reported as absent rather than guessed.
"""
from __future__ import annotations

import ctypes
import shutil
import subprocess
import threading
import time
from ctypes import wintypes

_lock = threading.Lock()
_last_times: tuple[int, int, int] | None = None
_gpu_cache: tuple[float, dict | None] = (0.0, None)
NOWINDOW = 0x08000000


class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD), ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64), ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64), ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64), ("sullAvailExtendedVirtual", ctypes.c_uint64)]


def _times() -> tuple[int, int, int]:
    idle, kernel, user = (wintypes.FILETIME() for _ in range(3))
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    f = lambda t: (t.dwHighDateTime << 32) | t.dwLowDateTime  # noqa: E731
    return f(idle), f(kernel), f(user)


def cpu_load() -> float:
    """Percent of the CPU busy since the previous call (kernel time includes idle time)."""
    global _last_times
    with _lock:
        now = _times()
        prev, _last_times = _last_times, now
    if prev is None:
        time.sleep(0.2)
        return cpu_load()
    idle, total = now[0] - prev[0], (now[1] - prev[1]) + (now[2] - prev[2])
    return round(100.0 * (1 - idle / total), 1) if total > 0 else 0.0


def memory() -> dict:
    s = _MemStatus()
    s.dwLength = ctypes.sizeof(_MemStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
    return {"total": s.ullTotalPhys, "used": s.ullTotalPhys - s.ullAvailPhys, "percent": s.dwMemoryLoad}


def gpu() -> dict | None:
    """Name, load, temperature, memory and power of the first NVIDIA GPU (cached one second), None without one."""
    global _gpu_cache
    at, value = _gpu_cache
    if time.time() - at < 1.0:
        return value
    exe = shutil.which("nvidia-smi")
    value = None
    if exe:
        try:
            r = subprocess.run([exe, "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total,power.draw",
                                "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5, creationflags=NOWINDOW)
            name, load, temp, used, total, power = [x.strip() for x in r.stdout.splitlines()[0].split(",")]
            num = lambda x: float(x) if x.replace(".", "", 1).isdigit() else None  # noqa: E731
            value = {"name": name, "load": num(load), "temp": num(temp), "mem_used": num(used), "mem_total": num(total), "power": num(power)}
        except (OSError, subprocess.SubprocessError, IndexError, ValueError):
            value = None
    _gpu_cache = (time.time(), value)
    return value


def sample() -> dict:
    return {"t": time.time(), "cpu": cpu_load(), "memory": memory(), "gpu": gpu(), "cpu_temp": None}
