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
            page = " | ".join(self.exceptions()[-3:])
            raise RuntimeError(f"JS: {text}" + (f" [page: {page}]" if page else ""))
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

                project_ready = await cdp.wait_for(
                    "document.querySelector('#projName')?.textContent === 'Основное'", timeout=10)
                project_bar = await cdp.evaluate(
                    "(() => ({ bar: !!document.querySelector('#tbProj .proj-cur'),"
                    " all: !!document.querySelector('#projAll'),"
                    " pop: !!document.querySelector('#projPop') }))()")
                check("the header shows which project new results belong to",
                      project_ready and project_bar["bar"] and project_bar["all"] and project_bar["pop"],
                      str(project_bar))
                toggled = await cdp.evaluate(
                    "(() => { const b = document.querySelector('#projAll'); b.click();"
                    " const on = b.classList.contains('on'); b.click();"
                    " return on && !b.classList.contains('on'); })()")
                check("the project filter switches to all projects and back", toggled)
                created = await cdp.evaluate(
                    "(async () => { document.querySelector('#projCur').click();"
                    " document.querySelector('#projNewName').value = 'Тестовый проект';"
                    " document.querySelector('#projNewOk').click();"
                    " await new Promise(r => setTimeout(r, 500));"
                    " const active = (await (await fetch('/api/projects')).json()).active;"
                    " return { name: document.querySelector('#projName').textContent, active }; })()",
                    await_promise=True)
                check("a project can be created from the header and becomes active",
                      created["name"] == "Тестовый проект" and created["active"] != "main", str(created))
                restored = await cdp.evaluate(
                    "(async () => { document.querySelector('#projCur').click();"
                    " const item = [...document.querySelectorAll('.proj-item')].find(b => b.textContent.includes('Основное'));"
                    " item.click(); await new Promise(r => setTimeout(r, 400));"
                    " const active = (await (await fetch('/api/projects')).json()).active;"
                    " return { name: document.querySelector('#projName').textContent, active }; })()",
                    await_promise=True)
                check("choosing another project in the header switches it back",
                      restored["name"] == "Основное" and restored["active"] == "main", str(restored))

                # Deleting a draft asks first, right at the cursor: two variants of one shot look
                # the same as thumbnails, and the question must not jump to the middle of the screen.
                popover = await cdp.evaluate(
                    "(async () => {"
                    " const p = window.deleteClipItem({ id: 'ui-pop', prompt: 'Промпт для проверки',"
                    "   preview: true, mode: 'video', res: '1280×736', date: new Date() }, { x: 420, y: 300 });"
                    " await new Promise(r => setTimeout(r, 80));"
                    " const pop = document.querySelector('#draftDelPop');"
                    " const box = pop && !pop.hidden ? pop.getBoundingClientRect() : null;"
                    " const text = pop ? pop.textContent : '';"
                    " pop?.querySelector('[data-ddp=\"no\"]')?.click();"
                    " const result = await p;"
                    " return { shown: !!box, text, result, hidden: !!pop && pop.hidden,"
                    "   left: box ? box.left : -1, top: box ? box.top : -1,"
                    "   inside: !!box && box.left >= 0 && box.top >= 0"
                    "     && box.right <= innerWidth && box.bottom <= innerHeight }; })()",
                    await_promise=True)
                check("the delete cross asks at the cursor, with a title and buttons only",
                      popover["shown"] and "Удалить черновик?" in popover["text"]
                      and "Промпт для проверки" not in popover["text"]
                      and popover["inside"]
                      and abs(popover["left"] - 434) <= 25 and abs(popover["top"] - 314) <= 25
                      and popover["result"] is False and popover["hidden"], str(popover))

                # the interface can be zoomed (Ctrl+wheel): CSS offsets are multiplied by the zoom,
                # so a naive position drifts farther from the cursor the farther right it is clicked
                zoomed = await cdp.evaluate(
                    "(async () => {"
                    " document.documentElement.style.zoom = '1.1';"
                    " const p = window.deleteClipItem({ id: 'ui-pop2', preview: true, mode: 'video',"
                    "   date: new Date() }, { x: 420, y: 300 });"
                    " await new Promise(r => setTimeout(r, 80));"
                    " const pop = document.querySelector('#draftDelPop');"
                    " const b = pop.getBoundingClientRect();"
                    " const out = { left: b.left, top: b.top, right: b.right, bottom: b.bottom };"
                    " pop.querySelector('[data-ddp=\"no\"]').click();"
                    " await p;"
                    " document.documentElement.style.zoom = '';"
                    " return out; })()", await_promise=True)
                check("the question stays at the cursor when the interface is zoomed",
                      abs(zoomed["left"] - 434) <= 25 and abs(zoomed["top"] - 314) <= 25
                      and zoomed["right"] <= 1440 and zoomed["bottom"] <= 900, str(zoomed))

                # a clip that already reached the Library can be deleted from the same cross; the
                # question changes, because the record leaves the Library as well as the file
                lib_pop = await cdp.evaluate("""
                    (async () => {
                      window.eval('LIB.push({ id: "lib-ui", mode: "video", kind: "video", preview: false,'
                        + ' project: "main", date: new Date(), file: { type: "local", path: "C:/nope.mp4",'
                        + ' filename: "nope.mp4" } }); renderLib();');
                      const p = window.deleteClipItem(
                        { id: 'lib-ui', preview: false, mode: 'video', date: new Date() }, { x: 300, y: 300 });
                      await new Promise(r => setTimeout(r, 80));
                      const pop = document.querySelector('#draftDelPop');
                      const text = pop ? pop.textContent : '';
                      pop?.querySelector('[data-ddp="no"]')?.click();
                      const result = await p;
                      window.eval('(() => { const i = LIB.findIndex(x => x.id === "lib-ui");'
                        + ' if (i >= 0) LIB.splice(i, 1); renderLib(); })()');
                      return { text, result };
                    })()
                """, await_promise=True)
                check("a clip that is already in the Library asks with its own wording",
                      "Удалить клип из Библиотеки?" in lib_pop["text"]
                      and lib_pop["result"] is False, str(lib_pop))

                # a stage is a record of the project now, not of this browser: it is saved through
                # the server, its attached file is uploaded there, and the section paints the answer
                stage = await cdp.evaluate("""
                    (async () => {
                      const saved = await window.postStage({ id: 'ui-stage', mode: 'video',
                        name: 'Проверка', s: { q: '720p' }, prompt: 'тест',
                        date: new Date().toISOString(), files: ['video/first'],
                        names: { 'video/first': 'frame.png' } });
                      const file = new File([new Uint8Array([1, 2, 3])], 'frame.png', { type: 'image/png' });
                      await window.uploadStageFile('ui-stage', 'video/first', file);
                      const served = await fetch('/api/stages/ui-stage/file?key=video/first');
                      const bytes = served.ok ? [...new Uint8Array(await served.arrayBuffer())] : [];
                      const type = served.headers.get('content-type');
                      const shown = document.querySelector('#view-video .st-bar .st-n')?.textContent;
                      const listed = (await (await fetch('/api/stages')).json()).stages.length;
                      await window.bmDelete('ui-stage');
                      const after = document.querySelector('#view-video .st-bar .st-n')?.textContent;
                      const gone = (await fetch('/api/stages/ui-stage/file?key=video/first')).status;
                      return { saved: saved && saved.id, shown, listed, after, bytes, type, gone };
                    })()
                """, await_promise=True)
                check("a stage and its file are saved to the project, served, and deleted with it",
                      stage["saved"] == "ui-stage" and stage["shown"] == "1"
                      and stage["listed"] >= 1 and stage["after"] == "0"
                      and stage["bytes"] == [1, 2, 3] and stage["type"] == "image/png"
                      and stage["gone"] == 404, str(stage))

                # «Замена персонажа» used to have no stages at all: its state and slots are already in
                # the refs family's maps, so the section only had to join the list of sections with a bar
                swap_stage = await cdp.evaluate("""
                    (async () => {
                      const wait = ms => new Promise(r => setTimeout(r, ms));
                      document.querySelector('.nav[data-view="swap"]').click();
                      await wait(300);
                      const bar = document.querySelector('#view-swap .st-bar');
                      const out = { bar: !!bar, save: !!(bar && bar.querySelector('.st-save')),
                                    n0: bar ? bar.querySelector('.st-n').textContent : null };
                      bar.querySelector('.st-save').click();      // the section's own button, not postStage
                      await wait(1000);
                      const mine = (await (await fetch('/api/stages')).json()).stages.filter(s => s.mode === 'swap');
                      out.mode = mine.length ? mine[0].mode : '';
                      out.n1 = document.querySelector('#view-swap .st-bar .st-n')?.textContent;
                      out.item = (document.querySelector('#view-swap .st-bar .st-i') || {}).textContent || '';
                      if (mine.length) await window.bmDelete(mine[0].id);
                      out.n2 = document.querySelector('#view-swap .st-bar .st-n')?.textContent;
                      return out;
                    })()
                """, await_promise=True)
                check("«Замена персонажа» has stages of its own: save, list, delete",
                      swap_stage["bar"] and swap_stage["save"] and swap_stage["mode"] == "swap"
                      and swap_stage["n0"] == "0" and swap_stage["n1"] == "1"
                      and swap_stage["n2"] == "0" and swap_stage["item"].strip() != "", str(swap_stage))

                # A stage remembers the clip that was on the screen, and opening it brings the clip back
                # (only the video section did that). The player is the same shared panel everywhere,
                # so each section keeps the id its own `onShow` wrote and the page asks it to show again.
                restored_clip = await cdp.evaluate("""
                    (async () => {
                      const wait = ms => new Promise(r => setTimeout(r, ms));
                      addDraft({ id: 'ls-ui-clip', kind: 'video', mode: 'lipsync', preview: true,
                                 date: new Date(), url: '/ui/previews/lipsync.mp4', len: 3, prompt: 'губы' });
                      addDraft({ id: 'rf-ui-clip', kind: 'video', mode: 'refs', preview: true,
                                 date: new Date(), url: '/ui/previews/refs.mp4', len: 5, prompt: 'рефы' });
                      const stage = await window.postStage({ id: 'ui-ls-stage', mode: 'lipsync', name: 'Губы',
                        s: { aspect: '9:16', shownId: 'ls-ui-clip' }, prompt: 'губы',
                        date: new Date().toISOString() });
                      await window.postStage({ id: 'ui-rf-stage', mode: 'refs', name: 'Рефы',
                        s: { aspect: '16:9', shownId: 'rf-ui-clip' }, prompt: 'рефы',
                        date: new Date().toISOString() });
                      // the clip of this stage is gone: the newest clip of the project takes its place
                      await window.postStage({ id: 'ui-rf-gone', mode: 'refs', name: 'Без клипа',
                        s: { shownId: 'no-such-clip' }, date: new Date().toISOString() });
                      const out = { saved: (stage.s || {}).shownId };
                      await window.bmOpen('ui-ls-stage'); await wait(500);
                      const lv = document.querySelector('#lPreview .result');
                      out.ls = { hidden: !!(lv && lv.hidden), src: lv ? lv.getAttribute('src') : '',
                                 id: window.h3Lipsync.state.shownId };
                      await window.bmOpen('ui-rf-stage'); await wait(500);
                      const rv = document.querySelector('#view-refs .rm-preview .result');
                      out.rf = { hidden: !!(rv && rv.hidden), src: rv ? rv.getAttribute('src') : '',
                                 id: window.RM.refs.S.shownId };
                      await window.bmOpen('ui-rf-gone'); await wait(500);
                      out.fallback = { src: rv.getAttribute('src'), hidden: rv.hidden,
                                       id: window.RM.refs.S.shownId };
                      // the video section did this before: the clip must also survive the delayed
                      // apply() of the stage (it fires ~0,8 s later and used to empty the player)
                      addDraft({ id: 'vd-ui-clip', kind: 'video', mode: 'video', preview: true,
                                 date: new Date(), url: '/ui/previews/video.mp4', len: 5, prompt: 'видео' });
                      await window.postStage({ id: 'ui-vd-stage', mode: 'video', name: 'Видео',
                        s: { shownId: 'vd-ui-clip' }, date: new Date().toISOString() });
                      await window.bmOpen('ui-vd-stage'); await wait(1100);
                      const vv = document.querySelector('#vPreview .result');
                      out.vd = { hidden: vv.hidden, src: vv.getAttribute('src'),
                                 id: window.h3Video.state.shownId };
                      const rest = (await (await fetch('/api/stages')).json()).stages
                        .filter(s => ['lipsync', 'refs', 'video'].includes(s.mode));
                      for (const s of rest) await window.bmDelete(s.id);
                      dropDraft('ls-ui-clip'); dropDraft('rf-ui-clip'); dropDraft('vd-ui-clip');
                      return out;
                    })()
                """, await_promise=True)
                check("a stage keeps the id of the clip it was saved with",
                      restored_clip["saved"] == "ls-ui-clip", str(restored_clip["saved"]))
                check("opening a lipsync stage brings its clip back into the player",
                      not restored_clip["ls"]["hidden"] and "lipsync.mp4" in restored_clip["ls"]["src"]
                      and restored_clip["ls"]["id"] == "ls-ui-clip", str(restored_clip["ls"]))
                check("opening a refs stage brings its clip back into the player",
                      not restored_clip["rf"]["hidden"] and "refs.mp4" in restored_clip["rf"]["src"]
                      and restored_clip["rf"]["id"] == "rf-ui-clip", str(restored_clip["rf"]))
                check("a stage whose clip was deleted shows the newest one instead of an empty player",
                      not restored_clip["fallback"]["hidden"]
                      and "refs.mp4" in restored_clip["fallback"]["src"]
                      and restored_clip["fallback"]["id"] == "rf-ui-clip", str(restored_clip["fallback"]))
                check("the clip a video stage brings back stays after the stage finishes opening",
                      not restored_clip["vd"]["hidden"] and "video.mp4" in restored_clip["vd"]["src"]
                      and restored_clip["vd"]["id"] == "vd-ui-clip", str(restored_clip["vd"]))

                # A film of the studio belongs to a project: the studio used to load whichever film
                # was saved last, so a project could continue another project's film. Now the header
                # switch takes the studio to that project's own film (or starts a new one).
                film = await cdp.evaluate("""
                    (async () => {
                      const wait = ms => new Promise(r => setTimeout(r, ms));
                      const until = async (fn, ms = 6000) => { const t0 = Date.now();
                        while (Date.now() - t0 < ms) { if (fn()) return true; await wait(60); } return false; };
                      document.querySelector('.nav[data-view="long"]').click();
                      await wait(400);
                      const root = window.RM.long.root;
                      const pA = window.h3Projects.active(), runA = root._run, out = { pA, runA };
                      const first = document.querySelector('#view-long .sc textarea');
                      first.value = 'ФИЛЬМ А'; first.dispatchEvent(new Event('input', { bubbles: true }));
                      root._flush();                       // the studio's own save, into its project
                      let got = {};
                      for (let i = 0; i < 50; i++) {
                        got = await (await fetch('/api/long/project?project=' + encodeURIComponent(pA))).json();
                        if (got.run === runA) break;
                        await wait(100);
                      }
                      out.saved = { run: got.run || '', project: (got.project || {}).project || '',
                                    text: ((got.project || {}).scenes || [{}])[0].prompt || '' };
                      // a new project in the header: the studio must not keep editing project A's film
                      document.querySelector('#projCur').click();
                      document.querySelector('#projNewName').value = 'Фильм Б';
                      document.querySelector('#projNewOk').click();
                      await until(() => window.h3Projects.active() !== pA);
                      await until(() => root._run !== runA);
                      out.b = { project: window.h3Projects.active(), run: root._run,
                                own: root._project, newFilm: root._run !== runA,
                                scenes: [...document.querySelectorAll('#view-long .sc textarea')]
                                  .map(t => t.value) };
                      // and back: the film of this project opens again with its text
                      document.querySelector('#projCur').click();
                      const back = [...document.querySelectorAll('.proj-item')]
                        .find(b => b.textContent.includes('Основное'));
                      back.click();
                      await until(() => window.h3Projects.active() === pA);
                      await until(() => root._run === runA);
                      out.back = { run: root._run, own: root._project,
                                   scenes: [...document.querySelectorAll('#view-long .sc textarea')]
                                     .map(t => t.value) };
                      // a stage of the film opened while another project is chosen: bmOpen switches the
                      // project (and the studio) first and only then restores the stage
                      document.querySelector('#view-long .st-bar .st-save').click();
                      await wait(1000);
                      const mine = (await (await fetch('/api/stages')).json()).stages.filter(s => s.mode === 'long');
                      out.stage = mine.length ? { id: mine[0].id, project: mine[0].project, run: mine[0].run } : null;
                      document.querySelector('#projCur').click();
                      [...document.querySelectorAll('.proj-item')]
                        .find(b => b.textContent.includes('Фильм Б')).click();
                      await until(() => window.h3Projects.active() === out.b.project);
                      await until(() => root._run !== runA);
                      await window.bmOpen(out.stage.id);
                      await wait(900);
                      out.reopen = { project: window.h3Projects.active(), run: root._run,
                                     scenes: [...document.querySelectorAll('#view-long .sc textarea')]
                                       .map(t => t.value) };
                      for (const s of (await (await fetch('/api/stages')).json()).stages
                                       .filter(s => s.mode === 'long')) await window.bmDelete(s.id);
                      return out;
                    })()
                """, await_promise=True)
                check("the studio saves its film into the project that is open",
                      film["saved"]["run"] == film["runA"] and film["saved"]["project"] == film["pA"]
                      and film["saved"]["text"] == "ФИЛЬМ А", str(film["saved"]))
                check("another project starts its own film instead of continuing this one",
                      film["b"]["newFilm"] and film["b"]["project"] != film["pA"]
                      and film["b"]["own"] == film["b"]["project"]
                      and [s for s in film["b"]["scenes"] if s.strip()] == [], str(film["b"]))
                check("coming back to a project opens its film again, with its scenes",
                      film["back"]["run"] == film["runA"] and film["back"]["own"] == film["pA"]
                      and (film["back"]["scenes"] or [""])[0] == "ФИЛЬМ А", str(film["back"]))
                check("a stage of a film opened from another project brings that project and the film back",
                      film["stage"] and film["stage"]["project"] == film["pA"]
                      and film["reopen"]["project"] == film["pA"]
                      and film["reopen"]["run"] == film["stage"]["run"]
                      and (film["reopen"]["scenes"] or [""])[0] == "ФИЛЬМ А", str(film["reopen"]))

                # the previous version kept stages in localStorage: on the first start of this one
                # they are uploaded to the active project and the browser copy is dropped
                await cdp.evaluate(
                    "localStorage.setItem('h3-bookmarks', JSON.stringify([{"
                    " id: 'legacy-stage', mode: 'video', name: 'Старый этап', auto: false,"
                    " title: 'старый', date: '2026-11-01T10:00:00', s: { q: '480p' },"
                    " prompt: 'старый', files: [] }])); true")
                await cdp.call("Page.reload")
                await cdp.wait_for("document.readyState === 'complete' && !!window.h3Video", timeout=40)
                listed = []
                for _ in range(40):
                    listed = await cdp.evaluate(
                        "(async () => (await (await fetch('/api/stages')).json()).stages.map(s => s.id))()",
                        await_promise=True)
                    if "legacy-stage" in listed:
                        break
                    await asyncio.sleep(0.2)
                left = await cdp.evaluate("localStorage.getItem('h3-bookmarks')")
                count = await cdp.evaluate("document.querySelector('#view-video .st-bar .st-n')?.textContent")
                check("stages of the previous version move into the project on first start",
                      "legacy-stage" in listed and left is None and count == "1",
                      str({"ids": listed, "left": left, "count": count}))
                await cdp.evaluate("window.bmDelete('legacy-stage')", await_promise=True)

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

                # The lipsync editor is a module too, but its audio lives outside the panel: a
                # generated WAV goes through the same `loadAudio` the page's restore uses.
                lips = await cdp.evaluate(
                    "(() => { document.querySelector('.nav[data-view=\"lipsync\"]').click();"
                    " const sr = 8000, n = 8000, b = new ArrayBuffer(44 + n * 2), v = new DataView(b);"
                    " const w = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));"
                    " w(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); w(8, 'WAVEfmt ');"
                    " v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);"
                    " v.setUint32(24, sr, true); v.setUint32(28, sr * 2, true); v.setUint16(32, 2, true);"
                    " v.setUint16(34, 16, true); w(36, 'data'); v.setUint32(40, n * 2, true);"
                    " for (let i = 0; i < n; i++) v.setInt16(44 + i * 2, Math.round(Math.sin(i / 10) * 2000), true);"
                    " const file = new File([b], 'test.wav', {type: 'audio/wav'});"
                    " return window.h3Lipsync.loadAudio(file).then(() => {"
                    "  const st = window.h3Lipsync.state, clip = window.h3Lipsync.audioClip();"
                    "  return {frame: !!document.querySelector('#lPreview .frame'),"
                    "   chips: document.querySelectorAll('#lParams .chip[data-a]').length,"
                    "   refs: document.querySelectorAll('#refs .ref').length,"
                    "   dur: st.dur0, start: st.start, end: st.end,"
                    "   clip: clip ? clip.size : 0, count: document.querySelector('#rCount').textContent};"
                    " }); })()", await_promise=True)
                check("the lipsync section builds its panel and nine reference slots",
                      lips and lips["frame"] and lips["chips"] >= 5 and lips["refs"] == 9, str(lips))
                check("a WAV loaded through the module reaches the editor state",
                      lips and abs(lips["dur"] - 1.0) < 0.02 and lips["start"] == 0
                      and abs(lips["end"] - 1.0) < 0.02, str(lips))
                check("the module hands the selected fragment to the chat as a WAV file",
                      lips and lips["clip"] > 44, str(lips))

                # The shared factory: five sections in one module, built at load because the
                # remembered files and the details window need their slots during setup.
                refs = await cdp.evaluate(
                    "(() => { document.querySelector('.nav[data-view=\"refs\"]').click();"
                    " const params = document.querySelector('#view-refs .rm-params');"
                    " params.querySelector('.chip[data-a=\"9:16\"]').click();"
                    " window.h3Refs.modelsChanged();"
                    " return {modes: Object.keys(window.RM).sort(),"
                    "  aspect: window.RM.refs.S.aspect,"
                    "  slots: document.querySelectorAll('#view-refs .ref').length,"
                    "  built: ['refs', 'actor', 'swap', 'action', 'long'].every(m =>"
                    "   window.RM[m] && window.RM[m].root && window.RM[m].root.childElementCount > 0),"
                    "  flush: typeof window.RM.long.root._flush,"
                    "  preview: typeof window.RM.refs.prev.pause}; })()")
                check("the refs family publishes five built sections through window.RM",
                      refs and refs["modes"] == ["action", "actor", "long", "refs", "swap"]
                      and refs["built"] and refs["slots"] == 9, str(refs))
                check("a format click in the shared parameters reaches the mode state",
                      refs and refs["aspect"] == "9:16", str(refs))
                check("the long project can be flushed and the preview paused",
                      refs and refs["flush"] == "function" and refs["preview"] == "function", str(refs))

                # The same two sections twice more, with the screenshots a human can look at.
                for view, name in (("video", "video"), ("krea", "krea"), ("lipsync", "lipsync"),
                                   ("refs", "refs"), ("long", "long"),
                                   ("montage", "montage"), ("library", "library"), ("settings", "settings")):
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

                # Own workflows: a section that used to be wired while the page was parsed, now a
                # module with a lazy mount. The form is built from the server's report; a stub stands
                # in for ComfyUI (the check runs with a closed port), so the field kinds and the
                # write-back of an edited value are seen here.
                custom = await cdp.evaluate(
                    "(() => { document.querySelector('.nav[data-view=\"custom\"]').click();"
                    " return !!document.querySelector('#cwSel'); })()")
                # the list is read from the server: the select fills in on the next tick
                await cdp.wait_for("document.querySelectorAll('#cwSel option').length > 0", timeout=10)
                custom = await cdp.evaluate(
                    "(() => { const st = window.h3Modes.stats();"
                    " return {mounted: st.mounted.includes('custom'),"
                    "  select: !!document.querySelector('#cwSel'),"
                    "  options: document.querySelectorAll('#cwSel option').length,"
                    "  howto: !document.querySelector('#cwHowto').hidden,"
                    "  handle: !!(window.h3Custom && window.h3Custom.state)}; })()")
                check("the own-workflow section mounts and reads its list",
                      custom and custom["mounted"] and custom["select"] and custom["handle"]
                      and custom["howto"] and custom["options"] >= 1, str(custom))

                form = await cdp.evaluate(
                    """(async () => {
                      const report = {id: 'demo', name: 'Демо', notes: [], missing_nodes: [], widgets: [
                        {key: '6.text', node: '6', title: 'CLIP Text Encode', cls: 'CLIPTextEncode', name: 'text',
                         value: 'старый промпт', role: 'prompt', kind: 'string', multiline: true},
                        {key: '7.text', node: '7', title: 'CLIP Text Encode', cls: 'CLIPTextEncode', name: 'text',
                         value: 'брак', role: 'negative', kind: 'string', multiline: true},
                        {key: '3.width', node: '3', title: 'Empty Latent', cls: 'EmptyLatentImage', name: 'width',
                         value: 768, role: 'size', kind: 'int', min: 64, max: 2048, step: 8},
                        {key: '3.height', node: '3', title: 'Empty Latent', cls: 'EmptyLatentImage', name: 'height',
                         value: 1376, role: 'size', kind: 'int', min: 64, max: 2048, step: 8},
                        {key: '3.seed', node: '3', title: 'KSampler', cls: 'KSampler', name: 'seed',
                         value: 42, role: 'seed', kind: 'int'},
                        {key: '3.steps', node: '3', title: 'KSampler', cls: 'KSampler', name: 'steps',
                         value: 20, role: 'steps', kind: 'int'},
                        {key: '3.cfg', node: '3', title: 'KSampler', cls: 'KSampler', name: 'cfg',
                         value: 7.5, role: 'cfg', kind: 'float'},
                        {key: '4.aspect', node: '4', title: 'Ratio', cls: 'PrimitiveString',
                         name: 'value', value: '16:9', role: 'aspect', kind: 'combo', options: ['16:9', '9:16']},
                        {key: '10.image', node: '10', title: 'Load Image', cls: 'LoadImage', name: 'image',
                         value: 'in.png', role: 'media', kind: 'combo', options: ['in.png'], mkind: 'image', has_default: true}
                      ]};
                      const orig = window.h3Api.getJSON;
                      window.h3Api.getJSON = (u, o) => String(u).startsWith('/api/wf/') ? Promise.resolve(report) : orig(u, o);
                      try {
                        await window.h3Custom.open('demo');
                        const body = document.querySelector('#cwBody');
                        const ta = body.querySelector('textarea[data-k="6.text"]');
                        const before = window.h3Custom.state.vals['6.text'];
                        ta.value = 'новый промпт'; ta.dispatchEvent(new Event('input', {bubbles: true}));
                        const size = body.querySelectorAll('.cw-pair input');
                        size[0].value = '801';
                        size[0].dispatchEvent(new Event('change', {bubbles: true}));
                        return {left: !!body.querySelector('.cw-left'), right: !!body.querySelector('.cw-right'),
                          prompt: !!ta, negative: !!body.querySelector('.cw-pp.neg textarea'),
                          pair: size.length, seed: !!body.querySelector('[data-rnd="3.seed"]'),
                          media: body.querySelectorAll('.cw-file').length,
                          go: !!body.querySelector('#cwGo'), before,
                          after: window.h3Custom.state.vals['6.text'],
                          width: window.h3Custom.state.vals['3.width'],
                          saved: (JSON.parse(localStorage.getItem('h3-cw-demo') || '{}'))['6.text']};
                      } finally { window.h3Api.getJSON = orig; }
                    })()""", await_promise=True)
                check("the own-workflow form is built from the server's report",
                      form and form["left"] and form["right"] and form["prompt"] and form["negative"]
                      and form["pair"] == 2 and form["seed"] and form["media"] == 1 and form["go"], str(form))
                check("an edited field reaches the module's state and localStorage",
                      form and form["after"] == "новый промпт" and form["saved"] == "новый промпт"
                      and form["before"] == "старый промпт", str(form))
                check("a size typed into the paired fields is rounded to a multiple of 32",
                      form and form["width"] == "800", str(form))

                # The delete cross is not only in «Прошлые клипы»: the picture sections, the own
                # workflow's history and the clip cards of «Монтаж» ask the same question at the
                # cursor through the page's one `deleteClipItem`, and the section keeps nothing of a
                # picture it deleted. The server call is stood in for here (the list is what is tested).
                deleted = await cdp.evaluate("""
                    (async () => {
                      const calls = [];
                      const orig = window.deleteClipItem;
                      window.deleteClipItem = async (it, at) => {
                        calls.push({ id: it.id, x: at && at.x, y: at && at.y });
                        if (it.preview) dropDraft(it.id); else dropLib(it.id);
                        return true;
                      };
                      const wait = ms => new Promise(r => setTimeout(r, ms));
                      const pic = '/ui/previews/krea.jpg', out = {};
                      try {
                        addDraft({ id: 'pic-a', kind: 'image', mode: 'krea', preview: true,
                                   date: new Date(Date.now() - 5000), url: pic, prompt: 'первая' });
                        addDraft({ id: 'pic-b', kind: 'image', mode: 'krea', preview: true,
                                   date: new Date(), url: pic, prompt: 'вторая' });
                        const ph = document.querySelector('#view-krea .ip-hist');
                        ph.querySelector('.cw-th[data-id="pic-b"]').click();
                        await wait(80);
                        const px = ph.querySelector('.cw-th[data-id="pic-b"] .cw-x');
                        out.pic = { cross: !!px, title: px ? px.title : '',
                                    shown: !document.querySelector('#view-krea .ip-res').hidden };
                        px.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 321, clientY: 234 }));
                        await wait(200);
                        out.pic.on = (ph.querySelector('.cw-th.on') || {}).dataset?.id || '';
                        out.pic.left = [...ph.querySelectorAll('.cw-th')].map(t => t.dataset.id);
                        out.pic.calls = calls.splice(0);

                        addDraft({ id: 'cw-a', kind: 'image', mode: 'custom', wf: 'demo', preview: true,
                                   date: new Date(Date.now() - 5000), url: pic, prompt: 'раз' });
                        addDraft({ id: 'cw-b', kind: 'image', mode: 'custom', wf: 'demo', preview: true,
                                   date: new Date(), url: pic, prompt: 'два' });
                        const ch = document.querySelector('#cwHist');
                        ch.querySelector('.cw-th[data-id="cw-b"]').click();
                        await wait(80);
                        const cx = ch.querySelector('.cw-th[data-id="cw-b"] .cw-x');
                        out.cw = { cross: !!cx, on: (ch.querySelector('.cw-th.on') || {}).dataset?.id || '' };
                        cx.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 333, clientY: 222 }));
                        await wait(220);
                        out.cw.after = (ch.querySelector('.cw-th.on') || {}).dataset?.id || '';
                        out.cw.left = [...ch.querySelectorAll('.cw-th')].map(t => t.dataset.id);
                        out.cw.calls = calls.splice(0);

                        addDraft({ id: 'mt-a', kind: 'video', mode: 'video', preview: true, date: new Date(),
                                   url: '/ui/previews/video.mp4', len: 5, prompt: 'в монтаже' });
                        await wait(150);
                        const box = document.querySelector('#view-montage .mt-lib');
                        const card = box.querySelector('.mt-card[data-id="mt-a"]');
                        const mx = card && card.querySelector('.mt-del');
                        out.mt = { card: !!card, del: !!mx, icon: !!(mx && mx.querySelector('svg')),
                                   hide: !!(card && card.querySelector('.mt-x')) };
                        mx.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 355, clientY: 244 }));
                        await wait(300);
                        out.mt.left = [...box.querySelectorAll('.mt-card')].map(c => c.dataset.id);
                        out.mt.strip = document.querySelectorAll('#view-montage .mt-item').length;
                        out.mt.calls = calls.splice(0);
                      } finally {
                        window.deleteClipItem = orig;
                        ['pic-a', 'pic-b', 'cw-a', 'cw-b', 'mt-a'].forEach(id => dropDraft(id));
                      }
                      return out;
                    })()
                """, await_promise=True)
                check("a picture in «Прошлые изображения» has a delete cross at the cursor",
                      deleted["pic"]["cross"] and "Удалить черновик" in deleted["pic"]["title"]
                      and deleted["pic"]["calls"] == [{"id": "pic-b", "x": 321, "y": 234}], str(deleted["pic"]))
                check("the picture on the screen gives way to the next one when it is deleted",
                      deleted["pic"]["on"] == "pic-a" and "pic-b" not in deleted["pic"]["left"], str(deleted["pic"]))
                check("a result of an own workflow can be deleted from its history",
                      deleted["cw"]["cross"] and deleted["cw"]["on"] == "cw-b"
                      and deleted["cw"]["after"] == "cw-a" and deleted["cw"]["calls"][0]["x"] == 333,
                      str(deleted["cw"]))
                check("a clip card of «Монтаж» has its own delete, apart from «убрать из списка»",
                      deleted["mt"]["card"] and deleted["mt"]["del"] and deleted["mt"]["icon"]
                      and deleted["mt"]["hide"] and "mt-a" not in deleted["mt"]["left"]
                      and deleted["mt"]["strip"] == 0
                      and deleted["mt"]["calls"] == [{"id": "mt-a", "x": 355, "y": 244}],
                      str(deleted["mt"]))
                await asyncio.sleep(0.3)
                await cdp.screenshot(shots / "custom.png")

                # LoRA: an empty section in the page, built by its module on the first visit, with
                # the download hook the page's event router calls by name.
                await cdp.evaluate("(() => { document.querySelector('.nav[data-view=\"loras\"]').click(); })()")
                # the list of LoRAs is read from the server on enter
                await cdp.wait_for("(document.querySelector('#view-loras .lr-mine') || {}).textContent", timeout=10)
                loras = await cdp.evaluate(
                    "(() => { const root = document.querySelector('#view-loras');"
                    " return {mounted: window.h3Modes.stats().mounted.includes('loras'),"
                    "  built: !!root.querySelector('.card') && !!root.querySelector('.lr-mine'),"
                    "  hint: (root.querySelector('.lr-mine') || {}).textContent || '',"
                    "  hook: typeof window.onLoraDownload === 'function',"
                    "  tokens: !!root.querySelector('.lr-tok-save'),"
                    "  search: !!root.querySelector('.lr-find')}; })()")
                check("the LoRA section is built by its module and installs the download hook",
                      loras and loras["mounted"] and loras["built"] and loras["hook"]
                      and loras["tokens"] and loras["search"], str(loras))
                check("with no ComfyUI the LoRA list says what to do",
                      loras and "ComfyUI не видит" in loras["hint"], str(loras))
                await asyncio.sleep(0.3)
                await cdp.screenshot(shots / "loras.png")

                # The agent: the section's markup is static, the module owns the conversation, the
                # attachments and the model card. `h3Agent` is the page's door into the chat (a
                # Library picture "into the chat"); the target buttons write into the module's state.
                await cdp.evaluate("(() => { document.querySelector('.nav[data-view=\"agent\"]').click(); })()")
                await cdp.wait_for("(document.querySelector('#agBig') || {}).textContent", timeout=15)
                agent = await cdp.evaluate(
                    "(() => { const st = window.h3Modes.stats();"
                    " return {mounted: st.mounted.includes('agent'),"
                    "  handle: !!(window.h3Agent && window.h3Agent.state),"
                    "  card: (document.querySelector('#agBig') || {}).textContent || '',"
                    "  verdict: (document.querySelector('#agState') || {}).textContent || '',"
                    "  targets: document.querySelectorAll('#agTarget button[data-t]').length,"
                    "  chats: (document.querySelector('#agHist') || {}).textContent || '',"
                    "  send: !!document.querySelector('#agSend')}; })()")
                check("the agent section mounts with its model card and target buttons",
                      agent and agent["mounted"] and agent["handle"] and agent["send"]
                      and agent["targets"] >= 7 and agent["card"].strip() and agent["verdict"].strip(),
                      str(agent))
                check("the chat list is read on entering the section",
                      agent and "переписки" in agent["chats"], str(agent))

                attached = await cdp.evaluate(
                    """(async () => {
                      const b = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='), c => c.charCodeAt(0));
                      await window.h3Agent.attachFile(new File([b], 'picture.png', {type: 'image/png'}));
                      return {files: document.querySelectorAll('#agAtt button[data-i]').length,
                        imgs: document.querySelectorAll('#agAtt img').length,
                        cross: !document.querySelector('#agClrField').hidden};
                    })()""", await_promise=True)
                check("a picture attached through h3Agent reaches the chat",
                      attached and attached["files"] == 1 and attached["imgs"] == 1 and attached["cross"],
                      str(attached))

                target = await cdp.evaluate(
                    "(() => { document.querySelector('#agTarget2 button[data-t=\"krea\"]').click();"
                    " const st = window.h3Agent.state;"
                    " const box = document.querySelector('#agTarget2 button[data-t=\"krea\"]');"
                    " const out = {target: st.target, on: box.classList.contains('on'),"
                    "  ctx: (document.querySelector('#agCtx') || {}).textContent || ''};"
                    " document.querySelector('#agAtt button[data-i]').click();"
                    " out.left = document.querySelectorAll('#agAtt button[data-i]').length;"
                    " return out; })()")
                check("a target click writes into the module's state and its context line",
                      target and target["target"] == "krea" and target["on"]
                      and "Krea" in target["ctx"] and target["left"] == 0, str(target))
                await asyncio.sleep(0.3)
                await cdp.screenshot(shots / "agent.png")

                hooks_before = await cdp.evaluate("DRAFT_HOOKS.length")

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
                hooks_after = await cdp.evaluate(
                    "({hooks: DRAFT_HOOKS.length, download: typeof window.onLoraDownload})")
                check("the sections' page-wide hooks are taken back on close",
                      hooks_after and hooks_after["download"] == "undefined"
                      and hooks_after["hooks"] < hooks_before,
                      f"{hooks_before} -> {hooks_after}")

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


