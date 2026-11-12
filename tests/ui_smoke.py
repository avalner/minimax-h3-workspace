#!/usr/bin/env python3
"""The window in a real browser: sections open, modules load, nothing throws.

    python tests/ui_smoke.py

Starts the app on a free port with an empty temporary data folder and `--comfy` pointing at a
closed port (so the setup wizard is skipped and the navigation is unlocked), then drives headless
Chrome or Edge through the DevTools protocol:

- loads `/` and waits until the page and the three UI modules (api, events, state) are there;
- checks that exactly one section is shown and the queue starts empty;
- opens every navigation section, twice, and checks that the promised section is the active one;
- collects uncaught exceptions and console errors from the whole run;
- keeps a screenshot of a few sections in `_test/ui-shots` (for a human to look at).

No GPU, no ComfyUI and no real generation: sections whose engine is absent show their own message.
The check needs Chrome or Edge and aiohttp. Without a browser it exits 3 (skipped, cannot run here),
so a machine that only runs the Python checks is not failed.

Exit code: 0 all checks passed, 1 something failed, 3 cannot run here.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
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

try:
    import aiohttp
except ImportError:  # the runner skips the group when aiohttp is absent; this is for a direct run
    print("нет aiohttp: проверка окна без него не запускается")
    sys.exit(3)

results = []
SKIP = 3


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail and not ok else ""))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def browser_path() -> str | None:
    named = os.environ.get("H3STUDIO_BROWSER")
    candidates = [named] if named else []
    candidates += [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for path in candidates:
        if path and Path(path).exists():
            return path
    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    return None


class CDP:
    """A very small DevTools client: one socket, calls by id, events kept in a list."""

    def __init__(self, ws):
        self.ws = ws
        self.next_id = 0
        self.events = []

    async def call(self, method: str, params: dict | None = None, timeout: float = 20.0):
        self.next_id += 1
        cid = self.next_id
        await self.ws.send_json({"id": cid, "method": method, "params": params or {}})
        while True:
            msg = await self.ws.receive(timeout=timeout)
            if msg.type != aiohttp.WSMsgType.TEXT:
                raise RuntimeError(f"devtools closed: {msg.type}")
            data = json.loads(msg.data)
            if data.get("id") == cid:
                if "error" in data:
                    raise RuntimeError(f"{method}: {data['error']}")
                return data.get("result", {})
            self.events.append(data)

    async def evaluate(self, expression: str, await_promise: bool = False, timeout: float = 20.0):
        result = await self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": await_promise,
            "userGesture": True,
        }, timeout=timeout)
        if result.get("exceptionDetails"):
            detail = result["exceptionDetails"]
            text = (detail.get("exception") or {}).get("description") or detail.get("text")
            raise RuntimeError(f"JS: {text}")
        return (result.get("result") or {}).get("value")

    async def wait_for(self, expression: str, timeout: float = 30.0, step: float = 0.15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if await self.evaluate(expression):
                    return True
            except RuntimeError:
                pass
            await asyncio.sleep(step)
        return False

    def exceptions(self):
        out = []
        for event in self.events:
            if event.get("method") == "Runtime.exceptionThrown":
                details = event["params"].get("exceptionDetails") or {}
                text = (details.get("exception") or {}).get("description") or details.get("text") or "?"
                # one short line plus the first stack frame: enough to find the call site
                lines = [line.strip() for line in str(text).splitlines() if line.strip()]
                out.append(" / ".join(lines[:6])[:500])
            elif event.get("method") == "Runtime.consoleAPICalled" and event["params"].get("type") == "error":
                parts = [str(a.get("value", a.get("description", ""))) for a in event["params"].get("args", [])]
                out.append(("console.error: " + " ".join(parts))[:300])
        return out

    async def screenshot(self, path: Path):
        result = await self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(result.get("data", "")))


def http_json(url: str, method: str = "GET", timeout: float = 3.0):
    request = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


async def wait_http(url: str, proc: subprocess.Popen, timeout: float, attempt: float = 8.0):
    """The first `/api/status` of a fresh folder may take a few seconds (import + model scan):
    retry with a generous per-request timeout instead of counting the warm-up as a failure."""
    deadline = time.time() + timeout
    while time.time() < deadline and proc.poll() is None:
        try:
            return await asyncio.to_thread(http_json, url, "GET", attempt)
        except (urllib.error.URLError, ConnectionError, OSError, ValueError):
            await asyncio.sleep(0.4)
    return None


async def run_page(base: str, browser: str, shots: Path):
    debug_port = free_port()
    profile = make_temp_dir("h3s-ui-chrome-")
    log = Path(profile) / "chrome.log"
    with open(log, "wb") as sink:
        chrome = subprocess.Popen([
            browser, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
            "--no-first-run", "--no-default-browser-check", "--mute-audio",
            "--disable-background-networking", "--disable-sync",
            f"--remote-debugging-port={debug_port}", f"--user-data-dir={profile}",
            "--window-size=1440,900", "about:blank",
        ], stdout=sink, stderr=subprocess.STDOUT)
    try:
        version = await wait_http(f"http://127.0.0.1:{debug_port}/json/version", chrome, 20)
        if not version:
            print("браузер не запустился: " + log.read_text(encoding="utf-8", errors="replace")[-600:])
            return SKIP
        page = await asyncio.to_thread(http_json, f"http://127.0.0.1:{debug_port}/json/new?url=about:blank", "PUT")
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(page["webSocketDebuggerUrl"], max_msg_size=0) as ws:
                cdp = CDP(ws)
                await cdp.call("Page.enable")
                await cdp.call("Runtime.enable")
                await cdp.call("Emulation.setDeviceMetricsOverride",
                               {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
                # the page is opened after the domains are enabled, so a script that throws while it
                # is parsed is caught too (a tab opened first may report its first error before us)
                await cdp.call("Page.navigate", {"url": base + "/"})
                loaded = await cdp.wait_for("document.readyState === 'complete'", timeout=40)
                check("the window page finishes loading", loaded)

                modules = await cdp.wait_for(
                    "!!(window.h3State && window.h3Api && window.h3Events)", timeout=20)
                check("api.js, events.js and state.js are loaded and published",
                      modules, await cdp.evaluate("Object.keys(window).filter(k => k.startsWith('h3')).join(',')"))
                if not modules:
                    print("       window:", await cdp.evaluate(
                        "document.body ? document.body.innerText.slice(0, 200) : '(no body)'"))
                    print("       errors:", " | ".join(cdp.exceptions()[:5]))

                ready = await cdp.wait_for("document.documentElement.classList.contains('booted')", timeout=10)
                check("the window shows itself (no stuck boot screen)", ready)

                one_view = await cdp.evaluate(
                    "document.querySelectorAll('.view.active').length === 1")
                check("exactly one section is active", one_view)

                empty_queue = await cdp.evaluate(
                    "(() => { const q = document.querySelector('#qList'); return !!q && /Очередь пуста/.test(q.textContent); })()")
                check("the queue starts empty with its own message", empty_queue)

                views = await cdp.evaluate(
                    "Array.from(document.querySelectorAll('.nav[data-view]'))"
                    ".filter(n => !n.hidden && !n.disabled).map(n => n.dataset.view)")
                check("the navigation offers its sections", len(views) >= 10, str(views))

                # Every section opens by a real click, twice: a section that only works the first
                # time (a duplicated handler, a stale element) is exactly what this must catch.
                wrong, missing = [], []
                for view in views:
                    for attempt in (1, 2):
                        ok = await cdp.evaluate(
                            f"(() => {{ const b = document.querySelector('.nav[data-view=\"{view}\"]');"
                            f" if (!b) return false; b.click();"
                            f" return document.querySelector('.view.active')?.id === 'view-{view}'; }})()")
                        if not ok:
                            (missing if attempt == 1 else wrong).append(view)
                        await asyncio.sleep(0.08)
                check("every section opens on the first click", not missing, ",".join(missing))
                check("every section opens again on a second visit", not wrong, ",".join(wrong))

                # The video mode is a module: the panel is built on the first visit, its controls
                # write into the module's state, and the page's two API calls (repeat, remember)
                # go through `window.h3Video` instead of reaching into the page's objects.
                video = await cdp.evaluate(
                    "(() => { const n = document.querySelector('.nav[data-view=\"video\"]'); n.click();"
                    " const params = document.querySelector('#vParams');"
                    " const prompt = document.querySelector('#vPrompt');"
                    " prompt.value = 'проверка кадра'; prompt.dispatchEvent(new Event('input'));"
                    " params.querySelector('.chip[data-a=\"9:16\"]').click();"
                    " params.querySelector('.seed-seg button[data-sf=\"1\"]').click();"
                    " const st = window.h3Video.state;"
                    " return {frame: !!document.querySelector('#vPreview .frame'),"
                    " chips: params.querySelectorAll('.chip[data-a]').length,"
                    " aspect: st.aspect, seedFixed: !!st.seedFixed,"
                    " count: document.querySelector('#vCount').textContent, plen: prompt.value.length,"
                    " seedRow: !!params.querySelector('.seed-row') && !params.querySelector('.seed-row').hidden}; })()")
                check("the video section builds its preview and parameters on the first visit",
                      video and video["frame"] and video["chips"] >= 5, str(video))
                check("a format click in the panel reaches the module's state", video["aspect"] == "9:16", str(video))
                check("a fixed seed opens the seed row", video["seedFixed"] and video["seedRow"], str(video))
                check("the prompt counter follows the input",
                      f"{video.get('plen')} символов" in (video.get("count") or ""), str(video))

                repeated = await cdp.evaluate(
                    "(() => { window.h3Video.repeat({mode: 'video', prompt: 'повтор клипа',"
                    " aspect: '16:9', q: '720p', len: 7});"
                    " return {prompt: document.querySelector('#vPrompt').value,"
                    " dur: window.h3Video.state.dur, aspect: window.h3Video.state.aspect}; })()")
                check("h3Video.repeat fills the prompt, format and length",
                      repeated and repeated["prompt"] == "повтор клипа" and repeated["dur"] == 7
                      and repeated["aspect"] == "16:9", str(repeated))
                await cdp.evaluate(
                    "(() => { const params = document.querySelector('#vParams');"
                    " params.querySelector('.chip[data-a=\"16:9\"]').click();"
                    " params.querySelector('.seed-seg button[data-sf=\"0\"]').click();"
                    " const p = document.querySelector('#vPrompt'); p.value = '';"
                    " p.dispatchEvent(new Event('input')); })()")

                # The picture sections are modules too: `imagePanel` builds one on demand and the
                # panel it publishes is the same object the details window and the agent fill.
                pictures = await cdp.evaluate(
                    "(() => { const k = window.imagePanel('krea'); const q = window.imagePanel('qwen');"
                    " q.fill({prompt: 'правка картинки'});"
                    " return {k: !!(k && k.fill && k.fillList), q: !!(q && q.fill),"
                    " kprompt: !!document.querySelector('#view-krea .kr-prompt'),"
                    " qprompt: (document.querySelector('#view-qwen .qw-prompt') || {}).value || '',"
                    " mounted: window.h3Modes.stats().mounted.filter(m => ['krea', 'qwen', 'inpaint'].includes(m))}; })()")
                check("the picture sections build on demand and publish fill/fillJob",
                      pictures and pictures["k"] and pictures["q"] and pictures["kprompt"], str(pictures))
                check("a picture panel filled before opening keeps the prompt",
                      pictures and pictures["qprompt"] == "правка картинки", str(pictures))

                # The same two sections twice more, with the screenshots a human can look at.
                for view, name in (("video", "video"), ("krea", "krea"), ("montage", "montage"),
                                   ("library", "library"), ("settings", "settings")):
                    opened = await cdp.evaluate(
                        f"(() => {{ const b = document.querySelector('.nav[data-view=\"{view}\"]');"
                        f" if (!b) return false; b.click(); return true; }})()")
                    await asyncio.sleep(0.4)
                    if opened:
                        await cdp.screenshot(shots / f"{name}.png")

                # The page's own event entry, without a ComfyUI: a snapshot of a running job, then
                # its result. This is the one-owner contract seen from the window: one row, one
                # library card even if the event is repeated, and the finished row can be cleared.
                snap = await cdp.evaluate(
                    "(() => { onServerMessage({type: 'hello', engine: true,"
                    " jobs: [{prompt_id: 'ui-job-1', mode: 'video', state: 'running', stage: 'sample', step: 2, max: 10}],"
                    " recent: [], results: []});"
                    " return {rows: document.querySelectorAll('#qList .q-row').length,"
                    " badge: document.querySelector('#qBadge').textContent,"
                    " state: (window.h3State.get('ui-job-1') || {}).state}; })()")
                check("a snapshot puts the running job in the queue",
                      snap and snap["rows"] == 1 and snap["badge"] == "1" and snap["state"] == "running", str(snap))

                done = await cdp.evaluate(
                    "(() => { const item = {id: 'ui-media-1', url: '/ui/previews/video.mp4',"
                    " date: new Date().toISOString(), mode: 'video', kind: 'video', prompt: 'проверка'};"
                    " onServerMessage({type: 'done', prompt_id: 'ui-job-1', item});"
                    " onServerMessage({type: 'done', prompt_id: 'ui-job-1', item});"
                    " return {state: (window.h3State.get('ui-job-1') || {}).state,"
                    " cards: document.querySelectorAll('#lib .li').length,"
                    " results: window.h3State.results().length,"
                    " text: document.querySelector('#qList .q-row') ? document.querySelector('#qList .q-row').textContent : ''}; })()")
                check("a done event finishes the row and adds the result",
                      done and done["state"] == "done" and done["cards"] == 1 and done["results"] == 1,
                      str(done))
                check("the finished row says it is ready", "готово" in (done or {}).get("text", "").lower(), str(done))

                cleared = await cdp.evaluate(
                    "(() => { document.querySelector('#qClear').click();"
                    " return {rows: document.querySelectorAll('#qList .q-row').length,"
                    " text: document.querySelector('#qList').textContent}; })()")
                check("finished rows can be cleared and the empty message comes back",
                      cleared and cleared["rows"] == 0 and "Очередь пуста" in cleared["text"], str(cleared))

                # Mode ownership: a section mounts once, leaving it does not add another handler or
                # timer, and closing the window gives every scope back.
                first_pass = await cdp.evaluate("window.h3Modes.stats()")
                for view in views:
                    await cdp.evaluate(
                        f"(() => {{ const b = document.querySelector('.nav[data-view=\"{view}\"]');"
                        f" if (b) b.click(); }})()")
                    await asyncio.sleep(0.03)
                second_pass = await cdp.evaluate("window.h3Modes.stats()")
                check("every visited section is mounted once",
                      len(first_pass["mounted"]) == len(set(first_pass["mounted"]))
                      and len(first_pass["mounted"]) >= len(views),
                      f"{first_pass['mounted']} / views {views}")
                check("a second round of visits adds no listener, timer or scope",
                      second_pass["scopes"] == first_pass["scopes"]
                      and second_pass["listeners"] <= first_pass["listeners"]
                      and second_pass["intervals"] <= first_pass["intervals"]
                      and second_pass["urls"] <= first_pass["urls"],
                      f"{first_pass} -> {second_pass}")
                check("the active section is the mode the registry knows about",
                      second_pass["current"] == views[-1] if views else True,
                      f"{second_pass['current']} vs {views[-1] if views else '-'}")

                disposed = await cdp.evaluate(
                    "(() => { window.h3Modes.disposeAll(); return window.h3Modes.stats(); })()")
                check("closing the window releases every mode scope",
                      disposed and disposed["scopes"] == 0 and disposed["listeners"] == 0
                      and disposed["intervals"] == 0 and disposed["mounted"] == [],
                      str(disposed))

                errors = cdp.exceptions()
                check("no uncaught exception or console error in the window", not errors,
                      " | ".join(errors[:4]))
                return 0 if all(results) else 1
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=15)
        except subprocess.TimeoutExpired:
            chrome.kill()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    browser = browser_path()
    if not browser:
        print("нет Chrome/Edge: проверка окна пропущена")
        return SKIP

    data = Path(os.environ.get("H3STUDIO_DATA") or make_temp_dir("h3s-ui-"))
    data.mkdir(parents=True, exist_ok=True)
    agent_file = os.environ.get("H3STUDIO_AGENT_FILE") or str(data / "agent.json")
    port = free_port()
    env = {**os.environ, "H3STUDIO_DATA": str(data), "H3STUDIO_AGENT_FILE": agent_file,
           "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    server_log = data / "server.log"
    with open(server_log, "wb") as sink:
        server = subprocess.Popen(
            [sys.executable, "-B", "server.py", "--port", str(port), "--comfy", "http://127.0.0.1:9"],
            cwd=str(APP), env=env, stdout=sink, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    shots = Path(os.environ.get("H3STUDIO_UI_SHOTS") or ROOT / "_test" / "ui-shots")
    shots.mkdir(parents=True, exist_ok=True)
    try:
        status = asyncio.run(wait_http(base + "/api/status", server, 60))
        check("the app starts for the window", bool(status),
              server_log.read_text(encoding="utf-8", errors="replace")[-800:])
        if not status:
            return 1
        code = asyncio.run(run_page(base, browser, shots))
        if code == SKIP:
            return SKIP
        passed = sum(results)
        print(f"\n{passed}/{len(results)} passed   снимки: {shots}")
        return 0 if code == 0 and passed == len(results) else 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    sys.exit(main())

