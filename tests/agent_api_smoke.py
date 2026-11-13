#!/usr/bin/env python3
"""Smoke test of the agent API: discovery, key, capabilities, named inputs, starting image-to-video, job status.

Runs the real server in a temporary data folder against a *fake* ComfyUI (a few HTTP
endpoints, no rendering): nothing is ever generated and a working ComfyUI is not touched.

    python tests/agent_api_smoke.py
    python tests/agent_api_smoke.py --python "I:\\...\\python_embeded\\python.exe"

The server needs aiohttp; if the current interpreter has none, pass --python with one
that does (the ComfyUI portable python has it).
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"
PREFIX = "/api/agent/v1/"

from tempdir import make_temp_dir  # noqa: E402  (the test runs as a script from tests/)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeComfy(http.server.BaseHTTPRequestHandler):
    """Just enough of ComfyUI for the app to accept a job: models it can see, an upload, a prompt id."""

    models = {}
    prompts = []

    def log_message(self, *args):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.startswith("/models/"):
            return self._json(self.models.get(path[len("/models/"):], []))
        if path == "/queue":
            return self._json({"queue_running": [], "queue_pending": []})
        if path == "/system_stats":
            return self._json({"system": {}, "devices": []})
        return self._json({}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        path = self.path.split("?")[0]
        if path == "/upload/image":
            return self._json({"name": "agent-frame.png", "subfolder": "h3studio", "type": "input"})
        if path == "/prompt":
            self.prompts.append(json.loads(raw or b"{}"))
            return self._json({"prompt_id": "fake-job-1", "number": 1})
        if path in ("/free", "/queue", "/interrupt"):
            return self._json({})
        return self._json({}, 404)


def make_png(path, w=2, h=2):
    """A tiny real PNG: the API has to read its size from the header, no library involved."""
    raw = b"".join(b"\x00" + bytes([255, 0, 0]) * w for _ in range(h))

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class Client:
    def __init__(self, port, token=None):
        self.base = f"http://127.0.0.1:{port}"
        self.token = token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never through a system proxy

    def call(self, method, path, body=None, token=True, raw=False):
        data, headers = None, {}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if token is True and self.token:
            headers["X-Agent-Token"] = self.token
        elif isinstance(token, str):
            headers["X-Agent-Token"] = token
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=30) as r:
                payload = r.read()
                return r.status, (payload if raw else json.loads(payload or b"{}"))
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                return e.code, json.loads(payload or b"{}")
            except ValueError:
                return e.code, {"raw": payload.decode("utf-8", "replace")}


results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail and not ok else ""))


JOB_VIEW_PROBE = r'''
import sys, time, types
sys.path.insert(0, ".")
import agentapi
from domain.jobs import JobRecord, JobState


class Cfg:
    def __init__(self, data):
        self.data = data

    def get(self, key, default=None):
        return self.data.get(key, default)


app = types.SimpleNamespace()
app.config = Cfg({"allow_agents": True})
app.port = 1234
app.library = types.SimpleNamespace(items=[])
app.pending_resume = []
app.file_path = lambda item: None
app.job_outcome = lambda pid: None   # no finished job here: the record is the only owner
app.jobs = {"p-run": {"meta": {"mode": "video", "prompt": "тест", "seed": 1}, "state": "running",
                      "stage": "sample", "step": 3, "step_max": 8, "t0": time.time() - 30,
                      "scenes": {"n": 4}, "scene_now": 2}}
api = agentapi.AgentAPI(app, ".", 25.0)

run = api.job_view("p-run")
assert run["state"] == "running", run
assert run["progress"]["step"] == 3 and run["progress"]["total"] == 8, run
assert run["progress"]["scene"] == 2 and run["progress"]["scenes"] == 4, run
assert 0.3 < run["progress"]["percent"] < 0.5, run
assert run["elapsed"] >= 29, run

app.jobs["p-queued"] = {"meta": {"mode": "video"}, "state": "queued", "t0": time.time()}
queued = api.job_view("p-queued")
assert queued["state"] == "queued" and queued["progress"]["stage"] == "queued", queued
assert queued["queue_position"] == 0, queued

app.job_outcome = lambda pid: ({"state": "error", "at": "2026-11-11T10:00:00",
                                "message": "нет модели", "meta": {}, "created": 0.0,
                                "phase": "failed"} if pid == "p-bad" else None)
bad = api.job_view("p-bad")
assert bad["state"] == "failed" and bad["error"]["message"] == "нет модели", bad

# a job whose result is being written is running (not done, not 404), and says so additively
app.jobs["p-fin"] = {"meta": {"mode": "video"}, "state": "running", "finalizing": True, "t0": time.time(),
                     "record": JobRecord(job_id="jf", prompt_id="p-fin", state=JobState.FINALIZING)}
fin = api.job_view("p-fin")
assert fin["state"] == "running" and fin["phase"] == "finalizing" and fin["finalizing"] is True, fin

# a job of an earlier run waits for «восстановить»: queued and recovering, not forgotten
app.pending_resume = [JobRecord(job_id="jp", prompt_id="p-old", state=JobState.QUEUED,
                                legacy={"meta": {"mode": "video", "prompt": "из прошлого запуска"}})]
old = api.job_view("p-old")
assert old and old["state"] == "queued" and old["recovering"] is True, old
assert old["progress"]["stage"] == "recovering" and old["prompt"] == "из прошлого запуска", old

assert api.job_view("unknown") is None
print("ok")
'''


RECONCILE_PROBE = r'''
import asyncio
import sys
import time

sys.path.insert(0, ".")
import server


async def main():
    app = server.App(port=0)
    queue = {"queue_running": [[0, "p-alive", {}, {}, []]], "queue_pending": []}
    history = {"p-done": {"status": {"status_str": "success"}, "outputs": {}},
               "p-error": {"status": {"status_str": "error"}, "outputs": {}}}

    async def fake_json(method, path, **kw):
        if path == "/queue":
            return 200, queue
        if path.startswith("/history/"):
            pid = path.rsplit("/", 1)[-1]
            return 200, ({pid: history[pid]} if pid in history else {})
        raise AssertionError(path)

    finished, errors = [], []

    async def fake_finish(job):
        finished.append(job["prompt_id"])
        app.jobs.pop(job["prompt_id"], None)  # the real finish_job pops first thing

    async def fake_broadcast(payload):
        if payload.get("type") == "error":
            errors.append(payload["prompt_id"])

    app.comfy_json = fake_json
    app.finish_job = fake_finish
    app.broadcast = fake_broadcast
    app.jobs = {
        "p-done": {"prompt_id": "p-done", "state": "running", "t0": 0},
        "p-error": {"prompt_id": "p-error", "state": "running", "t0": 0},
        "p-gone": {"prompt_id": "p-gone", "state": "running", "t0": 0},
        "p-alive": {"prompt_id": "p-alive", "state": "queued", "t0": 0},
        "p-fresh": {"prompt_id": "p-fresh", "state": "queued", "t0": time.time()},
    }
    await app.reconcile_jobs()
    assert finished == ["p-done"], finished
    assert sorted(errors) == ["p-error", "p-gone"], errors
    assert set(app.jobs) == {"p-alive", "p-fresh"}, app.jobs
    print("ok")


asyncio.run(main())
'''


def seed_library(data_dir, picture):
    data_dir.mkdir(parents=True, exist_ok=True)
    item = {"mode": "krea", "sub": "Изображение по тексту", "prompt": "тестовая картинка", "aspect": "1:1",
            "q": "1K", "len": 0.0, "res": "2×2", "seed": 7, "id": "itest0000001", "prompt_id": "p-test-done",
            "date": "2026-11-11T10:00:00", "fav": False, "kind": "image", "ext": "png",
            "origin": {"kind": "agent", "idempotency_key": "seed-key"},
            "file": {"type": "local", "path": str(picture), "filename": picture.name}}
    (data_dir / "library.json").write_text(json.dumps([item], ensure_ascii=False), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", default=sys.executable, help="interpreter with aiohttp (server side)")
    ap.add_argument("--tmp", default=None, help="folder for the temporary data (default: the system temp folder)")
    ap.add_argument("--keep", action="store_true", help="keep the temporary folder")
    args = ap.parse_args()

    try:
        subprocess.run([args.python, "-c", "import aiohttp"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, OSError):
        print(f"FAIL  {args.python} has no aiohttp: pass --python with an interpreter that has it")
        return 2

    root = make_temp_dir("h3s-agent-", args.tmp)
    data = root / "data"
    roots = root / "roots"
    roots.mkdir(parents=True)
    agent_json = root / "agent.json"
    picture = roots / "frame.png"
    make_png(picture, 3, 2)
    (roots / "not-a-picture.exe").write_bytes(b"MZ")
    seed_library(data, picture)

    # a fake ComfyUI that already "sees" every model file the app may ask for
    probe = subprocess.run([args.python, "-B", "-c",
                            "import sys, json; sys.path.insert(0, '.'); import catalog; "
                            "cats = {v['category'] for vs in catalog.VARIANTS.values() for v in vs.values()}; "
                            "print(json.dumps({c: sorted({v['file'] for vs in catalog.VARIANTS.values() "
                            "for v in vs.values() if v['category'] == c}) for c in sorted(cats)}))"],
                           cwd=str(APP_DIR), capture_output=True, text=True, encoding="utf-8")
    FakeComfy.models = json.loads(probe.stdout) if probe.returncode == 0 else {}
    comfy_port = free_port()
    comfy = http.server.ThreadingHTTPServer(("127.0.0.1", comfy_port), FakeComfy)
    comfy.daemon_threads = True
    threading.Thread(target=comfy.serve_forever, daemon=True).start()

    (data / "config.json").write_text(json.dumps({
        "engine": "external", "setup_done": True, "comfy_url": f"http://127.0.0.1:{comfy_port}",
        "modes": ["video", "krea", "qwen", "upscale"]}, ensure_ascii=False), encoding="utf-8")

    port = free_port()
    env = {**os.environ, "H3STUDIO_DATA": str(data), "H3STUDIO_AGENT_FILE": str(agent_json),
           "H3STUDIO_AGENT_ROOTS": str(roots), "PYTHONIOENCODING": "utf-8"}
    # a phone-book-free start: the ComfyUI portable python has a ._pth that ignores cwd and PYTHONPATH
    boot = ("import runpy, sys; sys.path.insert(0, '.'); sys.argv = ['server.py', '--port', " + repr(str(port))
            + "]; runpy.run_path('server.py', run_name='__main__')")
    proc = subprocess.Popen([args.python, "-B", "-c", boot],
                            cwd=str(APP_DIR), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    client = Client(port)
    try:
        # 1. discovery: the app publishes its port and key
        for _ in range(120):
            if agent_json.exists():
                try:
                    published = json.loads(agent_json.read_text(encoding="utf-8"))
                    if client.call("GET", PREFIX + "ping", token=False)[0] == 200:
                        break
                except (OSError, ValueError):
                    pass
            if proc.poll() is not None:
                print(proc.communicate()[0])
                print("FAIL  the server did not start")
                return 1
            time.sleep(0.5)
        else:
            print("FAIL  the server did not publish agent.json / did not answer")
            return 1

        check("ping without a key", True)
        status, ping = client.call("GET", PREFIX + "ping", token=False)
        check("ping names the app", status == 200 and ping.get("app") == "minimax-h3-workspace", str(ping))
        check("agent.json has port and token",
              published.get("port") == port and len(published.get("token", "")) > 20
              and published.get("instance") == ping.get("instance"), str(published))

        # 2. the key is required, the window's own API is not
        status, body = client.call("GET", PREFIX + "capabilities", token=False)
        check("no key -> 401 BAD_TOKEN", status == 401 and body["error"]["code"] == "BAD_TOKEN", str(body))
        status, body = client.call("GET", PREFIX + "capabilities", token="wrong")
        check("wrong key -> 401", status == 401, str(body))
        status, body = client.call("GET", "/api/version", token=False)
        check("the window's own API still works without a key", status == 200 and "version" in body, str(body))

        client.token = published["token"]
        status, caps = client.call("GET", PREFIX + "capabilities")
        check("capabilities with the key", status == 200 and caps.get("api") == 1, str(caps)[:300])
        check("capabilities list modes and limits",
              "video" in caps.get("modes", []) and "max_seconds" in caps.get("limits", {}), str(caps)[:300])
        check("capabilities list the asset roots",
              str(roots) in caps.get("agents", {}).get("asset_roots", []), str(caps.get("agents")))

        # 3. the switch in the window
        status, body = client.call("POST", "/api/agent/allow", {"enabled": False}, token=False)
        check("the window can switch agents off", status == 200 and body.get("enabled") is False, str(body))
        status, body = client.call("GET", PREFIX + "capabilities")
        check("switched off -> 403 AGENTS_DISABLED", status == 403 and body["error"]["code"] == "AGENTS_DISABLED", str(body))
        status, body = client.call("POST", "/api/agent/allow", {"enabled": True}, token=False)
        check("switched on again", status == 200 and body.get("enabled") is True, str(body))

        # 4. named inputs
        status, one = client.call("POST", PREFIX + "assets", {"path": str(picture)})
        check("import a picture by path", status == 201 and one.get("kind") == "image", str(one))
        check("the picture's size is read from the header",
              one.get("width") == 3 and one.get("height") == 2, str(one))
        check("the asset gets a content name", len(one.get("asset", "")) == 16, str(one))
        status, again = client.call("POST", PREFIX + "assets", {"path": str(picture)})
        check("the same file is one asset", status == 201 and again.get("asset") == one["asset"]
              and again.get("deduplicated") is True, str(again))
        status, raw = client.call("GET", PREFIX + f"assets/{one['asset']}/content", raw=True)
        check("the asset keeps the bytes", status == 200 and raw == picture.read_bytes(), str(len(raw)))
        status, listing = client.call("GET", PREFIX + "assets")
        check("the asset is listed", status == 200 and any(a["asset"] == one["asset"] for a in listing["items"]), str(listing)[:200])

        outside = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "win.ini"
        if outside.exists():
            status, body = client.call("POST", PREFIX + "assets", {"path": str(outside)})
            check("a file outside the allowed folders -> 403 ASSET_FORBIDDEN",
                  status == 403 and body["error"]["code"] == "ASSET_FORBIDDEN", str(body))
        status, body = client.call("POST", PREFIX + "assets", {"path": str(roots / "ghost.png")})
        check("a missing file -> 404 ASSET_NOT_FOUND", status == 404 and body["error"]["code"] == "ASSET_NOT_FOUND", str(body))
        status, body = client.call("POST", PREFIX + "assets", {"path": str(roots / "not-a-picture.exe")})
        check("an unsuitable file -> 415 ASSET_BAD_TYPE", status == 415 and body["error"]["code"] == "ASSET_BAD_TYPE", str(body))
        status, body = client.call("POST", PREFIX + "assets", {"path": "frame.png"})
        check("a relative path -> 400 BAD_REQUEST", status == 400 and body["error"]["code"] == "BAD_REQUEST", str(body))

        # 5. job status
        status, body = client.call("GET", PREFIX + "jobs/unknown-job")
        check("an unknown job -> 404 JOB_NOT_FOUND", status == 404 and body["error"]["code"] == "JOB_NOT_FOUND", str(body))
        status, job = client.call("GET", PREFIX + "jobs/p-test-done")
        check("a finished job is shown as done", status == 200 and job.get("state") == "done", str(job)[:300])
        check("a finished job keeps its origin", (job.get("origin") or {}).get("kind") == "agent", str(job)[:300])
        check("its result points at the file", status == 200 and job["results"]
              and job["results"][0]["path"] == str(picture), str(job)[:300])
        check("its result has a link for the window", job["results"] and job["results"][0]["url"].startswith("http"), str(job)[:300])
        status, body = client.call("GET", PREFIX + "jobs/p-test-done/wait?timeout=5")
        check("wait on a finished job returns at once", status == 200 and body.get("state") == "done", str(body)[:200])
        status, listing = client.call("GET", PREFIX + "jobs")
        check("the job list answers", status == 200 and "active" in listing and "finished" in listing, str(listing)[:200])
        status, listing = client.call("GET", PREFIX + "library?limit=5")
        check("the library shows the finished work",
              status == 200 and any(i["library_id"] == "itest0000001" for i in listing["items"]), str(listing)[:300])
        status, body = client.call("POST", PREFIX + "assets", {"path": str(picture)}, token=False)
        check("import without a key -> 401", status == 401, str(body))

        # 6. starting image-to-video through the same queue the window uses
        status, body = client.call("POST", PREFIX + "jobs", {"mode": "krea", "prompt": "x"})
        check("only video for now -> 400 MODE_UNSUPPORTED",
              status == 400 and body["error"]["code"] == "MODE_UNSUPPORTED", str(body))
        status, body = client.call("POST", PREFIX + "jobs", {"mode": "video"})
        check("no prompt -> 400 INVALID_PARAM", status == 400 and body["error"]["code"] == "INVALID_PARAM", str(body))
        status, body = client.call("POST", PREFIX + "jobs", {"mode": "video", "prompt": "x", "aspect": "5:4"})
        check("bad aspect -> 400 INVALID_PARAM", status == 400 and body["error"]["code"] == "INVALID_PARAM", str(body))
        status, body = client.call("POST", PREFIX + "jobs", {"mode": "video", "prompt": "x",
                                                            "inputs": {"last_frame": {"asset": one["asset"]}}})
        check("last_frame alone -> 400 INVALID_PARAM", status == 400 and body["error"]["code"] == "INVALID_PARAM", str(body))
        status, body = client.call("POST", PREFIX + "jobs", {"mode": "video", "prompt": "x",
                                                            "inputs": {"first_frame": {"asset": "0000000000000000"}}})
        check("unknown asset -> 404 ASSET_NOT_FOUND", status == 404 and body["error"]["code"] == "ASSET_NOT_FOUND", str(body))

        job_body = {"mode": "video", "prompt": "Медленный наезд на лицо, дождь за окном",
                    "params": {"seconds": 4, "aspect": "16:9", "quality": "480p", "seed": 7},
                    "inputs": {"first_frame": {"asset": one["asset"]}}, "idempotency_key": "smoke-1"}
        status, sub = client.call("POST", PREFIX + "jobs", job_body)
        check("agent starts image-to-video", status == 202 and sub.get("job") == "fake-job-1", str(sub))
        check("the picture went into ComfyUI",
              (sub.get("inputs") or {}).get("first_frame", {}).get("comfy") == "h3studio/agent-frame.png", str(sub))
        check("the job remembers that an agent started it",
              (sub.get("origin") or {}).get("kind") == "agent", str(sub))
        status, again = client.call("POST", PREFIX + "jobs", job_body)
        check("a retry with the same key does not start a second render",
              status == 200 and again.get("job") == "fake-job-1" and again.get("deduplicated") is True, str(again))
        check("ComfyUI got exactly one prompt", len(FakeComfy.prompts) == 1, str(len(FakeComfy.prompts)))
        graph = FakeComfy.prompts[0].get("prompt") or {}
        nodes = [n for n in graph.values() if isinstance(n, dict)]
        check("the graph loads the agent's picture",
              any(n.get("class_type") == "LoadImage" and n.get("inputs", {}).get("image") == "h3studio/agent-frame.png"
                  for n in nodes), str([n.get("class_type") for n in nodes])[:200])
        h3 = next((n for n in nodes if n.get("class_type") == "MiniMaxH3ImageToVideo"), {})
        size = h3.get("inputs", {})
        check("the graph is image-to-video at the asked size and seed",
              size.get("width", 0) * size.get("height", 0) > 400_000 and abs(size.get("width", 1) / max(1, size.get("height", 1)) - 16 / 9) < 0.05
              and any(n.get("inputs", {}).get("noise_seed") == 7 for n in nodes), str(size)[:200])
        status, job = client.call("GET", PREFIX + "jobs/fake-job-1")
        check("the new job is queued", status == 200 and job["state"] == "queued", str(job)[:300])
        check("the job shows the agent as its origin", (job.get("origin") or {}).get("kind") == "agent", str(job)[:300])
        status, body = client.call("POST", PREFIX + "jobs/fake-job-1/cancel")
        check("cancel a waiting job", status == 200 and body.get("state") == "canceled", str(body))
        status, job = client.call("GET", PREFIX + "jobs/fake-job-1")
        check("it is canceled now", status == 200 and job["state"] == "canceled", str(job)[:300])
        status, body = client.call("POST", PREFIX + "jobs/fake-job-1/cancel")
        check("cancel twice -> 409 ALREADY_FINISHED",
              status == 409 and body["error"]["code"] == "ALREADY_FINISHED", str(body))

        # 7. a running job, without ComfyUI: the same process reads its own queue the way an agent would
        probe = subprocess.run([args.python, "-B", "-c", JOB_VIEW_PROBE], cwd=str(APP_DIR),
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("a running job: stage, step, scene and how far it is",
              probe.returncode == 0, (probe.stdout + probe.stderr)[-600:])

        # 8. after a dropped connection: finished jobs are taken, lost ones are reported, fresh ones untouched
        probe = subprocess.run([args.python, "-B", "-c", RECONCILE_PROBE], cwd=str(APP_DIR),
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env={**os.environ, "H3STUDIO_DATA": str(data), "PYTHONIOENCODING": "utf-8"})
        check("a reconnect takes a job that finished meanwhile and reports one that vanished",
              probe.returncode == 0, (probe.stdout + probe.stderr)[-600:])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        comfy.shutdown()
        if not args.keep:
            import shutil
            shutil.rmtree(root, ignore_errors=True)
        else:
            print(f"kept: {root}")

    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
