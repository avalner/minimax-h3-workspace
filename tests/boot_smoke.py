#!/usr/bin/env python3
"""The app still starts: the window, the API, an empty library and the queue endpoints.

    python tests/boot_smoke.py

Runs the real `app/server.py` on a free port with H3STUDIO_DATA pointing at an empty temporary
folder, so nothing of the user's is read or written. ComfyUI does not have to be running: the app is
expected to start anyway and say "offline". This is the check that a refactoring did not break the
application as a whole — the unit tests cannot see an import that a handler only needs at startup.

Exit code: 0 all checks passed, 1 something failed.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"

from tempdir import make_temp_dir  # noqa: E402  (the test runs as a script from tests/)

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail and not ok else ""))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url, method="GET", timeout=10):
    request = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def head(url, timeout=10):
    """(status, content-type, bytes): the content type matters as much as the body for js/css."""
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.headers.get("Content-Type", ""), response.read()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    data = Path(os.environ.get("H3STUDIO_DATA") or make_temp_dir("h3s-boot-"))
    data.mkdir(parents=True, exist_ok=True)
    agent_file = os.environ.get("H3STUDIO_AGENT_FILE") or str(data / "agent.json")
    port = free_port()
    env = {**os.environ, "H3STUDIO_DATA": str(data), "H3STUDIO_AGENT_FILE": agent_file,
           "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}

    log = data / "server.log"
    with open(log, "wb") as sink:
        proc = subprocess.Popen([sys.executable, "-B", "server.py", "--port", str(port)],
                                cwd=str(APP), env=env, stdout=sink, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        up = False
        deadline = time.time() + 60
        while time.time() < deadline and proc.poll() is None:
            try:
                status, _ = get(base + "/api/status", timeout=3)
                up = status == 200
                break
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(0.3)
        check("the app starts and answers /api/status", up, log.read_text(encoding="utf-8", errors="replace")[-800:])
        if not up:
            return 1

        status, body = get(base + "/api/status")
        payload = json.loads(body)
        check("/api/status is a JSON object with an «online» flag", isinstance(payload, dict) and "online" in payload,
              str(payload)[:300])
        check("a queue that could not be written is reported, not hidden", "queue_save_error" in payload,
              str(sorted(payload))[:300])

        folder = payload.get("data_dir") or {}
        check("/api/status names the data folder and does not warn about a local one",
              folder.get("path") == str(data) and not folder.get("warning"), str(folder)[:300])

        note = data / "instance.json"
        note_data = json.loads(note.read_text(encoding="utf-8")) if note.exists() else {}
        # the pid here is the interpreter's, which on this machine is not the launcher's pid (the
        # Windows Store python starts a child): the port and the folder are what the note is for
        check("the running copy says where it is (one folder, one copy)",
              note_data.get("port") == port and note_data.get("data") == str(data), str(note_data)[:200])

        storage = payload.get("storage") or {}
        migration = storage.get("migration") or {}
        check("the database is created and the empty folder is imported once",
              storage.get("schema_version") == 3 and migration.get("applied") == [
                  "0001-schema", "1000-import-library", "1001-import-queue", "0002-drafts",
                  "0003-projects"] and not migration.get("problems"),
              str(storage)[:300])
        check("a fresh folder has nothing waiting in the queue", storage.get("waiting") == 0, str(storage)[:200])
        check("the database file is where the data folder is", (data / "h3studio.db").exists())

        status, body = get(base + "/api/queue/saved")
        check("an empty queue is served as count 0", json.loads(body).get("count") == 0, body[:200])

        status, body = get(base + "/api/queue/resume", method="POST")
        resumed = json.loads(body)
        check("resuming nothing is not an error", status == 200 and resumed.get("jobs") == [], str(resumed)[:200])

        status, body = get(base + "/api/library")
        check("an empty library is served as []", json.loads(body) == [], body[:200])

        status, body = get(base + "/api/projects")
        projects = json.loads(body)
        check("a fresh folder has one project, and it is the active one",
              [project["name"] for project in projects.get("projects", [])] == ["Основное"]
              and projects.get("active") == projects["projects"][0]["id"], body[:300])

        status, body = get(base + "/")
        check("the window itself is served", status == 200 and b"<" in body and len(body) > 10000, str(len(body)))
        check("the page loads its stylesheets and js modules, and has no inline style left",
              all(name in body for name in (b"/ui/css/base.css", b"/ui/css/widgets.css",
                                            b"/ui/css/sections.css", b"/ui/js/api.js",
                                            b"/ui/js/events.js", b"/ui/js/state.js",
                                            b"/ui/js/components.js", b"/ui/js/modes.js",
                                            b"/ui/js/modes/video.js", b"/ui/js/modes/lipsync.js",
                                            b"/ui/js/modes/pictures.js", b"/ui/js/modes/montage.js",
                                            b"/ui/js/modes/custom.js", b"/ui/js/modes/loras.js",
                                            b"/ui/js/modes/agent.js", b"/ui/js/modes/refs.js"))
              and b"<style" not in body[:6000], body[:200].decode("utf-8", "replace"))

        # the window's own files: the content type is part of the contract — a module script served as
        # text/plain is refused by the browser and the window opens blank (Windows registry, .js)
        for sheet, marker in ((b"/ui/css/base.css", b":root"), (b"/ui/css/widgets.css", b".drop{"),
                              (b"/ui/css/sections.css", b"Library")):
            status, ctype, css = head(base + sheet.decode())
            check(f"the stylesheet {sheet.decode()} is served as text/css",
                  status == 200 and ctype.startswith("text/css") and marker in css,
                  f"{status} {ctype} {len(css)}")
        status, ctype, js = head(base + "/ui/js/api.js")
        check("the HTTP module is served as JavaScript", status == 200 and "javascript" in ctype and b"export" in js,
              f"{status} {ctype} {len(js)}")
        status, ctype, _ = head(base + "/ui/js/events.js")
        check("the event module is served as JavaScript too", status == 200 and "javascript" in ctype, f"{status} {ctype}")
        for name in ("state.js", "components.js", "lifecycle.js", "modes.js",
                     "modes/video.js", "modes/lipsync.js", "modes/pictures.js", "modes/montage.js",
                     "modes/custom.js", "modes/loras.js", "modes/agent.js", "modes/refs.js"):
            status, ctype, module = head(base + "/ui/js/" + name)
            check(f"{name} is served as JavaScript", status == 200 and "javascript" in ctype and len(module) > 500,
                  f"{status} {ctype} {len(module)}")
        status, ctype, _ = head(base + "/ui/previews/action.mp4")
        check("a mode preview keeps its video type", status == 200 and ctype.startswith("video/"), f"{status} {ctype}")
        try:
            head(base + "/ui/%2e%2e%2fserver.py")
            check("a file outside the window folder is refused", False, "отдал файл вне ui")
        except urllib.error.HTTPError as e:
            check("a file outside the window folder is refused", e.code in (400, 403, 404), str(e.code))

        try:
            get(base + "/api/agent/v1/capabilities")
            check("the agent API refuses a request without the key", False, "ответил без ключа")
        except urllib.error.HTTPError as e:
            check("the agent API refuses a request without the key", e.code in (401, 403), str(e.code))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        if proc.poll() is None:
            check("the app stops when it is asked to", False)

    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
