"""Shared setup for the unit tests.

Every test file is started in its own process by `tests/run.py`, so pointing the app at a temporary
data folder here is enough: the app reads H3STUDIO_DATA once, when it is imported.

Temp files of the tests live in `_test/tmp` (a folder the repository already ignores), not in the
system temp folder: on a machine with a tightly restricted %TEMP% the tests would otherwise fail
for reasons that have nothing to do with the code under test.

Nothing here touches the user's own data: the folder is new and empty, `keep_copies` is off, and no
network call is ever made (the fake ComfyUI address is a port nothing listens on).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

TEMP_ROOT = ROOT / "_test" / "tmp" / f"unit-{os.getpid()}"
TEMP_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = os.environ["TEMP"] = str(TEMP_ROOT)


def new_dir(prefix):
    """A fresh empty folder inside `_test/tmp`.

    `tempfile.mkdtemp` is deliberately not used: it creates the folder with an explicit mode whose
    rights are not inherited from the parent, and on a machine with a confined token a program then
    cannot create files inside the folder it has just created. `mkdir` inherits, as a program's own
    folders normally do.
    """
    for n in range(10000):
        folder = TEMP_ROOT / f"{prefix}-{n}"
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            continue
    raise RuntimeError("не удалось создать временную папку")


DATA = new_dir("data")
os.environ["H3STUDIO_DATA"] = str(DATA)
os.environ["H3STUDIO_AGENT_FILE"] = str(DATA / "agent.json")

VIDEO_OUTPUT = {"9": {"images": [{"filename": "clip.mp4", "subfolder": "", "type": "output",
                                  "metadata": {"width": 1280, "height": 720}}]}}


_APPS = []


def clean_data():
    """Close the Apps of earlier checks and empty the data folder they used.

    A database left open by a previous App keeps the file locked on Windows, so the Apps are closed
    first; then every file an App writes is removed, the database and its WAL side files included.
    """
    import server

    while _APPS:
        app = _APPS.pop()
        try:
            app.db.close()
        except Exception:
            pass
    for name in ("library.json", "queue.json", "config.json", "h3studio.db",
                 "h3studio.db-wal", "h3studio.db-shm"):
        (server.DATA_DIR / name).unlink(missing_ok=True)


def new_app():
    """An App on the data folder as it is right now (no cleanup: the caller may have prepared files)."""
    import server

    app = server.App(comfy_override="http://127.0.0.1:9")
    app.config.data["keep_copies"] = False   # a test never copies into the user's own Videos folder
    app.output_dir = None
    _APPS.append(app)
    return app


def fresh_app():
    """A new App on an empty data folder: no library, no queue, no config left from another test."""
    clean_data()
    return new_app()


def job_record(pid="p1", state=None, graph=None):
    """The typed record a job needs to be written to the database (a job without one is not persisted)."""
    from domain.jobs import JobRecord, JobState

    return JobRecord(job_id="j-" + pid, prompt_id=pid, backend="comfy", backend_id="comfy:test",
                     backend_job_id=pid, state=state or JobState.QUEUED,
                     created_at=time.time(), updated_at=time.time(),
                     graph=graph if graph is not None else {"1": {"class_type": "Test", "inputs": {}}})


def job(pid="p1", **extra):
    """A job record of the shape `App.queue` creates."""
    record = {"prompt_id": pid, "state": "running", "stages": {}, "stage": "save",
              "meta": {"mode": "video", "prompt": "тест", "seed": 1},
              "graph": {"1": {"class_type": "Test", "inputs": {}}}, "t0": 0, "preview": False}
    record.update(extra)
    return record


def finished(app, pid="p1", state=None, message="", meta=None):
    """A job that ended: its row stays in the database as the outcome (the queue is `active()`).

    There is no second copy of it in memory any more: the details window, the agent and the snapshot
    all read this record.
    """
    from domain.jobs import JobState

    record = job_record(pid, state=state or JobState.SUCCEEDED)
    if meta is not None:
        record.legacy = {"meta": meta}
    app.jobs_repo.save(record)
    if message:
        app.jobs_repo.note_message(pid, message)
    return record


def saved(pid="old1", **extra):
    """A record of queue.json: what is left there after the program was closed."""
    record = {"graph": {"1": {"class_type": "Test", "inputs": {}}}, "stages": {},
              "meta": {"mode": "video", "prompt": "из прошлого запуска", "seed": 1},
              "pid": pid, "preview": False, "saved": time.time()}
    record.update(extra)
    return record


class Req:
    """The only part of an aiohttp request the handlers under test use."""

    def __init__(self, body=None, match_info=None, query=None):
        self._body = body if body is not None else {}
        self.match_info = match_info or {}
        self.query = query or {}

    async def json(self):
        return self._body


def capture_broadcast(app):
    """Replace the websocket broadcast with a list: returns the list of sent messages."""
    events = []

    async def fake(payload):
        events.append(payload)

    app.broadcast = fake
    return events


async def until(predicate, timeout=2.0):
    """Wait for a condition that another task reaches (never a fixed sleep)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        await __import__("asyncio").sleep(0.01)
    raise AssertionError("условие не наступило за отведённое время")
