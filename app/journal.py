"""One line per event, for the author: what happened to a job, when, how long it took, and what kind
of error it was.

The journal is not a log of everything: it exists so that a report from a user can answer "what
happened to that clip" without asking them to reproduce it. Every line is JSON with a fixed set of
fields — `at`, `event`, and primitives the caller passes explicitly (ids, states, durations, error
type). Nothing is taken from the configuration, the request body or the prompt by itself, and a
value that is not a primitive is refused: that is what keeps tokens and secrets out of a file the
user may send to someone.

The file is bounded: after `MAX_BYTES` it is renamed once to `.1` and a new one starts, so the
diagnostics of the previous run survive but cannot grow without limit. When the folder cannot be
written, the journal keeps working in memory only — diagnostics never break the work.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024
KEEP = 200          # entries kept in memory for the report
MAX_TEXT = 200      # a message is a short line, not a body: longer values are cut


def error_type(exc: BaseException) -> str:
    """The kind of an error, without its text: 'TimeoutError', 'ClientConnectorError', …"""
    return type(exc).__name__


def _primitive(key: str, value):
    """Only what a log line may carry: a number, a flag, an id, a short string."""
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        return value[:MAX_TEXT]
    if hasattr(value, "value") and isinstance(value.value, (str, int, float)):   # an Enum
        return value.value
    return None      # a dict/list/object is not written: it may hold a token or a whole request


class Journal:
    """The last events in memory and on disk; `event()` never raises and never waits for anyone."""

    def __init__(self, path=None, *, clock=time.time):
        self.path = Path(path) if path else None
        self.entries = deque(maxlen=KEEP)
        self._clock = clock
        self._lock = threading.Lock()
        self.write_error = None

    def event(self, name: str, **fields):
        entry = {"at": datetime.fromtimestamp(self._clock()).isoformat(timespec="seconds"),
                 "event": str(name)[:60]}
        for key, value in fields.items():
            clean = _primitive(key, value)
            if clean is not None:
                entry[key] = clean
        with self._lock:
            self.entries.append(entry)
            self._write(entry)
        return entry

    def tail(self, n: int = 60):
        """The last `n` entries, oldest first: a section of the developer report."""
        with self._lock:
            return list(self.entries)[-int(n):]

    def _write(self, entry):
        if self.path is None:
            return
        try:
            self._rotate()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self.write_error = None
        except OSError as e:
            # a read-only folder must not stop a job: the journal keeps the entries in memory
            self.write_error = f"{error_type(e)}: {e}"

    def _rotate(self):
        try:
            if self.path.exists() and self.path.stat().st_size >= MAX_BYTES:
                os.replace(self.path, self.path.with_suffix(self.path.suffix + ".1"))
        except OSError:
            pass
