"""Only one copy of the program may work with one data folder.

Two copies on one folder would each resume the saved queue (starting every unfinished job twice),
rewrite `queue.json`, `library.json` and `config.json` behind each other's back, and — as soon as the
storage becomes a database — open the same SQLite file from two processes.

The guard is a named Windows mutex derived from the data folder: the system releases it when the
process ends, however it ends (a crash, the Task Manager, a power cut), so there is nothing left
behind to clean up. A lock file would survive a crash and would then wrongly keep the program shut.

Two different data folders stay possible on purpose: a second library is a legitimate setup.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from storage.atomic import atomic_write_json

ERROR_ALREADY_EXISTS = 183
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259

_lib = None      # kernel32 with the signatures set
_held = None     # the handle we keep open for the life of the process
_held_dir = None


def mutex_name(data_dir):
    """The mutex of one folder. The same folder always gives the same name, on every copy."""
    key = os.path.normcase(str(Path(data_dir).resolve())).encode("utf-8", "replace")
    return "Local\\h3studio-data-" + hashlib.sha1(key).hexdigest()[:16]


def _kernel32():
    global _lib
    if _lib is None:
        lib = ctypes.WinDLL("kernel32", use_last_error=True)
        lib.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        lib.CreateMutexW.restype = ctypes.c_void_p
        lib.CloseHandle.argtypes = [ctypes.c_void_p]
        lib.CloseHandle.restype = ctypes.c_bool
        lib.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
        lib.OpenProcess.restype = ctypes.c_void_p
        lib.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        lib.GetExitCodeProcess.restype = ctypes.c_bool
        _lib = lib
    return _lib


def acquire(data_dir):
    """Take the folder for this process. True when we are the only copy working with it."""
    global _held, _held_dir
    if sys.platform != "win32":
        return True   # the program is Windows-only; the checks run on Windows too
    if _held is not None:
        return True   # already ours
    try:
        lib = _kernel32()
        handle = lib.CreateMutexW(None, False, mutex_name(data_dir))
    except Exception:
        return True   # a system call that cannot be made must not stop the program
    if not handle:
        return True
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        lib.CloseHandle(handle)
        return False
    _held, _held_dir = handle, Path(data_dir)
    return True


def release():
    """Give the folder back (and take our `instance.json` away). Safe to call twice."""
    global _held, _held_dir
    if _held_dir is not None:
        forget(_held_dir, pid=os.getpid())
    if _held is not None and sys.platform == "win32":
        try:
            _kernel32().CloseHandle(_held)
        except Exception:
            pass
    _held, _held_dir = None, None


def is_alive(pid):
    """Whether a process with this id exists. When it cannot be asked, it is assumed alive."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            lib = _kernel32()
            handle = lib.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        except Exception:
            return True
        if not handle:
            return False
        try:
            code = ctypes.c_uint32()
            if not lib.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == STILL_ACTIVE
        finally:
            lib.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def publish(data_dir, port=None, url=None):
    """Say where the running copy is, so a second launch can tell the user instead of guessing."""
    data = {"pid": os.getpid(), "port": port, "url": url or (f"http://127.0.0.1:{port}" if port else None),
            "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "data": str(data_dir)}
    try:
        atomic_write_json(Path(data_dir) / "instance.json", data)
    except OSError:
        pass
    return data


def published(data_dir):
    """What the running copy wrote about itself, or None when there is no live copy."""
    try:
        data = json.loads((Path(data_dir) / "instance.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("pid") is not None and not is_alive(data["pid"]):
        return None   # written by a copy that is gone
    return data


def forget(data_dir, pid=None):
    """Remove `instance.json`, but only when it is ours (a newer copy may have written its own)."""
    path = Path(data_dir) / "instance.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if pid is not None and (not isinstance(data, dict) or data.get("pid") != pid):
        return
    try:
        path.unlink()
    except OSError:
        pass
