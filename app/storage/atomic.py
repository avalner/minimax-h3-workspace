"""Replace a small file in one step, so a crash never leaves half of it.

The write goes to a unique temporary file in the *same* folder as the target (same volume, so the
final rename is atomic), is flushed to the disk, and only then replaces the target with os.replace.
If anything fails, the target keeps its previous content and the temporary file is removed.

What this does NOT promise: a hardware failure can still lose the *last* write, and a file copied
elsewhere is not protected. It only guarantees that the target is either the old or the new version,
never a truncated one.

Windows notes: os.replace fails with PermissionError while another process holds the target open
(a virus scanner, the search indexer, an editor). Such a lock is usually momentary, so the replace
is retried a few times before the error is reported.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from pathlib import Path

__all__ = ["AtomicWriteError", "atomic_write_bytes", "atomic_write_text", "atomic_write_json"]


class AtomicWriteError(OSError):
    """A file could not be replaced; its previous version, if it existed, is still in place."""

    def __init__(self, path, message, cause=None):
        super().__init__(f"{path}: {message}")
        self.path = Path(path)
        self.cause = cause


def _temp_name(path):
    """A name nobody else can pick: same folder, hidden, with the pid and random bytes in it."""
    return f".{path.name}.{os.getpid():x}.{secrets.token_hex(4)}.tmp"


def _replace(temp, path, retries, retry_delay):
    for attempt in range(retries):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(retry_delay * (attempt + 1))


def _sync_dir(folder):
    """Make the rename itself durable. Directories cannot be opened for fsync on Windows."""
    if sys.platform == "win32":
        return
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_bytes(path, data, *, fsync=True, retries=5, retry_delay=0.02):
    """Write `data` to `path` so that the file is never observed half-written. Returns the path."""
    path = Path(path)
    temp = path.with_name(_temp_name(path))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(temp, "wb") as f:
            f.write(data)
            f.flush()
            if fsync:
                os.fsync(f.fileno())
        _replace(temp, path, retries, retry_delay)
    except OSError as e:
        try:
            temp.unlink()
        except OSError:
            pass
        if isinstance(e, AtomicWriteError):
            raise
        raise AtomicWriteError(path, f"запись не удалась ({type(e).__name__}: {e})", cause=e) from e
    _sync_dir(path.parent)
    return path


def atomic_write_text(path, text, *, encoding="utf-8", fsync=True, retries=5, retry_delay=0.02):
    """The same as atomic_write_bytes for text (UTF-8 by default, as everywhere in the app)."""
    return atomic_write_bytes(path, text.encode(encoding), fsync=fsync, retries=retries, retry_delay=retry_delay)


def atomic_write_json(path, data, *, indent=None, ensure_ascii=False, sort_keys=False, fsync=True):
    """JSON in one atomic step. `indent` is passed to json.dumps, so the file stays readable."""
    text = json.dumps(data, ensure_ascii=ensure_ascii, indent=indent, sort_keys=sort_keys)
    return atomic_write_text(path, text, fsync=fsync)
