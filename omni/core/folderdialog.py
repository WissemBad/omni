"""The Windows "select folder" and "open file" dialogs (the modern IFileOpenDialog), through ctypes: no Tk, no extra
package.

It runs in a child process (``omni pick-folder``), so the packaged app can use it without a window of its own and a
fault in the COM calls can never take the server down.
"""
from __future__ import annotations

import ctypes
import sys
import uuid
from ctypes import POINTER, byref, c_long, c_ulong, c_void_p, wintypes

CLSID_FILE_OPEN_DIALOG = "DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7"
IID_FILE_OPEN_DIALOG = "D57C7288-D4AD-4768-BE02-9D969532D960"
FOS_PICKFOLDERS, FOS_FORCEFILESYSTEM, FOS_PATHMUSTEXIST = 0x20, 0x40, 0x800
SIGDN_FILESYSPATH = 0x80058000
ERROR_CANCELLED = 0x800704C7

# vtable slots: IUnknown 0-2, IModalWindow::Show 3, IFileDialog::SetFileTypes 4, SetOptions 9, GetOptions 10,
# SetTitle 17, GetResult 20; IShellItem::GetDisplayName 5
SHOW, SET_FILE_TYPES, SET_OPTIONS, GET_OPTIONS, SET_TITLE, GET_RESULT, RELEASE, GET_DISPLAY_NAME = 3, 4, 9, 10, 17, 20, 2, 5


class _FilterSpec(ctypes.Structure):
    _fields_ = [("name", wintypes.LPCWSTR), ("spec", wintypes.LPCWSTR)]


class _Guid(ctypes.Structure):
    _fields_ = [("bytes", ctypes.c_ubyte * 16)]

    @classmethod
    def of(cls, text: str) -> "_Guid":
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


def _call(obj: c_void_p, slot: int, *args_and_types):
    """Call method ``slot`` of the COM object ``obj``; arguments are given as (value, ctype) pairs."""
    vtable = ctypes.cast(obj, POINTER(c_void_p))[0]
    fn = ctypes.cast(vtable, POINTER(c_void_p))[slot]
    types = [t for _v, t in args_and_types]
    proto = ctypes.WINFUNCTYPE(c_long, c_void_p, *types)
    return proto(fn)(obj, *[v for v, _t in args_and_types])


def pick_folder(title: str = "Choisir un dossier", files: tuple[str, str] | None = None) -> str:
    """Path chosen by the user, or an empty string when the dialog is cancelled. With ``files`` (name, ``*.a;*.b``)
    the dialog picks a file of those types instead of a folder."""
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx(None, 2)                       # apartment threaded; a mode mismatch is harmless here
    dialog = c_void_p()
    hr = ole32.CoCreateInstance(byref(_Guid.of(CLSID_FILE_OPEN_DIALOG)), None, 1, byref(_Guid.of(IID_FILE_OPEN_DIALOG)),
                                byref(dialog))
    if hr != 0 or not dialog.value:
        raise OSError(f"CoCreateInstance failed (0x{hr & 0xFFFFFFFF:08X})")
    try:
        opts = c_ulong()
        _call(dialog, GET_OPTIONS, (byref(opts), POINTER(c_ulong)))
        flags = opts.value | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST | (0 if files else FOS_PICKFOLDERS)
        _call(dialog, SET_OPTIONS, (flags, c_ulong))
        if files:
            spec = _FilterSpec(*files)
            _call(dialog, SET_FILE_TYPES, (1, ctypes.c_uint), (byref(spec), POINTER(_FilterSpec)))
        _call(dialog, SET_TITLE, (title, wintypes.LPCWSTR))
        owner = ctypes.windll.user32.GetForegroundWindow()
        hr = _call(dialog, SHOW, (owner, wintypes.HWND)) & 0xFFFFFFFF
        if hr == ERROR_CANCELLED:
            return ""
        if hr != 0:
            raise OSError(f"the folder dialog failed (0x{hr:08X})")
        item = c_void_p()
        _call(dialog, GET_RESULT, (byref(item), POINTER(c_void_p)))
        try:
            name = wintypes.LPWSTR()
            _call(item, GET_DISPLAY_NAME, (SIGDN_FILESYSPATH, c_ulong), (byref(name), POINTER(wintypes.LPWSTR)))
            path = name.value or ""
            ole32.CoTaskMemFree(name)
            return path
        finally:
            _call(item, RELEASE)
    finally:
        _call(dialog, RELEASE)


def main(argv: list[str], files: str = "") -> int:
    """``omni pick-folder [--files "name|*.a;*.b"] [title]``: print the chosen folder (or file)."""
    spec = tuple(files.split("|", 1)) if "|" in files else None
    try:
        print(pick_folder(argv[0] if argv else "Choisir un dossier", spec), flush=True)  # type: ignore[arg-type]
        return 0
    except OSError as e:
        print(str(e), file=sys.stderr)
        return 1
