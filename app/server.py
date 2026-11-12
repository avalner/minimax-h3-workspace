"""Minimax H3 Workspace local server.

Serves the UI and talks to ComfyUI over its HTTP/WebSocket API: uploads inputs,
queues workflows, relays progress and live previews to the browser, and keeps a
small library of finished videos. On first run it either connects to the user's
own ComfyUI or installs one (plus node packs and models) and manages it.

    python app/server.py [--port 8765] [--open]
"""

import argparse
import asyncio
import contextlib
import ctypes
import hashlib
import json
import os
import random
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import uuid
import urllib.parse
import webbrowser
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import web

import agent as ag
import agentapi
import catalog
import civitai_client
import cloud
import customwf
import datadir
import engine
import ffmpeg_mgr
import hf_client
import instance
import loras_registry
import monitor
import mp4info
import report
import speech
import updater
import watchdog
from backends.base import BackendError, BackendUnavailable, SubmitRejected
from backends.comfy import ComfyBackend
from domain.errors import InvalidSpec, InvalidTransition, JobConflict
from domain.jobs import (MAX_SECONDS, InputRef, JobRecord, JobSpec, JobState, ResultPolicy, new_job_id,
                         public_state)
from runtime.tasks import TaskSupervisor
from services.engines import EngineManager
from services.jobs import JobService
from services.media import EmptyResult, MediaService
from storage.atomic import atomic_write_json, atomic_write_text
from storage.database import Database
from storage.migrations import migrate
from storage.repositories import (IdempotencyRepository, JobRepository, LibraryRepository, MediaRepository,
                                  ResultRepository)
from version import APP_VERSION


def build_stamp():
    """Build date and time shown in the UI: the exe file's own timestamp (frozen build) — tells the user
    which build they are actually running, since the release number changes only at release time."""
    from datetime import datetime
    try:
        p = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__)
        return datetime.fromtimestamp(p.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return ""


def split_frames(text):
    """One frame per line / numbered item: "1: ...", "Frame 2. ...", "Кадр 3 - ..."."""
    parts = re.split(r"(?m)^\s*(?:frame|кадр)?\s*\d+\s*[:.)\-–]\s*", text.strip(), flags=re.I)
    frames = [p.strip().replace("\n", " ") for p in parts if p.strip()]
    if len(frames) < 2:
        frames = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return frames


def _ssl_context():
    # Windows store + the bundled certifi list: helps PCs with an outdated root-certificate store
    import ssl
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:
        pass
    return ctx


# ---------- pictures on the Windows clipboard ----------
# The web view is not allowed to read or write the picture clipboard itself (Chromium asks the host for
# a permission the window never grants), so the server does it: a quick format check with ctypes, and the
# conversion to/from PNG through .NET in a short-lived helper process.

CLIP_HELPER = r"""param([string]$Out, [string]$In, [string]$From)
Add-Type -AssemblyName System.Windows.Forms, System.Drawing

# put a PNG file on the clipboard: SetImage publishes CF_BITMAP/CF_DIB, which every program understands
if ($In) {
    $img = [System.Drawing.Image]::FromFile($In)
    try {
        $ok = $false
        for ($i = 0; $i -lt 25 -and -not $ok; $i++) {
            try { [System.Windows.Forms.Clipboard]::SetImage($img); $ok = $true } catch { Start-Sleep -Milliseconds 80 }
        }
        if (-not $ok) { exit 5 }
    } finally { $img.Dispose() }
    exit 0
}

# a picture file of a format the web view cannot show (TIFF, HEIC…): Windows decodes it, the PNG goes back
if ($From) {
    $img = [System.Drawing.Image]::FromFile($From)
    try { $img.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png) } finally { $img.Dispose() }
    exit 0
}

# take the picture off the clipboard and save it as PNG
$img = $null
for ($i = 0; $i -lt 25 -and $null -eq $img; $i++) {
    try { if ([System.Windows.Forms.Clipboard]::ContainsImage()) { $img = [System.Windows.Forms.Clipboard]::GetImage() } } catch { }
    if ($null -eq $img) { Start-Sleep -Milliseconds 80 }
}
if ($null -eq $img) { exit 3 }
try { $img.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png) } finally { $img.Dispose() }
"""

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CF_HDROP = 15

# a picture file copied in Explorer: the web view shows these itself, so the bytes go through untouched
RAW_IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
                  ".bmp": "image/bmp", ".gif": "image/gif", ".avif": "image/avif"}
# ...and these Windows can decode but the page cannot show, so they are converted to PNG first
CONVERT_IMAGE_EXT = {".tif", ".tiff", ".heic", ".heif", ".jxl", ".dib", ".jp2", ".j2k", ".ico", ".wdp", ".hdp"}
IMAGE_EXT = set(RAW_IMAGE_MIME) | CONVERT_IMAGE_EXT


def _clipboard_png_format():
    """The clipboard format literally called "PNG", if some program has registered it."""
    try:
        u = ctypes.windll.user32
        u.RegisterClipboardFormatW.restype = ctypes.c_uint
        return u.RegisterClipboardFormatW("PNG") or 0
    except Exception:
        return 0


def _clipboard_files():
    """Paths of the files copied in Explorer/Files (CF_HDROP); no file contents are read here."""
    try:
        u, shell = ctypes.windll.user32, ctypes.windll.shell32
        u.GetClipboardData.restype = ctypes.c_void_p
        shell.DragQueryFileW.restype = ctypes.c_uint
        shell.DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
        if not u.IsClipboardFormatAvailable(CF_HDROP) or not u.OpenClipboard(0):
            return []
        try:
            drop = u.GetClipboardData(CF_HDROP)
            if not drop:
                return []
            paths = []
            for i in range(min(shell.DragQueryFileW(drop, 0xFFFFFFFF, None, 0), 16)):
                length = shell.DragQueryFileW(drop, i, None, 0)
                buf = ctypes.create_unicode_buffer(length + 1)
                if shell.DragQueryFileW(drop, i, buf, length + 1):
                    paths.append(buf.value)
            return paths
        finally:
            u.CloseClipboard()
    except Exception:
        return []


def clipboard_has_picture():
    """Is a picture on the clipboard? Cheap: asks Windows about the formats and file names only, no conversion."""
    try:
        u = ctypes.windll.user32
        formats = [2, 8, 17]  # CF_BITMAP, CF_DIB, CF_DIBV5
        png = _clipboard_png_format()
        if png:
            formats.append(png)
        if any(u.IsClipboardFormatAvailable(f) for f in formats):
            return True
    except Exception:
        return False
    return any(Path(p).suffix.lower() in IMAGE_EXT for p in _clipboard_files())


def _clipboard_ready_png():
    """PNG bytes already on the clipboard (some programs put a finished PNG there), else None."""
    fmt = _clipboard_png_format()
    if not fmt:
        return None
    try:
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        u.GetClipboardData.restype = ctypes.c_void_p
        k.GlobalLock.restype = ctypes.c_void_p
        k.GlobalLock.argtypes = [ctypes.c_void_p]
        k.GlobalUnlock.argtypes = [ctypes.c_void_p]
        k.GlobalSize.restype = ctypes.c_size_t
        k.GlobalSize.argtypes = [ctypes.c_void_p]
        if not u.IsClipboardFormatAvailable(fmt) or not u.OpenClipboard(0):
            return None
        try:
            handle = u.GetClipboardData(fmt)
            if not handle:
                return None
            pointer = k.GlobalLock(handle)
            if not pointer:
                return None
            try:
                size = k.GlobalSize(handle)
                data = ctypes.string_at(pointer, size) if size > len(PNG_SIGNATURE) else b""
            finally:
                k.GlobalUnlock(handle)
        finally:
            u.CloseClipboard()
    except Exception:
        return None
    start = data.find(PNG_SIGNATURE)  # a raw PNG starts with it; a .NET DataObject puts a small header before
    return data[start:] if start >= 0 else None


def _clipboard_helper():
    """The .NET helper script, written once next to the app's other files."""
    path = DATA_DIR / "tools" / "clipboard.ps1"
    try:
        current = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    except OSError:
        current = ""
    if current != CLIP_HELPER:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(CLIP_HELPER, encoding="utf-8-sig")
    return path


def _clipboard_run(*args):
    exe = shutil.which("powershell") or str(Path(os.environ.get("SystemRoot", r"C:\Windows")) /
                                           "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    return subprocess.run([exe, "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", str(_clipboard_helper()), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180, creationflags=engine.CREATE_NO_WINDOW)


def clipboard_picture():
    """(bytes, mime) of the picture on the clipboard, or None when there is no picture."""
    ready = _clipboard_ready_png()  # a finished PNG another program put there: the bytes go through as they are
    if ready:
        return ready, "image/png"
    dropped = [Path(p) for p in _clipboard_files()]
    for file in dropped:  # a picture file copied in Explorer
        mime = RAW_IMAGE_MIME.get(file.suffix.lower())
        if mime:
            try:
                return file.read_bytes(), mime
            except OSError:
                continue
    out = DATA_DIR / "tmp" / f"clip-{uuid.uuid4().hex}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        for file in dropped:  # TIFF, HEIC…: Windows decodes them, the page gets a PNG
            if file.suffix.lower() in CONVERT_IMAGE_EXT and file.exists():
                _clipboard_run("-Out", str(out), "-From", str(file))
                break
        else:  # a bare bitmap on the clipboard (a screenshot, a copy inside a viewer)
            _clipboard_run("-Out", str(out))
        return (out.read_bytes(), "image/png") if out.exists() and out.stat().st_size > len(PNG_SIGNATURE) else None
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        out.unlink(missing_ok=True)


def clipboard_set_picture(data):
    """Copy PNG bytes to the clipboard so any other program can paste them."""
    path = DATA_DIR / "tmp" / f"clip-{uuid.uuid4().hex}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    try:
        return _clipboard_run("-In", str(path)).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        path.unlink(missing_ok=True)


class JobStore(dict):
    """prompt_id -> job; every change is written to disk so a closed program or a power cut does not lose the queue."""

    def __init__(self, on_change):
        super().__init__()
        self.on_change = on_change

    def __setitem__(self, k, v):
        super().__setitem__(k, v)
        self.on_change()

    def pop(self, k, *a):
        r = super().pop(k, *a)
        self.on_change()
        return r


def media_input(role, kind, comfy=None, item=None, url=None, name=None, **extra):
    """One file a job is made from: what it is, what kind it is, and where the window can show it from.

    `comfy` is a path in ComfyUI's input folder ("h3studio/h3s_ab12.png"), `item` a record of the
    Library, `url` a ready address (a file an agent brought). The details window needs only the role,
    the kind and the URL; the rest is a bonus.
    """
    d = {"role": role, "kind": kind, **extra}
    if comfy:
        path = str(comfy).replace("\\", "/")
        d["url"] = "/api/input?name=" + urllib.parse.quote(path)
        d["file"] = Path(path).name
    elif item:
        d["url"] = f"/api/file/{item['id']}"
        f = item.get("file") or {}
        if f.get("filename"):
            d["file"] = Path(f["filename"]).name
        for k in ("res", "len"):
            if item.get(k):
                d[k] = item[k]
    elif url:
        d["url"] = url
    if name:
        d["name"] = name
    return d


def round_size(form, step=32, low=256, high=2048, max_px=2200000):
    """The user's own width/height from the form, rounded to the model's step (None when not given)."""
    w, h = str(form.get("width", "")), str(form.get("height", ""))
    if not (w.isdigit() and h.isdigit()):
        return None
    w, h = int(w), int(h)
    if min(w, h) < 64:
        return None
    k = (max_px / (w * h)) ** 0.5 if w * h > max_px else 1.0
    r = lambda v: min(high, max(low, round(v * k / step) * step))
    return r(w), r(h)


SUPPORT_URL = "https://pay.cloudtips.ru/p/0e4d4824"
# Immers.Cloud: the author's referral link goes here (a discount for the new user, a bonus for the author). Empty: the plain site is opened.
RENT_REFERRAL = "https://immers.cloud/signup/r/20240927-7463913-939/"
RENT_SITE = "https://immers.cloud/"
import workflows as wf

# frozen builds keep code inside the bundle but user data next to the exe
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
HOME = Path(sys.executable).parent if getattr(sys, "frozen", False) else ROOT
UI_DIR = ROOT / "ui"
DATA_DIR = Path(os.environ.get("H3STUDIO_DATA", HOME / "data"))
TOOLS_DIR = Path(os.environ.get("H3STUDIO_TOOLS") or HOME / "tools")  # portable FFmpeg and other helpers
LONG_DIR = DATA_DIR / "long"   # saved scene states of the long video made scene by scene
LIBRARY_FILE = DATA_DIR / "library.json"    # read once, by the import; the database is the truth now
QUEUE_FILE = DATA_DIR / "queue.json"        # the same: imported once, then kept as the last snapshot
STATE_DB = DATA_DIR / "h3studio.db"
WORKFLOWS_DIR = DATA_DIR / "workflows"
PREVIEW_LIMIT = 40  # finished pictures waiting for «Добавить в библиотеку»: the newest are kept, the rest is forgotten

# The shutdown has one budget, and it is split with main.py on purpose: main.py waits CLEANUP_TIMEOUT
# (45 s) for this whole cleanup, aiohttp drains the running handlers for HANDLER_DRAIN of it, and
# App.stop gets the rest — the state-safe part (loops, writers, queue) must always fit, so the outer
# timeout can never cut a write. Stopping the engine comes after the queue is saved and has its own
# allowance: 15 + 8 + 20 < 45, and a process that is cut there is adopted by a job object anyway.
HANDLER_DRAIN = 15.0
SHUTDOWN_BUDGET = 8.0
ENGINE_STOP_TIMEOUT = 20.0
CLOUD_RETRY = 30.0   # a network blink does not make a remote job failed: five misses before giving up

CLIENT_ID = "h3studio-" + uuid.uuid4().hex[:8]

# a failed write of the stored state: the JSON files of the older version, or the database now
STORE_ERRORS = (OSError, sqlite3.Error)

# ComfyUI binary websocket event types
BIN_PREVIEW = 1
BIN_PREVIEW_META = 4

# ---------- who is allowed to talk to the local server ----------
# The server listens on 127.0.0.1, but any page in any browser on this computer can still reach it:
# a form-encoded POST is a "simple" request that needs no CORS preflight, and a WebSocket is not
# covered by the same-origin policy at all. Without a check, a random site could queue jobs, wipe
# the library or drive the agent. So every state-changing request (and the event socket) must come
# from our own page: the same local host and the very port this process listens on. Requests with no
# Origin header (curl, the MCP server, other programs) are allowed - they are not a web page acting
# on behalf of somebody else.

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def origin_ok(origin, port):
    """True when `origin` is our own page (or there is no origin at all)."""
    if not origin:
        return True                      # not a browser, or a navigation without Origin
    if origin == "null":
        return False                     # sandboxed iframe / file:// context: never ours
    try:
        parts = urllib.parse.urlsplit(origin)
        if parts.scheme not in ("http", "https"):
            return False
        origin_port = parts.port or (80 if parts.scheme == "http" else 443)
    except ValueError:
        return False                     # malformed Origin: reject, do not raise
    host = (parts.hostname or "").lower()
    if host == "::1":
        host = "127.0.0.1"
    return host in LOCAL_HOSTS and origin_port == port


@web.middleware
async def same_origin_only(request, handler):
    """Reject state-changing requests and WebSocket upgrades that come from another site."""
    upgrade = (request.headers.get("Upgrade") or "").lower()
    if request.method not in SAFE_METHODS or upgrade == "websocket":
        if not origin_ok(request.headers.get("Origin"), request.url.port):
            return web.json_response({"error": "cross-origin request rejected"}, status=403)
    return await handler(request)


def safe_lora_name(name):
    """A LoRA file name as ComfyUI lists it: no folders, no drive letters, no parent references."""
    name = str(name or "").strip().replace("/", "\\")
    if not name or name.startswith("\\") or ":" in name or ".." in name:
        return ""
    return name


def open_in_explorer(path):
    """Show a folder (or a file) in Explorer; quietly does nothing when the system refuses."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(path)], creationflags=0x08000000)
        else:
            webbrowser.open(Path(path).as_uri())
    except Exception:
        pass


class Library:
    """The Library list. The database is the truth; `items` is the list the window is shown.

    The JSON file of the older versions is read once, by the import (see `storage.migrations`), and
    is never written again: a second source of truth would drift from this one on the first failure.
    """

    def __init__(self, db):
        self.db = db
        self.repo = LibraryRepository(db)
        self.items = self.repo.load()

    def save(self, item=None):
        """Write one changed item back (a favourite flag, the program's own copy), or the whole list."""
        if item is not None:
            self.repo.add(item)   # an existing id is updated in place: its place in the list is kept
            return
        for it in self.items:
            self.repo.add(it)

    def get(self, item_id):
        return next((x for x in self.items if x["id"] == item_id), None)

    def add(self, item):
        self.repo.add(item)   # raises if the row cannot be written: memory is not touched then
        self.items.insert(0, item)

    def note_committed(self, item):
        """The row was already written inside someone else's transaction: only the window's list changes.

        `MediaService.finalize` writes the row together with the job's `succeeded`, so the item must
        not enter the list before that transaction commits, and the same id must not appear twice.
        """
        if self.get(item["id"]) is None:
            self.items.insert(0, item)

    def remove(self, item_id):
        self.repo.delete(item_id)
        self.items = [x for x in self.items if x["id"] != item_id]


class App:
    def __init__(self, comfy_override=None, port=None):
        self.config = engine.Config(DATA_DIR / "config.json")
        # a share or a synchronised folder is a poor home for the queue, the library and (stage 3) the
        # database: the window says so once instead of letting the user find out the hard way
        self.data_dir_warning = datadir.check_location(DATA_DIR)
        self.comfy_override = comfy_override.rstrip("/") if comfy_override else None
        self.port = port  # the port this window actually listens on: agents find it through agent.json
        self.agents = agentapi.AgentAPI(self, DATA_DIR, MAX_SECONDS)
        self.recent = {}  # prompt_id -> how a job ended, for the short while after it left the queue
        self.history = {}  # prompt_id -> what a failed or stopped job was made from (the details window asks later)
        self.session = None
        self.browsers = set()
        # the state of this folder: the database is opened once, and the older JSON files are imported
        # into it on the first start of this version (see storage.migrations for what and how)
        self.db = Database(STATE_DB)
        self.migration = None
        self.migration_error = None
        try:
            self.migration = migrate(self.db, DATA_DIR)
        except Exception as e:  # noqa: BLE001 - a broken database must not hide the window behind a traceback
            self.migration_error = f"{type(e).__name__}: {e}"
        self.jobs_repo = JobRepository(self.db)
        self.media_repo = MediaRepository(self.db)
        self.results_repo = ResultRepository(self.db)
        self.queue_save_error = None   # the last failure to persist the queue, shown in the window instead of a silent loss
        self.pending_resume = self.load_resume_queue()  # unfinished jobs of an earlier run (asked about at start)
        self.jobs = JobStore(self.save_queue)  # prompt_id -> job dict
        self.library = Library(self.db)
        # finished pictures of the picture sections that the user has not added to the Library yet:
        # they are served and saved to disk like Library items, but stay out of the Library list
        self.pending = {}  # item id -> item; memory only, so a restart forgets them
        self.output_dir = None
        self.comfy_online = False
        self.installer = None
        self.setup_log = []  # last setup events, replayed to a reopened window
        self.model_names = {}  # role -> name as ComfyUI lists it
        self.missing = []  # catalog entries ComfyUI does not see
        self.found = set()  # roles whose file this ComfyUI does see
        self.needed = []  # roles the chosen modes need (plus the optional extras of «Модели»)
        self.skills = ag.Skills(ROOT / "skills", DATA_DIR / "skills", self.config)
        self.llm = ag.LlamaServer(self.llm_root(), DATA_DIR / "llama.log")
        self.agent_installer = None
        self.hw = None
        self.rh_models = None  # model paths found in the user's RunningHub workflow
        self.monitor = monitor.Monitor()
        self.recent_errors = []  # last problems, for the report to the author
        # every background coroutine of the app: one place that keeps the reference, sees the error
        # and can stop it (a `create_task` in a handler is fire and forget)
        self.tasks = TaskSupervisor(note_error=self.note_error)
        self._stopping = False   # set first in the shutdown: no new work, no new tasks
        self.last_shutdown = None  # what the shutdown could not finish in its budget, for the report
        self.guard = watchdog.GpuWatchdog()  # the video card watch: slow render or hung engine
        self.guard_task = None
        self.watchdog_task = None  # the lost-job watch: a job ComfyUI no longer knows about
        self.guard_gpu = None    # the last reading the watch took
        self.stats = None        # last /system_stats: it carries the command line of the user's own ComfyUI
        self.ext_log = DATA_DIR / "comfyui-external.log"
        # who owns ComfyUI: the process, the one restart that may run, and the VRAM handshake with
        # the agent's model. Two restarts at once never start two engines.
        self.engines = EngineManager(
            url=lambda: self.comfy, wait_ready=self.wait_comfy, log_path=self.ext_log,
            external_restart=engine.restart_external, stats=lambda: self.stats,
            stop_llm=self.stop_llm, release_vram=self.release_vram, busy=lambda: bool(self.jobs),
            note_error=self.note_error, clock=time.time)
        self.ffmpeg_dl = None    # the running portable-FFmpeg download: {"cancel": Event, "pct": 0, "text": "", "done": False}
        self.ffmpeg_jobs = {}    # prompt_id -> {"cancel": Event, "task": Task, "dest": Path}
        self.lora_dl = None      # the running LoRA download from Civitai / Hugging Face
        # one submission path for the jobs that have a spec: the service owns the durable submit, the
        # idempotency keys and the state rules; the backend talks to the engine (built last: it reads
        # the engine address, which may point at a process the app manages)
        self.idempotency = IdempotencyRepository(self.db)
        self.service = JobService(self.jobs_repo, self.build_backend(), keys=self.idempotency)
        # the results of the jobs: one finalization for every path that collects them, streaming
        # copies into the results folder, and the drafts that wait for «Добавить в библиотеку»
        self.media = MediaService(
            db=self.db, jobs=self.jobs_repo, media=self.media_repo, results=self.results_repo,
            library=self.library, source=self.service.backend, outputs_of=self.outputs_for_media,
            output_dir=lambda: self.output_dir, results_dir=self.results_dir,
            keep_copies=self.keep_copies, present=self.public, hold_draft=self.hold_preview,
            note_error=self.note_error, on_outputs=self.keep_scene_state,
            extra_folders=lambda: [DATA_DIR / "cloud", DATA_DIR / "cache"])

    async def outputs_for_media(self, pid):
        """The engine's outputs, asked through the App every time: the media service must not keep a
        stale copy of `job_outputs` (the engine may be replaced, and the watch polls it)."""
        return await self.job_outputs(pid)

    @contextlib.asynccontextmanager
    async def view_stream(self, path, params):
        """One output file of the engine as a stream: status, declared size and chunks, no whole read.

        The media service owns the destination and the `.part`; this only lets the backend ask for
        the file without knowing how it is stored.
        """
        async with self.session.get(self.comfy + path, params=params) as r:
            yield r.status, r.content_length, r.content.iter_chunked(1 << 20)

    def llm_root(self):
        if self.config.get("llm_dir"):
            return Path(self.config.get("llm_dir"))
        if self.config.get("engine") == "managed" and self.config.get("install_dir"):
            return Path(self.config.get("install_dir")) / "llm"
        return DATA_DIR.parent / "llm"

    async def llm_variant(self):
        if not self.llm.alive():
            self.llm.root = self.llm_root()  # follows the install folder chosen in setup
            self.llm.custom = self.config.get("llm_custom")
        if self.config.get("llm_variant") in ag.LLM_VARIANTS:
            return self.config.get("llm_variant")
        if self.hw is None:
            self.hw = await asyncio.to_thread(engine.detect_hardware)
        return ag.pick_variant(self.hw)

    async def stop_llm(self):
        if self.llm.alive():
            await asyncio.to_thread(self.llm.stop)

    @property
    def comfy(self):
        if self.comfy_override:
            return self.comfy_override
        if self.engines.process:
            return self.engines.process.url
        return self.config.get("comfy_url", "http://127.0.0.1:8188").rstrip("/")

    def comfy_root(self):
        d = self.config.get("install_dir")
        return Path(d) / catalog.COMFY_DIRNAME if d else None

    def choice(self):
        return {**catalog.DEFAULT_CHOICE, **(self.config.get("choice") or {}), **(self.config.get("choice_override") or {})}

    def modes(self):
        modes = list(self.config.get("modes") or catalog.MODE_ROLES)
        if "actor" not in modes and ("refs" in modes or "lipsync" in modes):
            modes.append("actor")  # same models, so set-ups made before this mode existed get it too
        if "swap" not in modes and "refs" in modes:
            modes.append("swap")   # character-swap preset: References models + a 0.16 GB LoRA, fetched by "Докачать"
        return modes + [m for m in getattr(self, "auto_modes", ()) if m not in modes]

    # ---------- ComfyUI connection ----------

    def apply_mirror(self):
        engine.HF_MIRROR = "hf-mirror.com" if self.config.get("hf_mirror") else ""

    async def h_mirror(self, req):
        on = bool((await req.json()).get("on"))
        self.config.update(hf_mirror=on)
        self.apply_mirror()
        return web.json_response({"ok": True, "on": on})

    def awake_loop(self):
        """While generations are queued or running (and for ten minutes after the last one), Windows is not allowed to go to sleep:
        a long video made scene after scene overnight must not be stopped by the power plan. The screen may still switch off."""
        import ctypes
        import time
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        last_busy = 0.0
        while True:
            if self.jobs:
                last_busy = time.time()
            keep = time.time() - last_busy < 600
            try:
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if keep else 0))
            except Exception:
                return
            time.sleep(15)

    async def start(self, _app):
        if sys.platform == "win32":
            import threading
            threading.Thread(target=self.awake_loop, daemon=True, name="keep-awake").start()
        self.apply_mirror()
        trace = aiohttp.TraceConfig()

        async def _comfy_key(_s, _c, params):
            # key of a rented server: sent only to the ComfyUI address, never to other sites
            key = self.config.get("comfy_key")
            if key and params.url.host and params.url.host == urllib.parse.urlparse(self.comfy).hostname:
                params.headers["X-API-Key"] = key
                params.headers["Authorization"] = f"Bearer {key}"
        trace.on_request_start.append(_comfy_key)
        self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_ssl_context()), trace_configs=[trace])
        asyncio.get_running_loop().run_in_executor(None, self.prune_input_cache)  # the details window copies files it shows
        try:   # `.part` files of a closed run: never a file an active writer owns, never a user's file
            self.media.cleanup()
        except Exception as e:  # noqa: BLE001 - an untidy folder must not stop the program
            self.note_error(f"Уборка недописанных файлов не удалась: {e}")
        if self.config.get("engine") == "managed" and self.config.get("setup_done") and not self.comfy_override:
            if self.missing_node_packs():
                self.packs_task = self.tasks.spawn("install-nodes", self.install_missing_packs())
            else:
                self.start_managed()
        self.ws_task = self.tasks.spawn("comfy-ws", self.comfy_ws_loop())
        self.guard_task = self.tasks.spawn("gpu-watch", self.guard_loop())
        self.watchdog_task = self.tasks.spawn("lost-watch", self.jobs_watchdog())  # jobs ComfyUI has lost, not a hung card
        self.watch_cloud_jobs()   # a remote job survives the window: watch it, never send it again
        self.agents.publish()

    def prune_input_cache(self, days=30):
        """Drop the copies of job files the details window made: they are only a cache, ComfyUI keeps the originals."""
        folder = DATA_DIR / "cache" / "input"
        if not folder.is_dir():
            return
        edge = datetime.now().timestamp() - days * 86400
        for f in folder.glob("*"):
            try:
                if f.is_file() and f.stat().st_mtime < edge:
                    f.unlink()
            except OSError:
                pass

    def missing_node_packs(self):
        """Own ComfyUI: node packs from the catalog that are not in its custom_nodes yet (a newer app version may need more)."""
        root = self.comfy_root()
        if not root:
            return []
        nodes_dir = root / "ComfyUI" / "custom_nodes"
        if not nodes_dir.is_dir():
            return []
        return [p["name"] for p in catalog.NODE_PACKS if not (nodes_dir / p["name"]).exists()]

    async def install_missing_packs(self):
        """Put the missing node packs in place, then start ComfyUI. Runs in the background so the app opens at once;
        ComfyUI is started even when the download fails (no internet), only the new sections stay unavailable."""
        async def quiet(_ev):
            pass
        try:
            await engine.Installer(ROOT, self.session, quiet).install_nodes(self.comfy_root())
        except Exception as e:
            print(f"Не удалось докачать ноды ({type(e).__name__}): {e}", file=sys.stderr)
        if not self.engines.process:
            self.start_managed()

    async def stop(self, _app):
        """The one shutdown, in the order that keeps the recovery state safe.

        1. stop accepting new work and new background tasks;
        2. tell the installers and downloads to stop (their own cancel flags);
        3. cancel the loops (the engine web socket, the watches, the cloud polls) and wait for them;
        4. wait for the media writers, so a `.part` is never deleted under a live writer;
        5. write the recovery state — before anything is closed, so a budget cut cannot lose the
           queue: a job that was being finalized is already `finalizing` in the database, and the
           next start takes it from the engine's history instead of rendering it again;
        6. close the HTTP session, then the database;
        7. stop the processes this program started (the managed ComfyUI), last: it is the slow part
           and it owns no state that a cut would lose.

        A remote cloud job is *not* cancelled here: it keeps running on RunningHub, and the next start
        watches it again (`watch_cloud_jobs`).
        """
        if self._stopping:
            return
        self._stopping = True
        started = time.monotonic()

        def left(reserve=0.0):
            return max(0.5, SHUTDOWN_BUDGET - (time.monotonic() - started) - reserve)

        self.agents.unpublish()
        self.tasks.close()   # from this moment a handler gets None instead of a task on a closed session
        for installer in (self.installer, getattr(self, "dl_installer", None), self.agent_installer):
            if installer is not None:
                installer.cancelled = True
        if self.ffmpeg_dl:
            self.ffmpeg_dl["cancel"].set()
        for job in self.ffmpeg_jobs.values():
            job["cancel"].set()
        unfinished = await self.tasks.shutdown(timeout=left(4.0))
        writers = await self.media.shutdown(timeout=left())
        self.save_queue()   # the recovery state, while the database is still open
        if self.session is not None and not self.session.closed:
            await self.session.close()
        try:
            self.db.close()
        except Exception:  # noqa: BLE001 - a database that is already closed is not a shutdown failure
            pass
        try:
            await asyncio.wait_for(self.engines.stop(), timeout=ENGINE_STOP_TIMEOUT)
        except asyncio.TimeoutError:
            unfinished.append("engine-stop")   # the adopted process dies with the program, no state is lost
        await asyncio.to_thread(self.llm.stop)
        self.last_shutdown = {"seconds": round(time.monotonic() - started, 2), "unfinished": unfinished,
                              "writers": bool(writers)}
        if unfinished:
            self.note_error("При закрытии не успели: " + ", ".join(unfinished)
                            + ". Состояние сохранено, следующий запуск продолжит.")

    async def stop_aware_thread(self, func, *args, cancel=None):
        """Run a worker that answers a cancel event; a cancelled caller waits for the worker to stop.

        `asyncio.to_thread` cannot be interrupted: cancelling the task leaves FFmpeg writing while
        the cleanup removes its output. So the event is set and the worker is awaited — it checks the
        event and exits itself — before the caller is told that the work was cancelled.
        """
        worker = asyncio.ensure_future(asyncio.to_thread(func, *args))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            if cancel is not None:
                cancel.set()
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(asyncio.shield(worker), timeout=15.0)
            raise

    def start_managed(self):
        root = self.comfy_root()
        self.engines.start_managed(root)
        self.output_dir = root / "ComfyUI" / "output"

    async def wait_comfy(self, timeout=300):
        """Wait until ComfyUI answers; raise with its log if the process died."""
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            process = self.engines.process
            external = self.engines.external
            died = (process is not None and not process.alive()) or (
                process is None and external is not None and external.poll() is not None)
            if died:
                tail = self.engines.log_tail(20)
                hint = ("\n\nПохоже, проблема в драйвере видеокарты. Обновите драйвер NVIDIA (nvidia.com/drivers), "
                        "перезагрузите компьютер и нажмите «Продолжить».") if re.search(r"cuda|nvidia|driver", tail, re.I) else ""
                raise RuntimeError("ComfyUI не запустился:\n" + tail + hint)
            try:
                await self.system_stats()
                return
            except Exception:
                await asyncio.sleep(2)
        raise RuntimeError("ComfyUI долго не отвечает:\n" + self.engines.log_tail(20))

    async def resolve_models(self):
        """Match the chosen model files against what ComfyUI sees (any subfolder)."""
        visible = {}
        for cat in {v["category"] for variants in catalog.VARIANTS.values() for v in variants.values()}:
            try:
                _, lst = await self.comfy_json("GET", f"/models/{cat}")
            except Exception:
                lst = []
            visible[cat] = lst or []
        self.visible = {cat: {n.replace("/", "\\").split("\\")[-1] for n in lst} for cat, lst in visible.items()}
        names, found, entries = {}, set(), {}
        for role, variant in self.choice().items():
            entry = entries[role] = catalog.model_entry(role, variant)
            picked = (self.config.get("model_pick") or {}).get(role)  # a file the user chose by hand
            def seen(file):
                ok = {file, *catalog.FILE_ALIASES.get(file, ())}  # same file under another name (e.g. upscaler from Hugging Face)
                return next((n for n in visible[entry["category"]] if n.replace("/", "\\").split("\\")[-1] in ok), None)
            others = {v["file"] for r, vs in catalog.VARIANTS.items() if r != role for v in vs.values()}
            if picked and picked.replace("/", "\\").split("\\")[-1] in others:  # the file of ANOTHER model of the app: never feed it to this role
                picked = None
            hit = picked if picked in visible[entry["category"]] else seen(entry["file"])
            if not hit and role not in (self.config.get("choice_override") or {}):  # another variant the user already has (v1.3 instead of v2); never for a version picked by hand
                hit = next(filter(None, (seen(v["file"]) for v in catalog.VARIANTS[role].values())), None)
            names[role] = hit or entry["name"]
            if hit:
                found.add(role)
        # modes added after a set-up was made: with your own ComfyUI they switch on by themselves when the files are there
        self.auto_modes = {m for m in ("krea", "kreareal", "qwen", "flux", "upscale", "long") if self.config.get("engine") == "external"
                           and all(r in found for r in catalog.MODE_ROLES[m])}
        needed = catalog.roles_for(self.modes())
        needed += [r for r in catalog.EXTRA_ROLES if r not in needed]  # optional models: listed, downloaded on demand
        missing = [entries[r] for r in needed if r in entries and r not in found]
        self.model_names, self.missing, self.found, self.needed = names, missing, found, needed
        return names, missing

    async def comfy_json(self, method, path, **kw):
        async with self.session.request(method, self.comfy + path, **kw) as r:
            body = await r.text()
            data = json.loads(body) if body else None
            return r.status, data

    async def system_stats(self):
        try:
            status, data = await self.comfy_json("GET", "/system_stats", timeout=aiohttp.ClientTimeout(total=8))
            if status != 200 or not isinstance(data, dict) or "system" not in data:
                raise ValueError("no stats")
        except Exception:
            # some builds hide or slow down /system_stats; a queue answer still means ComfyUI is alive
            status, _ = await self.comfy_json("GET", "/queue", timeout=aiohttp.ClientTimeout(total=8))
            if status != 200:
                raise
            data = {"system": {}, "devices": []}
        argv = data["system"].get("argv", [])
        if "--output-directory" in argv:
            self.output_dir = Path(argv[argv.index("--output-directory") + 1])
        self.stats = data  # the command line in it is how the user's own ComfyUI can be started again
        return data

    async def reconcile_jobs(self):
        """After a reconnect: a job marked running may have finished while the app was not listening.
        A short websocket hiccup used to leave such a job stuck at its last percentage forever, with the
        finished clip never reaching the Library, so on every reconnect we ask ComfyUI what really happened."""
        if not self.jobs:
            return
        try:
            _, q = await self.comfy_json("GET", "/queue", timeout=aiohttp.ClientTimeout(total=10))
            alive = {str(x[1]) for key in ("queue_running", "queue_pending") for x in (q or {}).get(key, []) if len(x) > 1}
        except Exception:
            return
        now = datetime.now().timestamp()
        for pid, job in list(self.jobs.items()):
            if job.get("task") or pid in alive:  # a cloud task, or ComfyUI still has it
                continue
            if now - (job.get("t0") or 0) < 20:  # just submitted: it may not be in /queue yet
                continue
            try:
                _, hist = await self.comfy_json("GET", f"/history/{pid}")
            except Exception:
                continue
            entry = (hist or {}).get(pid)
            try:
                if not entry:
                    self.keep_meta(job)
                    self.jobs.pop(pid, None)
                    await self.broadcast({"type": "error", "prompt_id": pid, "message": (
                        "Задание пропало из очереди ComfyUI: связь с ним прерывалась. Запустите ещё раз.")})
                elif entry.get("status", {}).get("status_str") == "error":
                    self.keep_meta(job)
                    self.jobs.pop(pid, None)
                    await self.broadcast({"type": "error", "prompt_id": pid,
                                          "message": "ComfyUI закончил задание с ошибкой (связь с ним прерывалась)"})
                else:
                    await self.finish_job(job)  # it is done: take the result into the Library as usual
            except Exception as e:
                self.note_error(f"Не удалось восстановить задание после обрыва связи: {e}")

    async def comfy_ws_loop(self):
        while True:
            if not self.config.get("setup_done") and not self.comfy_override:
                await asyncio.sleep(2)
                continue
            ws_url = self.comfy.replace("http", "ws", 1) + f"/ws?clientId={CLIENT_ID}"
            try:
                async with self.session.ws_connect(ws_url, max_msg_size=0, heartbeat=20) as ws:
                    self.comfy_online = True
                    await self.resolve_models()
                    await self.reconcile_jobs()
                    await self.broadcast({"type": "engine", "online": True})
                    await self.warn_weak_gpu()
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self.on_comfy_event(json.loads(msg.data))
                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            await self.on_comfy_binary(msg.data)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            if self.comfy_online:
                self.comfy_online = False
                await self.broadcast({"type": "engine", "online": False})
            await asyncio.sleep(2)

    async def warn_weak_gpu(self):
        """The 'max' profile (full ~32 GB Singularity) is meant for 24 GB cards. Say so once per run on a weaker card."""
        if getattr(self, "_weak_warned", False) or self.config.get("choice", {}).get("lipsync_unet") != "full":
            return
        try:
            if self.hw is None:
                self.hw = await asyncio.to_thread(engine.detect_hardware)
            vram = self.hw.get("vram_gb") or 0
        except Exception:
            return
        if 0 < vram < 24:
            self._weak_warned = True
            text = (f"Выбрана самая тяжёлая модель (профиль «Максимум»), а у видеокарты {vram:g} ГБ памяти: "
                    "генерация может зависать на первом шаге. Откройте «Настройки», выберите для Lipsync/Референсов "
                    "облегчённую версию (pruned) и скачайте её.")
            self.note_error(text)
            await self.broadcast({"type": "notice", "text": text})

    # ---------- the video card watch ----------
    #
    # A percentage that does not move looks the same whether a step is heavy or ComfyUI is gone. The card
    # tells them apart (see watchdog.py), so while a job runs the app looks at it every ten seconds and
    # replaces an engine that has stopped working. Everything it decides goes to watchdog.log, and the
    # sidelist shows "нет шагов N мин" before anything is touched.

    def watchdog_on(self):
        return self.config.get("gpu_watchdog") is not False

    def watchdog_local(self):
        """A rented or otherwise remote ComfyUI cannot be started again from here, and its card is not ours."""
        return self.engines.local()

    def running_jobs(self):
        return [j for j in self.jobs.values() if j.get("state") == "running" and not j.get("task")]

    async def guard_loop(self):
        while True:
            await asyncio.sleep(watchdog.PERIOD)
            try:
                await self.guard_tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # the watch must never take the app down with it
                self.note_error(f"Сторож видеокарты: {type(e).__name__}: {e}")

    async def guard_tick(self):
        """One look at the card. Cheap when nothing runs: nvidia-smi is asked only while a job is going."""
        if self.engines.restarting:  # the engine is being replaced right now: there is nothing to judge yet
            return
        if not self.watchdog_on() or not self.watchdog_local() or not self.running_jobs():
            self.guard.paused()
            return
        gpu = await asyncio.to_thread(self.monitor.gpu)
        self.guard_gpu = gpu
        verdict = self.guard.observe(gpu, busy=True, online=self.comfy_online)
        if verdict:
            await self.on_stuck(verdict)

    async def on_stuck(self, verdict):
        """The engine looks hung: say why, restart it, and tell the user what that costs."""
        line = watchdog.describe(verdict)
        self.guard_log(line)
        self.note_error("Сторож видеокарты: " + line)
        await self.broadcast({"type": "notice", "text": watchdog.verdict_text(verdict)})
        ok, why = await self.restart_engine()
        if ok:
            await self.drop_engine_jobs("Задание прервано: движок завис, ComfyUI перезапущен")
            self.guard.reset()
            return
        text = (f"Перезапустить ComfyUI не получилось: {why}. Закройте окно ComfyUI и запустите его снова. "
                "Если это повторяется, перезагрузите компьютер и обновите драйвер NVIDIA.")
        self.note_error(text)
        await self.broadcast({"type": "notice", "text": text})

    async def restart_engine(self):
        """Start ComfyUI again. The engine manager owns the work and its one-restart-at-a-time rule:
        callers that arrive while it runs wait for it and receive the same answer, so a watch, a
        cancel timeout and the button never start two engines."""
        return await self.engines.restart()

    async def drop_engine_jobs(self, message=None, kind="error"):
        """The engine was replaced, so its queue is gone: nothing of what it had will finish now."""
        for jid in [j for j, job in self.jobs.items() if not job.get("task") and not job.get("finalizing")]:
            # a job that is being finalized has already finished in the engine: only its result is
            # being written, so a replaced engine is not a reason to declare it lost
            self.try_set_job_state(self.jobs[jid], JobState.CANCELED if kind == "interrupted" else JobState.FAILED,
                                   error=message)
            self.keep_meta(self.jobs.get(jid))
            self.jobs.pop(jid, None)
            await self.broadcast({"type": kind, "prompt_id": jid, **({"message": message} if message else {})})

    def guard_log(self, line):
        """One line about the card, for the report and for the developer. The window never reads it."""
        try:
            path = DATA_DIR / "watchdog.log"
            if path.exists() and path.stat().st_size > 400_000:
                path.replace(path.with_suffix(".old"))
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {line}\n")
        except OSError:
            pass

    def guard_log_tail(self, n=12):
        try:
            return "\n".join((DATA_DIR / "watchdog.log").read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
        except OSError:
            return ""

    def watch_state(self):
        """What the sidebar shows about the watch: nothing while all is well."""
        if not self.watchdog_on() or not self.watchdog_local():
            return {"state": "off", "text": ""}
        last = self.engines.last
        if last and last.get("at") and time.time() - last["at"] < 600:
            if last.get("ok"):
                when = time.strftime("%H:%M", time.localtime(last["at"]))
                return {"state": "warn", "text": f"перезапущен в {when}"}
            return {"state": "bad", "text": "перезапуск не удался"}
        if not self.running_jobs():
            return {"state": "ok", "text": ""}
        stall = self.guard.stall()
        if stall < 120:
            return {"state": "ok", "text": ""}
        g = self.guard_gpu or {}
        card = ""
        if g.get("util") is not None:
            card = f" · {round(g['util'])} %"
            if g.get("temp") is not None:
                card += f" · {round(g['temp'])} °C"
        return {"state": "warn", "text": f"нет шагов {int(stall // 60)} мин{card}"}

    async def h_watchdog_set(self, req):
        on = bool((await req.json()).get("on"))
        self.config.update(gpu_watchdog=on)
        if not on:
            self.guard.reset()
        self.guard_log("сторож выключен" if not on else "сторож включён")
        return web.json_response({"ok": True, "on": on})

    async def h_engine_restart(self, _req):
        """Settings → «Движок»: start ComfyUI again by hand."""
        if self.installer:
            return web.json_response({"error": "Сначала дождитесь установки"}, status=409)
        if self.engines.restarting:
            return web.json_response({"error": "Перезапуск уже идёт"}, status=409)
        self.guard_log("перезапуск по кнопке")
        ok, why = await self.restart_engine()
        if not ok:
            self.note_error(f"Не удалось перезапустить движок по кнопке: {why}")
            return web.json_response({"error": f"Не удалось перезапустить ComfyUI: {why}"}, status=500)
        await self.drop_engine_jobs("Задание остановлено: движок перезапущен вручную")
        return web.json_response({"ok": True, "url": self.comfy})

    async def on_comfy_binary(self, data):
        if len(data) < 8:
            return
        kind = struct.unpack(">I", data[:4])[0]
        if kind == BIN_PREVIEW:
            image = data[8:]
        elif kind == BIN_PREVIEW_META:
            meta_len = struct.unpack(">I", data[4:8])[0]
            meta = json.loads(data[8:8 + meta_len])
            if meta.get("prompt_id") not in self.jobs:
                return
            image = data[8 + meta_len:]
        else:
            return
        if not any(j["state"] == "running" for j in self.jobs.values()):
            return
        self.guard.note_progress()  # a live preview is the strongest sign of a working render
        for b in list(self.browsers):
            try:
                await b.send_bytes(image)
            except Exception:
                self.browsers.discard(b)

    async def on_comfy_event(self, msg):
        t, d = msg.get("type"), msg.get("data") or {}
        if t == "kj_preview_override":   # animated live preview of KJNodes (no prompt id in it: it belongs to the running job)
            run = next((j for j in self.jobs.values() if j["state"] == "running"), None)
            if run and d.get("image"):
                await self.broadcast({"type": "preview_anim", "prompt_id": run["prompt_id"], "image": d["image"],
                                      "mime": d.get("mime") or "image/jpeg", "step": d.get("step"), "total": d.get("total")})
            return
        job = self.jobs.get(d.get("prompt_id"))
        if not job:
            return
        pid = job["prompt_id"]
        self.guard.note_progress()  # any event of our job means ComfyUI is alive and working on it
        if t in ("execution_start", "executing", "progress"):
            job["last_ev"] = datetime.now().timestamp()  # the lost-job watch measures silence in wall-clock seconds
        if t == "execution_start":
            self.try_set_job_state(job, JobState.RUNNING)
            await self.send_stage(job, "load")
        elif t == "executing" and d.get("node"):
            stage = job["stages"].get(d["node"])
            if stage:
                await self.send_stage(job, stage, node=d["node"])
        elif t == "progress":
            stage = job["stages"].get(str(d.get("node")), job.get("stage"))
            if stage in ("sample", "sample2"):
                await self.send_stage(job, stage, d.get("value"), d.get("max"), node=str(d.get("node")))
        elif t == "execution_success":
            await self.finish_job(job)
        elif t == "execution_error":
            self.try_set_job_state(job, JobState.FAILED, error=str(d.get("exception_message", "")).strip())
            self.note_error(f'Ошибка генерации, узел {d.get("node_type", "")}: {d.get("exception_message", "").strip()}')
            hint = self.model_hint((job.get("meta") or {}).get("mode", ""), d.get("exception_message", ""))
            await self.broadcast({"type": "error", "prompt_id": pid,
                                  "message": f'{d.get("node_type", "")}: {d.get("exception_message", "").strip()}' + hint})
            self.keep_meta(job)
            self.jobs.pop(pid, None)
        elif t == "execution_interrupted":
            self.try_set_job_state(job, JobState.CANCELED)
            await self.broadcast({"type": "interrupted", "prompt_id": pid})
            self.keep_meta(job)
            self.jobs.pop(pid, None)

    async def send_stage(self, job, stage, step=None, total=None, node=None):
        job["stage"] = stage
        job["step"], job["step_max"] = step, total  # agents read the same numbers: "step 3 of 8"
        msg = {"type": "stage", "prompt_id": job["prompt_id"], "stage": stage, "step": step, "max": total}
        sc = job.get("scenes")
        if sc and node is not None and str(node) in sc.get("map", {}):
            job["scene_now"] = sc["map"][str(node)] + 1
        if sc and job.get("scene_now"):
            msg.update(scene=job["scene_now"], scenes=sc["n"])
        await self.broadcast(msg)

    async def job_outputs(self, pid, asks=4, delay=0.6):
        """What a finished job produced.

        ComfyUI announces «execution_success» before the history entry is written, so a single question
        right after the event can honestly answer "nothing yet" while the video is already on disk. That
        is how a finished render used to end up as «ComfyUI не вернул ни видео, ни изображение»: ask
        again for a few seconds before believing it.
        """
        for n in range(asks):
            try:
                _, hist = await self.comfy_json("GET", f"/history/{pid}", timeout=aiohttp.ClientTimeout(total=10))
            except Exception:
                hist = None
            outputs = ((hist or {}).get(pid) or {}).get("outputs") or {}
            if any(isinstance(values, list) and values for out in outputs.values() if isinstance(out, dict)
                   for values in out.values()):  # outputs is node id -> {"images": [...], "animated": [...]}
                return outputs
            if n < asks - 1:
                await asyncio.sleep(delay * (n + 1))
        return {}

    # ---------- what a job is, and where it is ----------

    def backend_identity(self):
        """Which engine a job belongs to. The settings may change later; a job must not follow them."""
        if self.rh():
            return "cloud:runninghub"
        return "comfy:" + self.comfy

    def build_backend(self):
        """The engine behind the job service: small callables, not the whole App."""
        return ComfyBackend(client=self.comfy_client, url=self.comfy, client_id=CLIENT_ID,
                            models=lambda: self.model_names, loras=lambda mode: self.custom_loras(mode),
                            view=self.view_stream)

    async def comfy_client(self, method, path, **kwargs):
        """The backend's HTTP: an engine that does not answer is not a job that failed."""
        try:
            return await self.comfy_json(method, path, **kwargs)
        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError, OSError) as e:
            raise BackendUnavailable(f"ComfyUI не отвечает: {type(e).__name__}") from e

    def attach_job(self, record, *, meta, last_output=False, titles=None, preview=False,
                   graph=None, stages=None, scenes=None):
        """Put a submitted job into the live queue and the database.

        The record is the truth; this dict is what the window, the engine events and the agent view
        have always used. One place builds it, whether the job came through the service or not.
        """
        pid = record.prompt_id or record.backend_job_id
        if not pid:
            raise InvalidTransition("У задания нет идентификатора: показывать в очереди нечего")
        job = {"prompt_id": pid, "state": public_state(record.state),
               "stages": stages if stages is not None else (record.legacy or {}).get("stages") or {},
               "stage": None, "meta": meta, "last_output": last_output, "titles": titles or {},
               "graph": graph if graph is not None else record.graph, "scenes": scenes,
               "t0": record.created_at, "preview": preview, "record": record}
        self.jobs[pid] = job
        return job

    def new_record(self, spec, prompt_id, backend_job_id=None):
        """The typed record of a new job: our id, the engine's id, the public id and the request.

        The record is born already queued: it exists only once the engine has accepted the job and
        given it an id. Before that there is nothing to track — the durable "submitting" moment of
        stage 4 is what will change that.
        """
        now = datetime.now().timestamp()
        return JobRecord(job_id=new_job_id(), spec=spec, backend="cloud" if self.rh() else "comfy",
                         backend_id=self.backend_identity(), backend_job_id=backend_job_id or prompt_id,
                         prompt_id=prompt_id, state=JobState.QUEUED, created_at=now, updated_at=now)

    def set_job_state(self, job, target, *, error=None):
        """The one place a job changes state.

        The rules live in `domain.jobs`; here the legacy fields the window and the agent API read are
        kept in step with the record: `state` in the words they already know, and the `finalizing`
        mark. A job without a record (an older run, or a test double) keeps the old behaviour.
        """
        target = JobState(target)
        record = job.get("record")
        if record is None:
            job["state"] = public_state(target)
            job["finalizing"] = target is JobState.FINALIZING
            return True
        changed = record.apply(target, now=datetime.now().timestamp(), error=error)
        job["state"] = public_state(record.state)
        job["finalizing"] = record.state is JobState.FINALIZING
        return changed

    def try_set_job_state(self, job, target, *, error=None):
        """Like set_job_state, but a late or repeated engine event is ignored instead of raising.

        The websocket loop must not be dropped because ComfyUI sent the same event twice or sent one
        for a job that has already ended.
        """
        try:
            return self.set_job_state(job, target, error=error)
        except InvalidTransition:
            return False

    async def finish_job(self, job):
        """Register the result of a job that ComfyUI has finished.

        Four different places ask for this — the websocket event, the reconnect reconciliation, the
        cancel race and the lost-job watch — and any of them can arrive twice or at the same moment.
        So the registration happens once per job, and the job leaves the queue only *after* its result
        is registered: between the generation and the library record the job stays visible, and a
        restart in that window can pick the result up instead of rendering it again.

        A failure here is not fatal: the job stays in the queue marked for retry, the error is shown,
        and the next event (or the watch) tries again — the clip on disk is not generated twice.
        """
        if job.get("finalizing") or job.get("finalized"):
            return   # a second event for the same job: the first one is registering its result
        pid = job["prompt_id"]
        self.set_job_state(job, JobState.FINALIZING)   # also sets the `finalizing` mark the watch reads
        job["finalize_tries"] = job.get("finalize_tries", 0) + 1
        self.save_queue()   # a crash in the middle of finalization must be known to the next start
        try:
            await self._finish_job(job)
        except Exception as e:  # noqa: BLE001 - a lost library record is better than a lost clip
            job["finalizing"] = False
            record = job.get("record")
            if record is not None:
                record.finalize_attempts += 1   # stays FINALIZING: the generation itself did happen
            self.note_error(f"Результат задания {pid} сгенерирован, но не сохранился: {type(e).__name__}: {e}")
            await self.broadcast({"type": "notice", "text": (
                "Результат готов, но записать его в Библиотеку не удалось: " + str(e) +
                ". Задание осталось в очереди — можно повторить.")})
            self.save_queue()

    async def _finish_job(self, job):
        """One call to the media service: find the outputs, make the copies, record, notify.

        The order inside the service is the recovery rule (see `services.media`): a broken transfer
        leaves a `.part`, never a half-written clip under the real name, and a repeated attempt finds
        the copy and the Library record of the previous one instead of making a second pair.
        """
        pid = job["prompt_id"]
        self.keep_meta(job)
        try:
            await self.media.finalize(job, notify=self.broadcast)
        except EmptyResult:
            self.note_error(f"ComfyUI закончил работу, но в истории нет файлов (папка {self.output_dir or 'output'})")
            self.set_job_state(job, JobState.FAILED, error="ComfyUI не вернул ни видео, ни изображение")
            self.jobs.pop(pid, None)   # there is nothing to wait for any more
            await self.broadcast({"type": "error", "prompt_id": pid,
                                  "message": "ComfyUI не вернул ни видео, ни изображение: проверьте папку output"})
            return
        job["finalized"] = True
        self.set_job_state(job, JobState.SUCCEEDED)
        self.jobs.pop(pid, None)   # the result is registered: only now the job leaves the queue

    async def keep_scene_state(self, job, refs):
        """A scene of the long video: keep the tail of its latent for the next scene, not for a size test.

        The latent is streamed through a `.part` like every other result file: it is small, but a
        half-written state would make the next scene load a broken one.
        """
        lg = (job.get("meta") or {}).get("long")
        if not lg or lg.get("test"):
            return
        lat = next((ref for _, ref in refs if str(ref["filename"]).lower().endswith(".latent")), None)
        videos = [ref for _, ref in refs if str(ref["filename"]).lower().endswith((".mp4", ".webm", ".mkv", ".mov"))]
        if not lat or not videos:
            return
        try:
            d = LONG_DIR / lg["run"]
            d.mkdir(parents=True, exist_ok=True)
            sfx = "_d" if lg.get("draft") else ""
            async with self.service.backend.open_output(lat) as stream:
                await self.media.files.save_stream(d / f"state_{lg['part']}{sfx}.latent", stream.chunks,
                                                   expected_bytes=stream.expected_bytes)
            atomic_write_json(d / f"state_{lg['part']}{sfx}.json",
                              {"res": (job.get("meta") or {}).get("res", "")})
        except Exception as e:
            self.note_error(f"Не удалось сохранить состояние сцены: {e}")

    async def broadcast(self, payload):
        self.remember(payload)
        text = json.dumps(payload, ensure_ascii=False)
        for b in list(self.browsers):
            try:
                await b.send_str(text)
            except Exception:
                self.browsers.discard(b)

    def remember(self, payload):
        """Keep the outcome of a job for a while: an agent asks about it after it has already left the queue."""
        kind = {"done": "done", "error": "error", "interrupted": "interrupted"}.get(payload.get("type", ""))
        pid = payload.get("prompt_id")
        if not kind or not pid:
            return
        self.recent[pid] = {"state": kind, "at": datetime.now().isoformat(timespec="seconds"),
                            "message": payload.get("message", "")}
        while len(self.recent) > 200:
            self.recent.pop(next(iter(self.recent)))

    def keep_meta(self, job):
        """Remember what a job was made from after it leaves the queue: the details window may be opened later."""
        if not job:
            return
        self.history[job["prompt_id"]] = {"meta": job.get("meta") or {}, "created": job.get("t0")}
        while len(self.history) > 200:
            self.history.pop(next(iter(self.history)))

    # ---------- helpers ----------

    def public(self, item):
        p = self.file_path(item)
        # a video deleted from disk stays in the list until the user removes it, but is marked
        return {k: v for k, v in item.items() if k != "file"} | {"url": f"/api/file/{item['id']}",
                                                                 "missing": bool(p) and not p.exists()}

    def file_path(self, item):
        if item["file"].get("type") == "local":
            return Path(item["file"]["path"])
        orig = None
        if self.output_dir and item["file"].get("type") == "output":
            orig = self.output_dir / item["file"].get("subfolder", "") / item["file"]["filename"]
        if orig and orig.exists():
            return orig
        local = item.get("local")   # the program's own copy: it stays when ComfyUI's output folder is cleaned
        if local and Path(local).exists():
            return Path(local)
        return orig

    def find_item(self, item_id):
        """A Library record, or a finished picture that is still waiting for «Добавить в библиотеку»."""
        return self.library.get(item_id) or self.pending.get(item_id)

    def hold_preview(self, item):
        """A picture is not added to the Library by itself: it waits until the user presses «Добавить в библиотеку»."""
        item["preview"] = True
        self.pending[item["id"]] = item
        while len(self.pending) > PREVIEW_LIMIT:
            self.pending.pop(next(iter(self.pending)))

    # ---------- own copies of the results (they must not vanish when ComfyUI's output folder is cleaned) ----------

    def default_results_dir(self):
        """Videos\\Minimax H3\\output in the user's folder: it does not depend on where the program lies and survives updates."""
        return Path.home() / "Videos" / "Minimax H3" / "output"

    def results_dir(self):
        d = self.config.get("results_dir")
        return Path(d) if d else self.default_results_dir()

    def keep_copies(self):
        return self.config.get("keep_copies") is not False

    async def archive_item(self, item):
        """Copy the finished file of a library item into the program's own results folder.

        The copy itself belongs to the media service: a `.part` next to the destination, a size
        check, an atomic rename, and a failure that is reported as `archive_failed` instead of
        putting the generation in doubt.
        """
        return await self.media.archive_item(item)

    async def upload_state(self, path, name):
        """Put a saved scene state (.latent) into ComfyUI's input folder, where its LoadLatent node looks for it."""
        data = aiohttp.FormData()
        data.add_field("image", path.read_bytes(), filename=name, content_type="application/octet-stream")
        data.add_field("type", "input")
        data.add_field("overwrite", "true")
        status, res = await self.comfy_json("POST", "/upload/image", data=data)
        if status != 200:
            raise web.HTTPBadGateway(text="Не удалось передать состояние сцены в ComfyUI")
        return res["name"]

    async def upload_input(self, field, rh=None):
        """Put an image/audio/video file into ComfyUI's input/h3studio folder (or upload it to RunningHub)."""
        data = aiohttp.FormData()
        name = f"h3s_{uuid.uuid4().hex[:8]}{Path(field.filename or 'img.png').suffix or '.png'}"
        content_type = field.headers.get("Content-Type", "application/octet-stream")
        raw = await field.read()
        if getattr(self, "_capture", None) is not None:
            self._capture[field.name] = (Path(field.filename or "x.png").suffix.lower() or ".png", raw)
        if rh:
            try:
                return await rh.upload(name, raw, content_type)
            except cloud.CloudError as e:
                raise web.HTTPBadGateway(text=str(e))
        data.add_field("image", raw, filename=name, content_type=content_type)
        data.add_field("subfolder", "h3studio")
        data.add_field("type", "input")
        status, res = await self.comfy_json("POST", "/upload/image", data=data)
        if status != 200:
            raise web.HTTPBadGateway(text="Не удалось загрузить файл в ComfyUI")
        return f'{res["subfolder"]}/{res["name"]}' if res.get("subfolder") else res["name"]

    async def put_input(self, path, name=None):
        """A file from disk into ComfyUI's input/h3studio (a picture an agent brought, a ready clip)."""
        path = Path(path)
        data = aiohttp.FormData()
        nm = name or f"h3s_{uuid.uuid4().hex[:8]}{path.suffix.lower() or '.png'}"
        try:
            raw = await asyncio.to_thread(path.read_bytes)
        except OSError as e:
            raise web.HTTPBadRequest(text=f"Не удалось прочитать файл: {e}")
        data.add_field("image", raw, filename=nm, content_type="application/octet-stream")
        data.add_field("subfolder", "h3studio")
        data.add_field("type", "input")
        status, res = await self.comfy_json("POST", "/upload/image", data=data)
        if status != 200:
            raise web.HTTPBadGateway(text="Не удалось передать файл в ComfyUI")
        return f'{res["subfolder"]}/{res["name"]}' if res.get("subfolder") else res["name"]

    # ---------- HTTP handlers ----------

    def storage_state(self):
        """What the database is: where it lives, which schema it has, what the import did."""
        migration = self.migration.to_dict() if self.migration is not None else None
        return {"path": str(STATE_DB), "schema_version": self.db.schema_version(),
                "migration": migration, "error": self.migration_error,
                "waiting": len(self.pending_resume)}

    async def h_status(self, _req):
        base = {"configured": bool(self.config.configured or self.comfy_override),
                "engine": self.config.get("engine"), "installing": bool(self.installer),
                # a queue that could not be written is not a saved queue: the window says so instead of hiding it
                "queue_save_error": self.queue_save_error,
                "data_dir": {"path": str(DATA_DIR), "warning": self.data_dir_warning},
                "storage": self.storage_state(),
                # what a closing run could not finish in its budget: the next start may have work to do
                "shutdown": self.last_shutdown}
        try:
            stats = await self.system_stats()
        except Exception:
            return web.json_response({**base, "online": False})
        dev = (stats.get("devices") or [{}])[0]
        await self.resolve_models()
        missing = {m["role"] for m in self.missing}
        needed = self.needed or catalog.roles_for(self.modes())
        models = [{"role": role, "title": catalog.ROLE_TITLES[role], "name": self.model_names[role],
                   "category": catalog.VARIANTS[role][variant]["category"],
                   "folder": catalog.VARIANTS[role][variant]["category"], "marks": catalog.marks_text(role),
                   "file": catalog.VARIANTS[role][variant]["file"], "url": engine.mirrored(catalog.VARIANTS[role][variant]["url"]),
                   "size": catalog.VARIANTS[role][variant]["size"], "have": role not in missing,
                   "needed": role in needed, "picked": role in (self.config.get("model_pick") or {}),
                   "modes": [m for m, roles in catalog.MODE_ROLES.items() if role in roles],
                   "variants": [{"key": k, "label": catalog.VARIANT_LABELS.get(role, {}).get(k, k), "size": v["size"],
                                 "current": k == variant, "have": v["file"] in getattr(self, "visible", {}).get(v["category"], ())}
                                for k, v in catalog.VARIANTS[role].items()] if len(catalog.VARIANTS[role]) > 1 else []}
                  for role, variant in self.choice().items() if role in needed]
        choice = self.choice()
        add_modes = {m: round((catalog.plan_size(choice, self.modes() + [m]) - catalog.plan_size(choice, self.modes())) / 1e9, 1)   # gigabytes (plan_size is in bytes)
                     for m in catalog.MODE_ROLES if m not in self.modes()}
        return web.json_response({
            **base, "online": True, "modes": self.modes(), "add_modes": add_modes, "hf_mirror": bool(self.config.get("hf_mirror")),
            "offer_modes": [{"mode": m, "gb": add_modes[m]} for m in catalog.OFFER_MODES
                            if m in add_modes and m not in (self.config.get("offered_modes") or [])],
            "gpu": gpu_name(dev.get("name", "")),
            "vram_total": dev.get("vram_total"), "vram_free": dev.get("vram_free"),
            "comfy_version": stats["system"].get("comfyui_version"),
            "output_dir": str(self.output_dir) if self.output_dir else None,
            "paths": {"install": self.config.get("install_dir"), "models": self.config.get("models_dir"),
                      "ext_models": self.config.get("ext_models_dir"),
                      "assistant": str(self.llm.root), "data": str(DATA_DIR)},
            "models": models,
        })

    async def nodes_status(self):
        """Which custom nodes the chosen sections need, and whether this ComfyUI knows them."""
        modes = set(self.modes())
        out = []
        for c in catalog.NODE_CHECKS:
            used = [m for m in c["modes"] if m in modes]
            if not used:
                continue
            have = True
            for cls in c["classes"]:
                try:
                    status, data = await self.comfy_json("GET", f"/object_info/{cls}", timeout=aiohttp.ClientTimeout(total=8))
                    have = have and status == 200 and isinstance(data, dict) and cls in data
                except Exception:
                    have = False
            repo = catalog.node_repo(c["pack"])
            out.append({"pack": c["pack"], "have": have, "optional": c["optional"], "why": c["why"], "modes": used,
                        "url": f"https://github.com/{repo}"})
        return out

    async def h_nodes(self, _req):
        try:
            return web.json_response({"nodes": await self.nodes_status()})
        except aiohttp.ClientError:
            return web.json_response({"error": "ComfyUI не отвечает"}, status=503)

    async def h_models_files(self, req):
        """Files ComfyUI sees in the folder this model role lives in, for picking one by hand."""
        role = req.query.get("role", "")
        if role not in catalog.VARIANTS:
            raise web.HTTPBadRequest(text="Неизвестная модель")
        cat = next(iter(catalog.VARIANTS[role].values()))["category"]
        try:
            _, lst = await self.comfy_json("GET", f"/models/{cat}")
        except Exception:
            raise web.HTTPServiceUnavailable(text="ComfyUI не отвечает")
        return web.json_response({"files": lst or [], "folder": cat})

    async def h_models_download(self, req):
        """Own ComfyUI: download missing model files of the app's catalog into the user's models folder (resumable)."""
        body = await req.json()
        managed = self.config.get("engine") == "managed"
        if managed:  # the app's own ComfyUI: the folder is known
            folder = str(Path(self.config.get("install_dir") or "") / catalog.COMFY_DIRNAME / "ComfyUI" / "models")
        else:
            folder = (body.get("folder") or self.config.get("ext_models_dir") or "").strip()
        p = Path(folder) if folder else None
        if not p or not p.is_dir():
            raise web.HTTPBadRequest(text="Папка не найдена. Укажите папку models вашего ComfyUI.")
        if not any((p / sub).is_dir() for sub in ("diffusion_models", "loras", "vae", "text_encoders", "checkpoints")):
            raise web.HTTPBadRequest(text="Это не похоже на папку models ComfyUI: внутри нет diffusion_models, loras, vae. Выберите папку, где они лежат.")
        if getattr(self, "dl_task", None) and not self.dl_task.done():
            raise web.HTTPConflict(text="Скачивание уже идёт. Дождитесь окончания или остановите его.")
        choice = self.choice()
        roles = [r for r in (body.get("roles") or [body.get("role")]) if r in catalog.VARIANTS and r in choice]
        if not roles:
            raise web.HTTPBadRequest(text="Неизвестная модель")
        if not managed:
            self.config.update(ext_models_dir=str(p))
        entries = [catalog.model_entry(r, choice[r]) for r in roles]

        async def emit(ev):
            await self.broadcast({"type": "mdl", "state": ev.get("state"), "label": ev.get("label"),
                                  "done": ev.get("overall_done", ev.get("done")), "total": ev.get("overall_total", ev.get("total")),
                                  "index": ev.get("index"), "count": ev.get("count")})

        inst = engine.Installer(ROOT, self.session, emit)
        self.dl_installer = inst

        async def run():
            total = sum(e["size"] for e in entries)
            base = 0
            try:
                for i, e in enumerate(entries):
                    dest = p / e["category"] / Path(e["name"].replace("\\", "/"))
                    label = catalog.ROLE_TITLES.get(e["role"], e["role"])

                    async def emit_overall(ev, base=base, idx=i, label=label):
                        if ev.get("done") is not None:
                            ev = dict(ev, overall_done=base + ev["done"], overall_total=total, index=idx + 1, count=len(entries), label=label)
                        await emit(ev)
                    inst.emit = emit_overall
                    await inst.download(e["url"], dest, "models", label, e["size"])
                    base += e["size"]
                await self.resolve_models()
                await self.broadcast({"type": "mdl", "state": "done", "label": "Готово: файлы скачаны и лежат в вашей папке моделей"})
            except engine.Cancelled:
                await self.broadcast({"type": "mdl", "state": "error", "label": "Скачивание остановлено. Скачанное сохранено: нажмите «Скачать» снова, и оно продолжится."})
            except Exception as ex:
                await self.broadcast({"type": "mdl", "state": "error", "label": str(ex)[:300]})
        self.dl_task = self.tasks.spawn("models", run())
        return web.json_response({"ok": True, "folder": str(p), "count": len(entries), "size": sum(e["size"] for e in entries)})

    async def h_models_download_cancel(self, _req):
        inst = getattr(self, "dl_installer", None)
        if inst:
            inst.cancelled = True
        return web.json_response({"ok": True})

    async def h_models_open(self, req):
        """Open the download page of a model in the browser (only links of the app's own catalog)."""
        body = await req.json()
        role = body.get("role")
        if role not in catalog.VARIANTS:
            raise web.HTTPBadRequest(text="Неизвестная модель")
        url = catalog.VARIANTS[role][self.choice().get(role) or next(iter(catalog.VARIANTS[role]))]["url"]
        url = engine.mirrored(url)
        webbrowser.open(url)
        return web.json_response({"ok": True, "url": url})

    async def h_models_compat(self, req):
        """Does an own model file fit the mode it is picked in (the speed-up files are made for one family of models)."""
        role, name, mode = req.query.get("role", ""), req.query.get("name", ""), req.query.get("mode", "")
        if role not in catalog.VARIANTS:
            raise web.HTTPBadRequest(text="Неизвестная модель")
        if self.hw is None:
            self.hw = await asyncio.to_thread(engine.detect_hardware)
        notes = catalog.compat_notes(mode, role, name or self.model_names.get(role, ""), self.hw.get("compute_cap") or 0)
        missing = {m["role"] for m in self.missing}
        picked = self.config.get("model_pick") or {}
        loras = [{"title": catalog.ROLE_TITLES.get(r, r), "name": self.model_names.get(r, ""), "have": r not in missing,
                  "must": r in catalog.MODE_ROLES.get(mode, []), "mine": r in picked}
                 for r in catalog.MODE_ROLES.get(mode, []) + catalog.MODE_OPTIONAL.get(mode, []) if r.endswith("_lora") and r in self.model_names]
        loras += [{"title": "Своя LoRA", "name": x["file"], "have": True, "must": False, "mine": True, "custom": True, "off": mode in (x.get("skip") or [])}
                  for x in self.unique_loras() if x.get("on", True)]
        return web.json_response({"notes": [{"level": lv, "text": t} for lv, t in notes], "loras": loras,
                                  "picked": picked.get(role), "name": self.model_names.get(role)})

    async def h_models_pick(self, req):
        body = await req.json()
        role, name = body.get("role"), body.get("name") or ""
        if role not in catalog.VARIANTS:
            raise web.HTTPBadRequest(text="Неизвестная модель")
        if name and not catalog.looks_right(role, name):
            foreign = catalog.foreign_marks(role, name)
            if foreign:
                raise web.HTTPBadRequest(text=(
                    f"Этот файл — «{catalog.ROLE_TITLES.get(foreign[0], foreign[0])}», а не «{catalog.ROLE_TITLES.get(role, role)}». "
                    f"Выберите файл для нужной строки, иначе генерация сломается с непонятной ошибкой."))
            if not body.get("force"):
                title = catalog.ROLE_TITLES.get(role, role)
                return web.json_response({"ok": False, "warn": (
                    f"Название файла «{name.replace('/', chr(92)).split(chr(92))[-1]}» не похоже на нужное для «{title}». "
                    f"В названии обычно есть {catalog.marks_text(role)}. Если выбрать не тот файл, генерация сломается с "
                    f"непонятной ошибкой. Всё равно выбрать этот файл?")})
        picks = dict(self.config.get("model_pick") or {})
        if name:
            picks[role] = name
        else:
            picks.pop(role, None)
        self.config.update(model_pick=picks)
        await self.resolve_models()
        return web.json_response({"ok": True})

    def model_hint(self, mode, text):
        """After a ComfyUI error that smells like a wrong model file: say which models of this mode to check."""
        if not re.search(r"shape|size|conv|dtype|mat1|mat2|cannot be|expected|mismatch|tensor|dimension|state_dict|missing key|unexpected key|invalid", text or "", re.I):
            return ""
        roles = catalog.MODE_ROLES.get(mode, [])
        if not roles or not self.model_names:
            return ""
        picked = self.config.get("model_pick") or {}
        sus = [r for r in roles if r in self.model_names and (r in picked or not catalog.looks_right(r, self.model_names[r]))]
        bad = [r for r in sus if not catalog.looks_right(r, self.model_names[r])]
        lines = ["", "Подсказка: похоже, одна из моделей этого раздела не подходит (ошибка про размеры или формат данных)."]
        for r in bad:
            lines.append(f"• «{catalog.ROLE_TITLES.get(r, r)}»: сейчас стоит {self.model_names[r].replace('/', chr(92)).split(chr(92))[-1]}, "
                         f"а в названии нужного файла есть {catalog.marks_text(r)}.")
        for r in sus:
            if r not in bad:
                lines.append(f"• «{catalog.ROLE_TITLES.get(r, r)}»: вы выбирали файл вручную, это "
                             f"{self.model_names[r].replace('/', chr(92)).split(chr(92))[-1]}. Проверьте его.")
        lines.append("Откройте «Настройки» и для нужной строки нажмите «Заменить…», выберите файл «как по умолчанию» или тот, "
                     "что указан в подсказке строки. Если непонятно, нажмите «Отчёт для разработчика» и пришлите файл.")
        return "\n".join(lines)

    async def h_models_variant(self, req):
        """Use another version of the same model (Singularity v1.3 -> v2, a lighter one...). The file is fetched by "Докачать"."""
        body = await req.json()
        role, variant = body.get("role"), body.get("variant") or ""
        if role not in catalog.VARIANTS or (variant and variant not in catalog.VARIANTS[role]):
            raise web.HTTPBadRequest(text="Неизвестная версия модели")
        over = dict(self.config.get("choice_override") or {})
        if variant:
            over[role] = variant
        else:
            over.pop(role, None)
        picks = dict(self.config.get("model_pick") or {})
        picks.pop(role, None)  # a file picked by hand belonged to the old choice
        self.config.update(choice_override=over, model_pick=picks)
        await self.resolve_models()
        entry = catalog.model_entry(role, self.choice()[role])
        have = role not in {m["role"] for m in self.missing}
        return web.json_response({"ok": True, "have": have, "size": entry["size"], "file": entry["file"]})

    async def h_update_check(self, _req):
        info = await updater.check(self.session)
        self.update_info = info if info.get("available") else None
        return web.json_response({**{k: v for k, v in info.items() if k not in ("asset", "sha256")},
                                  "can_update": updater.can_update()})

    async def h_update_install(self, _req):
        info = getattr(self, "update_info", None)
        if not info or not updater.can_update():
            raise web.HTTPBadRequest(text="Обновление недоступно")
        if self.jobs:
            raise web.HTTPConflict(text="Сейчас идёт генерация. Дождитесь её конца и нажмите «Обновить» снова.")
        try:
            installer = await updater.download(self.session, info)
        except Exception as e:
            raise web.HTTPBadGateway(text=str(e))
        updater.run(installer)
        # the installer is already running on its own: leave, so it can replace the files and open the app again
        asyncio.get_running_loop().call_later(3, lambda: os._exit(0))
        return web.json_response({"ok": True})

    async def h_system(self, _req):
        """CPU / RAM / GPU / disk readings for the sidebar, and what the video card watch sees."""
        where = self.output_dir or self.config.get("install_dir") or HOME
        data = await asyncio.to_thread(self.monitor.sample, where)
        data["watch"] = self.watch_state()
        data["watchdog"] = self.watchdog_on()
        return web.json_response(data)

    # ---------- first-run setup ----------

    async def setup_emit(self, ev):
        if ev.get("state") == "error":
            self.note_error(f'Установка, шаг {ev.get("step")}: {ev.get("label")}')
        if ev.get("state") != "progress" or ev.get("step") != getattr(self, "_last_step", None):
            self.setup_log = (self.setup_log + [ev])[-40:]
        self._last_step = ev.get("step")
        self.setup_last = ev
        await self.broadcast(ev)

    async def h_setup_info(self, req):
        install_dir = req.query.get("install_dir") or self.config.get("install_dir") or engine.default_install_dir()
        modes = [m for m in (req.query.get("modes") or ",".join(catalog.MODE_ROLES)).split(",") if m in catalog.MODE_ROLES]
        hw = await asyncio.to_thread(engine.detect_hardware, install_dir)
        choice, notes, verdict, profile = catalog.choose_models(hw, req.query.get("profile"))
        plan = [catalog.model_entry(r, choice[r]) for r in catalog.roles_for(modes or ["video"])]
        need_gb = round((catalog.COMFY_SIZE * 2.5 + sum(m["size"] for m in plan)) / 1e9)
        # download size of each profile for each set of modes, for the size picker
        sizes = {}
        for p in catalog.PROFILES:
            c = catalog.choose_models(hw, p)[0]
            sizes[p] = {m: round(catalog.plan_size(c, [m]) / 1e9) for m in catalog.MODE_ROLES}
            sizes[p]["selected"] = round(catalog.plan_size(c, modes or ["video"]) / 1e9)
        return web.json_response({
            "hardware": hw, "choice": choice, "notes": notes, "verdict": verdict,
            "profile": profile, "recommended": catalog.recommended_profile(hw), "modes": modes,
            "profiles": {k: {"title": v["title"], "about": v["about"], "sizes": sizes[k]} for k, v in catalog.PROFILES.items()},
            "install_dir": install_dir, "need_gb": need_gb,
            "plan": [{k: m[k] for k in ("role", "title", "file", "size", "variant")} for m in plan],
            "config": {k: self.config.get(k) for k in ("engine", "install_dir", "models_dir", "comfy_url", "setup_done")},
            "installing": bool(self.installer), "log": self.setup_log,
            "last": getattr(self, "setup_last", None),
        })

    async def h_setup_external(self, req):
        body = await req.json()
        url = (body.get("url") or "http://127.0.0.1:8188").strip().rstrip("/")
        if not url.startswith("http"):
            url = "http://" + url
        old, old_key = self.config.get("comfy_url"), self.config.get("comfy_key")
        key = (body.get("key") or "").strip()
        pu = urllib.parse.urlparse(url)
        tok = (urllib.parse.parse_qs(pu.query).get("token") or [""])[0]
        remote = pu.hostname not in ("127.0.0.1", "localhost", "::1")
        base = f"{pu.scheme}://{pu.netloc}"
        if tok and not key:   # the whole link of a rented server was pasted («http://IP/?token=...»): take the key out of it
            key = tok
            if remote and not pu.port:
                base = f"{pu.scheme}://{pu.hostname}:8000"   # such a link opens the page; the API of the Immers.Cloud image is on port 8000
        url = base + ("/api" if pu.path.rstrip("/").endswith("/api") else "")   # a path or a query in the pasted address is dropped
        self.config.data["comfy_key"] = key
        # switching to the user's own engine: the app's managed ComfyUI is ours, so it is stopped
        # instead of left running behind the new address (one owner, one process, one card)
        if self.engines.process is not None:
            await self.engines.stop()
        stats, last_err, used = None, None, url
        # a rented server may publish the API only under «/api» (as the Immers.Cloud image does): try both
        for cand in ([url] + ([url + "/api"] if remote and not url.rstrip("/").endswith("/api") else [])):
            self.config.data["comfy_url"] = cand
            try:
                stats = await self.system_stats()
                used = cand
                break
            except Exception as e:
                last_err = e
        if stats is None:
            self.config.data["comfy_url"] = old
            self.config.data["comfy_key"] = old_key
            hint = " Если сервер арендован: проверьте ключ, что порт открыт в брандмауэре самой машины (для Immers.Cloud это порт 8000) и что адрес вида http://IP:8000." if remote else ""
            return web.json_response({"error": f"ComfyUI не отвечает по адресу {url} ({type(last_err).__name__}). Откройте этот адрес "
                                      "в браузере: если страница ComfyUI не открывается, проверьте адрес и порт в окне, "
                                      "где запущен ComfyUI." + hint}, status=400)
        url = used
        ws_ok = True
        try:   # progress and «done» of every job come over the WebSocket: with a rented server it may be blocked by its proxy
            async with self.session.ws_connect(url.replace("http", "ws", 1) + f"/ws?clientId={CLIENT_ID}", timeout=8) as _ws:
                pass
        except Exception:
            ws_ok = False
        self.config.update(comfy_key=key)
        modes = [m for m in (body.get("modes") or []) if m in catalog.MODE_ROLES] or list(catalog.MODE_ROLES)
        self.config.update(engine="external", comfy_url=url, setup_done=True, choice=catalog.DEFAULT_CHOICE, modes=modes)
        names, missing = await self.resolve_models()
        try:
            nodes = [n["pack"] for n in await self.nodes_status() if not n["have"] and not n["optional"]]
        except Exception:
            nodes = []
        return web.json_response({"ok": True, "version": stats["system"].get("comfyui_version"), "url": url, "ws_ok": ws_ok,
                                  "missing": [m["file"] for m in missing], "nodes_missing": nodes})

    async def h_mode_offered(self, req):
        mode = (await req.json()).get("mode")
        self.config.update(offered_modes=sorted(set(self.config.get("offered_modes") or []) | {mode}))
        return web.json_response({"ok": True})

    async def h_mode_add(self, req):
        """Own ComfyUI: switch on a mode added after the set-up, so its model files show up in Models (with download buttons)."""
        mode = (await req.json()).get("mode")
        if mode not in catalog.MODE_ROLES:
            return web.json_response({"error": "Неизвестный режим"}, status=400)
        modes = sorted(set(self.modes()) | {mode}, key=list(catalog.MODE_ROLES).index)
        self.config.update(modes=modes)
        await self.resolve_models()
        return web.json_response({"ok": True})

    async def h_setup_install(self, req):
        if self.installer:
            return web.json_response({"error": "Установка уже идёт"}, status=409)
        body = await req.json()
        install_dir = str(Path(body.get("install_dir") or engine.default_install_dir()))
        models_dir = (body.get("models_dir") or "").strip() or None
        if models_dir and not Path(models_dir).is_dir():
            return web.json_response({"error": f"Папка не найдена: {models_dir}"}, status=400)
        hw = await asyncio.to_thread(engine.detect_hardware, install_dir)
        profile = body.get("profile") or self.config.get("profile")
        choice, _, verdict, profile = catalog.choose_models(hw, profile)
        choice = {**choice, **(self.config.get("choice_override") or {})}  # a version the user picked by hand stays
        modes = [m for m in (body.get("modes") or self.modes()) if m in catalog.MODE_ROLES] or ["video"]
        if body.get("add_mode"):
            modes = sorted(set(self.modes()) | {body["add_mode"]}, key=list(catalog.MODE_ROLES).index)
        self.config.update(engine="managed", install_dir=install_dir, models_dir=models_dir,
                           choice=choice, profile=profile, modes=modes, setup_done=False)
        self.setup_log = []
        self.installer = engine.Installer(ROOT, self.session, self.setup_emit)
        self.installer.task = self.tasks.spawn("install", self.run_install(install_dir, models_dir))
        return web.json_response({"ok": True})

    async def run_install(self, install_dir, models_dir):
        inst = self.installer
        try:
            Path(install_dir).mkdir(parents=True, exist_ok=True)
            root = await inst.install_comfy(install_dir)
            await inst.install_nodes(root)
            if models_dir:
                inst.write_extra_paths(root, models_dir)
            await inst.step("start", "progress", label="Запускаем ComfyUI…")
            await self.engines.stop()
            self.start_managed()
            await self.wait_comfy()
            await inst.step("start", "done", label="ComfyUI запущен")
            _, missing = await self.resolve_models()
            # shared files and the video mode first, so the first mode is usable sooner
            order = ["text_encoder", "video_vae", "audio_vae", "preview", "fast_unet"]
            missing.sort(key=lambda m: order.index(m["role"]) if m["role"] in order else len(order))
            if missing:
                gb = sum(m["size"] for m in missing) / 1e9
                await inst.step("models", "progress", label=f"Скачиваем модели: {len(missing)} файлов, {gb:.0f} ГБ")
                await inst.download_models(root / "ComfyUI" / "models", missing)
                await self.resolve_models()
            else:
                await inst.step("models", "done", label="Все модели уже есть")
            self.config.update(setup_done=True)
            await self.setup_emit({"type": "setup", "step": "finish", "state": "done", "label": "Готово!"})
        except engine.Cancelled:
            await self.setup_emit({"type": "setup", "step": "finish", "state": "cancelled",
                                   "label": "Установка остановлена. Скачанное сохранено — можно продолжить позже."})
        except Exception as e:
            await self.setup_emit({"type": "setup", "step": "finish", "state": "error", "label": str(e)})
        finally:
            self.installer = None

    def note_error(self, text):
        self.recent_errors = (self.recent_errors + [f"{datetime.now():%H:%M:%S} {text}"])[-15:]

    async def h_report(self, req):
        """Collect everything the author needs into one text file and show it in Explorer."""
        try:
            page = await req.json()
        except Exception:
            page = {}
        comfy = {"адрес": self.comfy, "режим": self.config.get("engine"), "онлайн": self.comfy_online,
                 "настройка завершена": bool(self.config.get("setup_done"))}
        try:
            st = await self.system_stats()
            sysinfo = st.get("system", {})
            comfy.update({"версия ComfyUI": sysinfo.get("comfyui_version"), "версия PyTorch": sysinfo.get("pytorch_version"),
                          "запуск": " ".join(map(str, sysinfo.get("argv", []))),
                          "устройства": "; ".join(f'{d.get("name")} (всего {round(d.get("vram_total", 0) / 1024 ** 3, 1)} ГБ, '
                                                  f'свободно {round(d.get("vram_free", 0) / 1024 ** 3, 1)} ГБ)' for d in st.get("devices", []))})
            await self.resolve_models()
            needed = self.needed or catalog.roles_for(self.modes())
            missing = {m["role"] for m in self.missing}
            models = [{"title": catalog.ROLE_TITLES[r], "name": self.model_names.get(r), "have": r not in missing}
                      for r in self.choice() if r in needed]
        except Exception as e:
            comfy["ошибка связи"] = f"{type(e).__name__}: {e}"
            models = []
        hw = await asyncio.to_thread(engine.detect_hardware, self.config.get("install_dir"))
        long_states = []
        try:
            for d in sorted(LONG_DIR.iterdir()) if LONG_DIR.is_dir() else []:
                files = sorted(d.glob("state_*.latent"))
                if not files:
                    continue
                sizes = ", ".join(f"{f.name}: {round(f.stat().st_size / 1024, 1)} КБ" for f in files)
                long_states.append(f"{d.name}: {sizes}")
        except OSError as e:
            long_states.append(f"не удалось прочитать папку: {e}")
        text = await asyncio.to_thread(report.build, {
            "long_states": long_states,
            "home": HOME, "data_dir": DATA_DIR, "hw": hw, "page": page.get("page"), "comfy": comfy, "models": models,
            "config": self.config.data, "errors": self.recent_errors, "toasts": page.get("toasts"),
            "comfy_log": self.engines.log_tail(60),
            "guard": self.guard_log_tail(), "guard_on": self.watchdog_on(),
            "shutdown": self.last_shutdown,   # what a closing run could not finish in its budget
            "llm_log": self.llm.log_tail(12)})
        folder = DATA_DIR / "reports"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"Отчёт для разработчика {datetime.now():%Y-%m-%d %H-%M-%S}.txt"
        path.write_text(text, encoding="utf-8")
        try:
            subprocess.Popen(["explorer", "/select,", str(path)])
        except Exception:
            pass
        return web.json_response({"ok": True, "path": str(path), "name": path.name})

    async def h_open_driver(self, _req):
        webbrowser.open("https://www.nvidia.com/Download/index.aspx?lang=ru")
        return web.json_response({"ok": True})

    async def h_search_web(self, req):
        """Open a web search in the user's browser (missing model file / custom node in 'Свои workflow')."""
        body = await req.json()
        q = str(body.get("q", "")).strip()[:200]
        if not q:
            raise web.HTTPBadRequest(text="Пустой запрос")
        webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(q))
        return web.json_response({"ok": True})

    # ---------- the queue: unfinished jobs, kept in the database ----------

    def load_resume_queue(self):
        """Jobs the database holds but this run has not started: what the window asks about at start.

        The queue never lived longer than a week (the JSON version dropped older records), so an older
        record is forgotten here as well instead of being offered forever.

        A cloud job is not in this list: the window closing never cancelled it on RunningHub, so it is
        watched again at start (`watch_cloud_jobs`) instead of being asked about — and it is never
        re-sent, which would render (and pay for) the same clip twice.
        """
        week_ago = datetime.now().timestamp() - 7 * 86400
        out = []
        self.resume_cloud = []
        for record in self.jobs_repo.active():
            if record.created_at and record.created_at < week_ago:
                self.jobs_repo.delete(record.job_id)
                continue
            if record.backend == "cloud" and record.backend_job_id:
                self.resume_cloud.append(record)
                continue
            out.append(record)
        return out

    def persisted_job(self, job):
        """The record of a live job, with the fields the window and the engine still keep in the dict.

        `legacy` is the transitional part: the queue-record fields of the older version (stages, meta,
        titles, preview). Stage 4 folds them into the job service; until then they travel with the
        record, so a restart can resume a job exactly instead of guessing its parameters back.
        """
        record = job.get("record")
        if record is None:
            return None
        if record.graph is None:
            record.graph = job.get("graph")
        record.legacy = {"stages": job.get("stages") or {}, "meta": job.get("meta") or {},
                         "titles": job.get("titles") or {}, "last_output": bool(job.get("last_output")),
                         "preview": bool(job.get("preview"))}
        return record

    def save_queue(self):
        """Persist the live jobs. Called on every change of the job list.

        The database is the queue now: a job is written when it is added and on every change, and its
        row goes away when the job leaves memory (finished, cancelled, dropped). A failed write is
        remembered and reported instead of raising into the caller's state change.
        """
        try:
            live = set()
            waiting = ({record.job_id for record in self.pending_resume}
                       | {record.job_id for record in getattr(self, "resume_cloud", ())})
            with self.db.transaction():
                for job in list(self.jobs.values()):
                    record = self.persisted_job(job)
                    if record is None:
                        continue
                    self.jobs_repo.save(record)
                    live.add(record.job_id)
                for row in self.db.query("SELECT job_id FROM jobs"):
                    if row["job_id"] not in live and row["job_id"] not in waiting:
                        self.jobs_repo.delete(row["job_id"])
            self.queue_save_error = None
        except STORE_ERRORS as e:
            self.queue_save_error = str(e)
            self.note_error(f"Не удалось сохранить очередь заданий: {e}")

    async def h_queue_saved(self, _req):
        items = [{"mode": (record.legacy.get("meta") or {}).get("mode"),
                  "prompt": ((record.legacy.get("meta") or {}).get("prompt") or "")[:80]}
                 for record in self.pending_resume]
        return web.json_response({"count": len(items), "items": items})

    def job_from_record(self, record):
        """The job dict the window, the watch and the engine paths have always used, rebuilt from the
        record. `finalizing` is not restored: whoever resumes the job decides that."""
        legacy = record.legacy or {}
        pid = record.prompt_id or record.backend_job_id
        now = datetime.now().timestamp()
        return {"prompt_id": pid, "state": public_state(record.state), "stages": legacy.get("stages") or {},
                "stage": None, "meta": legacy.get("meta") or {}, "last_output": legacy.get("last_output", False),
                "titles": legacy.get("titles") or {}, "graph": record.graph, "scenes": None,
                "t0": record.created_at or now, "last_ev": now, "preview": legacy.get("preview", False),
                # a cloud job is watched by its task id; it is never sent to ComfyUI again
                "task": record.backend_job_id if record.backend == "cloud" else None,
                "record": record}

    def watch_cloud_jobs(self):
        """Watch the cloud jobs that were still running when the window closed.

        Closing the program never cancelled them on RunningHub: the task id is in the record, and the
        new run attaches to it instead of asking ComfyUI (which never had it) or sending it again
        (which would render — and pay for — the same clip twice). With the cloud switched off the
        records are kept for the next start, not lost.
        """
        records, self.resume_cloud = list(getattr(self, "resume_cloud", ())), []
        if not records:
            return
        rh = self.rh()
        if rh is None:
            self.resume_cloud = records
            self.note_error(f"Облачных заданий ждёт облако: {len(records)} (включите облако, чтобы следить за ними)")
            return
        for record in records:
            job = self.job_from_record(record)
            pid = job["prompt_id"]
            if not pid or pid in self.jobs:
                continue
            self.jobs[pid] = job
            self.try_set_job_state(job, JobState.QUEUED)   # the first status of the task moves it on
            self.tasks.spawn(f"cloud:{pid}", self.poll_cloud(rh, pid), replace=True)

    async def _history_done(self, pid):
        """What ComfyUI's history says about a job of the previous run: finished, failed or unknown."""
        try:
            _, hist = await self.comfy_json("GET", f"/history/{pid}", timeout=aiohttp.ClientTimeout(total=10))
        except Exception:
            return "unknown"
        entry = (hist or {}).get(pid)
        if not entry:
            return "unknown"
        if entry.get("status", {}).get("status_str") == "error":
            return "failed"
        return "finished" if entry.get("outputs") else "unknown"

    async def _resume_one(self, job, running, pending, backend_ok, out):
        """Resume one job of an earlier run; True when it may leave the waiting list, False to keep it."""
        meta = job.get("meta") or {}
        mode, prompt = meta.get("mode"), (meta.get("prompt") or "")[:80]
        pid = job.get("prompt_id")
        record = job.get("record")
        if record is not None and record.backend_id and record.backend_id != self.backend_identity():
            # the settings now point at another engine: this job belongs to the one that had it, and
            # sending it to a different server would generate a different clip behind the user's back
            self.note_error(f"Задание {pid} ждёт прежний движок ({record.backend_id}), а сейчас выбран другой")
            out.append({"prompt_id": pid, "mode": mode, "prompt": prompt, "state": "kept",
                        "reason": "задание принадлежит другому движку"})
            return False
        live = ("running" if pid in running else "queued" if pid in pending else None) if pid else None
        if live:  # ComfyUI still has it (or is running it): watch it instead of sending it twice
            if pid not in self.jobs:
                self.jobs[pid] = job
                self.set_job_state(job, JobState.RUNNING if live == "running" else JobState.QUEUED)
            out.append({"prompt_id": pid, "mode": mode, "prompt": prompt, "state": live})
            return True
        if not backend_ok:
            return False  # an engine that does not answer has not lost anything: keep the record as it is
        if pid:
            done = await self._history_done(pid)
            if done == "finished":  # it finished while the window was closed: collect the result, do not render again
                self.jobs[pid] = job
                await self.finish_job(job)
                if not job.get("finalized"):
                    # the result could not be registered (e.g. the disk refused the Library write):
                    # the record stays in the waiting list, which is what the next start retries from
                    self.jobs.pop(pid, None)
                    out.append({"prompt_id": pid, "mode": mode, "prompt": prompt, "state": "queued"})
                    return False
                out.append({"prompt_id": pid, "mode": mode, "prompt": prompt, "state": "done"})
                return True
            if done == "failed":
                self.recent[pid] = {"state": "error", "at": datetime.now().isoformat(timespec="seconds"),
                                    "message": "ComfyUI закончил это задание с ошибкой, пока окно было закрыто"}
                out.append({"prompt_id": pid, "mode": mode, "prompt": prompt, "state": "error"})
                return True
        resp = await self.queue(job["graph"], job.get("stages") or {}, meta, job.get("last_output", False),
                                job.get("titles"), job.get("preview", False))
        body = json.loads(resp.text)
        if resp.status != 200 or not body.get("prompt_id"):
            raise RuntimeError(body.get("error") or f"ComfyUI отклонил задание ({resp.status})")
        out.append({"prompt_id": body["prompt_id"], "mode": mode, "prompt": prompt, "state": "queued"})
        return True

    async def h_queue_resume(self, _req):
        """Bring back the unfinished jobs of an earlier run, each one on its own.

        A job leaves the waiting list only after it was really resumed (re-attached to a live job,
        taken from the history, or submitted again), so one broken record or an engine that is not up
        yet cannot lose the rest. A ComfyUI that does not answer is not an empty queue and its jobs
        are never sent to it blindly.
        """
        items = list(self.pending_resume)
        if not items:
            return web.json_response({"jobs": [], "restored": 0, "kept": 0, "unreachable": False})
        running, pending, backend_ok = set(), set(), True
        try:
            _, q = await self.comfy_json("GET", "/queue")
            for key, bucket in (("queue_running", running), ("queue_pending", pending)):
                bucket.update(str(x[1]) for x in (q or {}).get(key, []) if len(x) > 1)
        except Exception as e:
            backend_ok = False
            self.note_error(f"Очередь прошлого запуска ждёт ComfyUI: он не отвечает ({type(e).__name__})")
        out, kept = [], 0
        for record in items:
            job = self.job_from_record(record)
            try:
                resumed = await self._resume_one(job, running, pending, backend_ok, out)
            except Exception as e:
                self.note_error(f"Не удалось восстановить задание из прошлой очереди: {e}")
                resumed = False
            if not resumed:
                kept += 1
                continue
            self.pending_resume = [r for r in self.pending_resume if r.job_id != record.job_id]
            self.save_queue()   # after every job: a crash in the middle must not submit anything twice
        return web.json_response({
            "jobs": out, "restored": len(out), "kept": kept, "unreachable": not backend_ok,
            "error": None if backend_ok else "ComfyUI не отвечает: очередь сохранена и будет восстановлена позже"})

    async def h_queue_discard(self, _req):
        for record in list(self.pending_resume):
            self.jobs_repo.delete(record.job_id)
        self.pending_resume = []
        return web.json_response({"ok": True})

    async def h_job(self, req):
        """Everything about one job for the details window: what it is, what it was made from, how it ended."""
        pid = req.match_info["pid"]
        out = {"prompt_id": pid, "state": "unknown", "fps": wf.FPS}
        job = self.jobs.get(pid)
        if job:
            record = job.get("record")
            out.update(state=job.get("state") or "queued", stage=job.get("stage"), step=job.get("step"),
                       max=job.get("step_max"), created=job.get("t0"), meta=job.get("meta") or {},
                       finalizing=bool(job.get("finalizing")),
                       phase=record.state.value if record is not None else None)
            return web.json_response(out)
        items = [self.public(x) for x in (*self.library.items, *self.pending.values()) if x.get("prompt_id") == pid]
        if items:   # finished: the Library record keeps the job's own meta (prompt, format, inputs)
            drop = {"file", "url", "local", "fav", "id", "kind", "ext", "prompt_id", "date", "missing"}
            out.update(state="done", meta={k: v for k, v in items[0].items() if k not in drop},
                       created=items[0].get("date"), items=items)
            return web.json_response(out)
        kept = self.history.get(pid) or {}
        if kept:
            out.update(meta=kept.get("meta") or {}, created=kept.get("created"))
        if pid in self.recent:
            r = self.recent[pid]
            out.update(state=r.get("state") or "unknown", message=r.get("message") or "")
        elif not kept:
            waiting = next((record for record in self.pending_resume
                            if (record.prompt_id or record.backend_job_id) == pid), None)
            if not waiting:
                raise web.HTTPNotFound()
            out.update(state="queued", meta=(waiting.legacy or {}).get("meta") or {},
                       created=waiting.created_at, phase=waiting.state.value)
        return web.json_response(out)

    async def h_input(self, req):
        """A file a job was made from (a picture, a reference video, the audio): ComfyUI keeps it in its input folder."""
        name = (req.query.get("name") or "").strip()
        path = Path(name.replace("\\", "/"))
        parts = [p for p in path.parts if p not in ("", ".")]
        if not parts or path.is_absolute() or ".." in parts or parts[0] == "..":
            raise web.HTTPNotFound()
        filename, subfolder = parts[-1], "/".join(parts[:-1])
        if not re.fullmatch(r"[A-Za-z0-9 _\-.,()\[\]{}@+=]+", filename):
            raise web.HTTPNotFound()
        key = hashlib.sha1((subfolder + "/" + filename).encode()).hexdigest()[:20]
        ext = Path(filename).suffix.lower()[:6] or ".bin"
        cache = DATA_DIR / "cache" / "input" / f"{key}{ext}"
        if not (cache.exists() and cache.stat().st_size > 0):
            try:
                async with self.session.get(self.comfy + "/view", params={
                        "filename": filename, "subfolder": subfolder, "type": "input"}) as r:
                    if r.status != 200:
                        raise web.HTTPNotFound(text="Файл задания не найден: ComfyUI его уже не хранит")
                    data = await r.read()
            except aiohttp.ClientError:
                raise web.HTTPServiceUnavailable(text="ComfyUI не отвечает")
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_name(f"{cache.name}.{uuid.uuid4().hex[:8]}.part")
            tmp.write_bytes(data)
            tmp.replace(cache)
        return web.FileResponse(cache)

    async def h_support(self, _req):
        """Open the author's donation page (a fixed address, never taken from the request)."""
        webbrowser.open(SUPPORT_URL)
        return web.json_response({"ok": True})

    async def h_rent_open(self, _req):
        """Open the rental site (the author's referral link if there is one): a fixed address, never taken from the request."""
        webbrowser.open(RENT_REFERRAL or RENT_SITE)
        return web.json_response({"ok": True})

    async def h_rent_info(self, _req):
        return web.json_response({"ref": bool(RENT_REFERRAL)})

    async def h_clipboard(self, _req):
        """Text from the Windows clipboard (the web view may refuse to let the page read it)."""
        text = ""
        try:
            import ctypes
            u, k = ctypes.windll.user32, ctypes.windll.kernel32
            u.GetClipboardData.restype = ctypes.c_void_p
            k.GlobalLock.restype = ctypes.c_void_p
            k.GlobalLock.argtypes = [ctypes.c_void_p]
            k.GlobalUnlock.argtypes = [ctypes.c_void_p]
            if u.OpenClipboard(0):
                try:
                    h = u.GetClipboardData(13)  # CF_UNICODETEXT
                    if h:
                        p = k.GlobalLock(h)
                        if p:
                            text = ctypes.wstring_at(p)
                            k.GlobalUnlock(h)
                finally:
                    u.CloseClipboard()
        except Exception:
            pass
        return web.json_response({"text": text})

    async def h_clipboard_has_image(self, _req):
        """Whether the "Вставить" item in the picture-slot menu should be clickable."""
        return web.json_response({"have": await asyncio.to_thread(clipboard_has_picture)})

    async def h_clipboard_image(self, req):
        """GET: the clipboard picture (404 when there is none). POST: put this PNG on the clipboard."""
        if req.method == "POST":
            data = await req.read()
            if not data.startswith(PNG_SIGNATURE):
                return web.json_response({"error": "Нужна картинка в формате PNG"}, status=400)
            if await asyncio.to_thread(clipboard_set_picture, data):
                return web.json_response({"ok": True})
            return web.json_response({"error": "Не удалось положить картинку в буфер обмена"}, status=500)
        picture = await asyncio.to_thread(clipboard_picture)
        if not picture:
            return web.json_response({"error": "Не удалось получить картинку из буфера обмена"}, status=404)
        data, mime = picture
        return web.Response(body=data, content_type=mime, headers={"Cache-Control": "no-store"})

    async def h_setup_cancel(self, _req):
        if self.installer:
            self.installer.cancelled = True
        return web.json_response({"ok": True})

    async def h_setup_reset(self, _req):
        """Back to the first-run wizard; installed files stay where they are."""
        if self.installer:
            return web.json_response({"error": "Сначала остановите установку"}, status=409)
        await self.engines.stop()   # the wizard starts over: the engine this app started is stopped
        self.config.update(setup_done=False)
        return web.json_response({"ok": True})

    # ---------- cloud (RunningHub) ----------

    def rh(self):
        """A RunningHub client when cloud generation is switched on, else None."""
        if self.config.get("rh_enabled") and self.config.get("rh_key"):
            return cloud.RunningHub(self.session, self.config.get("rh_key"), self.config.get("rh_host", "ai"))
        return None

    async def h_cloud_get(self, _req):
        c = self.config
        return web.json_response({"enabled": bool(c.get("rh_enabled")), "has_key": bool(c.get("rh_key")),
                                  "host": c.get("rh_host", "ai"), "workflow_id": c.get("rh_workflow_id", ""),
                                  "instance": c.get("rh_instance", "default")})

    async def h_cloud_set(self, req):
        b = await req.json()
        upd = {}
        if b.get("key"):
            upd["rh_key"] = b["key"].strip()
        if b.get("clear_key"):
            upd["rh_key"] = None
            upd["rh_enabled"] = False
        if b.get("host") in cloud.HOSTS:
            upd["rh_host"] = b["host"]
        if "workflow_id" in b:
            upd["rh_workflow_id"] = str(b["workflow_id"]).strip()
        if b.get("instance") in cloud.INSTANCE_TYPES:
            upd["rh_instance"] = b["instance"]
        if "enabled" in b:
            upd["rh_enabled"] = bool(b["enabled"]) and bool(upd.get("rh_key", self.config.get("rh_key")))
        self.config.update(**upd)
        self.rh_models = None
        return await self.h_cloud_get(req)

    async def h_setup_cloud(self, _req):
        """First-run choice "cloud only": nothing installed locally."""
        if not (self.config.get("rh_key") and self.config.get("rh_enabled")):
            return web.json_response({"error": "Сначала сохраните ключ и включите облако"}, status=400)
        self.config.update(engine="cloud", setup_done=True, modes=list(catalog.MODE_ROLES))
        return web.json_response({"ok": True})

    async def h_cloud_test(self, _req):
        key = self.config.get("rh_key")
        if not key:
            return web.json_response({"error": "Вставьте API-ключ"}, status=400)
        rh = cloud.RunningHub(self.session, key, self.config.get("rh_host", "ai"))
        try:
            acc = await rh.account()
            names = await rh.model_names(self.config.get("rh_workflow_id")) if self.config.get("rh_workflow_id") else set()
        except cloud.CloudError as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"ok": True, "account": acc, "models": sorted(names)[:40]})

    async def queue_cloud(self, rh, graph, stages, meta, preview=False, spec=None):
        if not self.config.get("rh_workflow_id"):
            return web.json_response({"error": "Укажите ID любого вашего workflow на RunningHub (раздел «Настройки» → «Облако»)"},
                                     status=400)
        if getattr(self, "rh_models", None) is None:
            try:
                self.rh_models = await rh.model_names(self.config.get("rh_workflow_id"))
            except cloud.CloudError:
                self.rh_models = set()
        cloud.remap_models(graph, self.rh_models)
        try:
            task = await rh.create(graph, self.config.get("rh_workflow_id"), self.config.get("rh_instance", "default"))
        except cloud.CloudError as e:
            return web.json_response({"error": str(e)}, status=400)
        pid = "rh-" + task
        self.jobs[pid] = {"prompt_id": pid, "state": "queued", "stages": {}, "stage": None,
                          "meta": dict(meta, cloud=True), "task": task, "t0": datetime.now().timestamp(),
                          "preview": preview, "record": self.new_record(spec, pid, backend_job_id=task)}
        self.set_job_state(self.jobs[pid], JobState.QUEUED)
        self.tasks.spawn(f"cloud:{pid}", self.poll_cloud(rh, pid), replace=True)
        return web.json_response({"prompt_id": pid, "seed": meta["seed"]})

    async def poll_cloud(self, rh, pid):
        job = self.jobs.get(pid)
        last = None
        misses = 0   # consecutive "could not ask": a network blink is not a failed remote job
        try:
            while pid in self.jobs:
                try:
                    status = await rh.status(job["task"])
                except cloud.CloudError:
                    misses += 1
                    if misses < 5:
                        await asyncio.sleep(CLOUD_RETRY)
                        continue
                    raise
                misses = 0
                if status is None:   # not known there any more (expired, or another key): say why
                    await rh.outputs(job["task"])   # raises with the provider's own reason
                    raise cloud.CloudError("RunningHub не знает это задание")
                if status != last:
                    last = status
                    stage = {"QUEUED": "cloud_queued", "RUNNING": "cloud_run"}.get(status)
                    if stage:
                        await self.send_stage(job, stage)
                    target = {"QUEUED": JobState.QUEUED, "RUNNING": JobState.RUNNING}.get(status)
                    if target is not None:
                        self.try_set_job_state(job, target)
                if status == "SUCCESS":
                    outs = await rh.outputs(job["task"]) or []
                    video = next((o for o in outs if str(o.get("fileType", "")).lower() in ("mp4", "webm", "mov", "mkv")
                                  or str(o.get("fileUrl", "")).lower().split("?")[0].endswith((".mp4", ".webm", ".mov"))), None)
                    if not video:
                        raise cloud.CloudError("RunningHub не вернул видео")
                    self.try_set_job_state(job, JobState.FINALIZING)
                    dest = DATA_DIR / "cloud" / f'{pid}.mp4'
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    # the clip is streamed through a `.part` and published only when complete: a
                    # broken cloud download must not leave a finished-looking video on the disk
                    async with self.session.get(video["fileUrl"]) as r:
                        if r.status != 200:
                            raise cloud.CloudError(f"RunningHub не отдал видео: HTTP {r.status}")
                        await self.media.files.save_stream(dest, r.content.iter_chunked(1 << 20),
                                                           expected_bytes=r.content_length)
                    self.jobs.pop(pid, None)
                    item = dict(job["meta"], id=uuid.uuid4().hex[:12], prompt_id=pid,
                                date=datetime.now().isoformat(timespec="seconds"), fav=False,
                                file={"type": "local", "path": str(dest), "filename": dest.name})
                    if video.get("consumeCoins"):
                        item["sub"] = f'{item.get("sub", "")} · ☁ {video["consumeCoins"]} RH'.strip(" ·")
                    if job.get("preview"):
                        self.hold_preview(item)
                    else:
                        try:
                            self.library.add(item)
                        except STORE_ERRORS as e:
                            # the file is already on disk: report where it is instead of losing it
                            self.note_error(f"Облачный результат не записался в Библиотеку: {e}")
                            await self.broadcast({"type": "error", "prompt_id": pid, "message": (
                                f"Видео скачано в файл {dest}, но в Библиотеку не попало: {e}")})
                            return
                    self.try_set_job_state(job, JobState.SUCCEEDED)
                    await self.broadcast({"type": "done", "prompt_id": pid, "item": self.public(item)})
                    return
                if status == "FAILED":
                    await rh.outputs(job["task"])  # raises with the failure reason
                    raise cloud.CloudError("Задание на RunningHub завершилось ошибкой")
                if pid not in self.jobs:
                    return   # it was finished, cancelled or dropped while we were asking
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise   # the window is closing: watching stops, the remote job itself is not cancelled
        except Exception as e:  # noqa: BLE001 - the cloud path reports its own errors to the window
            if pid in self.jobs:
                job = self.jobs.get(pid)
                self.try_set_job_state(job, JobState.FAILED, error=str(e))
                self.keep_meta(job)
                self.jobs.pop(pid, None)
                await self.broadcast({"type": "error", "prompt_id": pid, "message": str(e)})

    async def read_form(self, req, file_fields, need_prompt=True):
        form, files = {}, {}
        rh = self.rh()
        async for field in await req.multipart():
            if field.name in file_fields and field.filename:
                files[field.name] = await self.upload_input(field, rh)
            else:
                form[field.name] = await field.text()
        prompt = form.get("prompt", "").strip()
        aspect, quality = form.get("aspect", "16:9"), form.get("quality", "720p")
        if not prompt and need_prompt:
            raise web.HTTPBadRequest(text="Напишите промпт")
        if aspect not in wf.ASPECTS or quality not in wf.QUALITY:
            raise web.HTTPBadRequest(text="Неизвестный формат или качество")
        seed = int(form["seed"]) if form.get("seed", "").isdigit() else random.randrange(2 ** 50)
        return form, files, prompt, aspect, quality, seed

    async def prepare_engine(self, mode_key):
        """Before a job goes to the engine: the agent's model out of VRAM, the previous job's models freed.

        The rule (when to ask, when to keep the models for the next picture) lives in the engine
        manager; this is the app side of that handshake.
        """
        return await self.engines.prepare(mode_key)

    async def release_vram(self):
        """The card side of `prepare_engine`: ask ComfyUI to unload, then say what is still held."""
        try:
            await self.comfy_json("POST", "/free", json={"unload_models": True, "free_memory": True})
            await asyncio.sleep(1.5)
            gpu = await asyncio.to_thread(self.monitor.gpu)
            used = (gpu or {}).get("vram_used_gb")
            if used is not None and used > 2.5:  # right after /free ComfyUI itself holds well under this
                await self.broadcast({"type": "notice", "text": (
                    f"Перед заданием занято {used:g} ГБ видеопамяти. Скорее всего, её держат другие программы "
                    "(браузер, Adobe, игры). Закройте их, иначе генерация может идти медленнее.")})
        except Exception:
            pass

    async def queue(self, graph, stages, meta, last_output=False, titles=None, preview=False, spec=None):
        """The submission of the modes that still build their graph in the handler.

        Video goes through the job service (see `start_video_job`); the rest of the modes keep this
        path until stage 4 brings each of them over.
        """
        scenes = stages.pop("__scenes__", None)   # which sampler is which scene (long video), for the progress text
        rh = self.rh()
        if rh:
            return await self.queue_cloud(rh, graph, stages, meta, preview, spec=spec)
        await self.prepare_engine((meta or {}).get("mode", ""))
        status, res = await self.comfy_json("POST", "/prompt", json={
            "prompt": graph, "client_id": CLIENT_ID,
            "extra_data": {"preview_method": "auto"}})
        if status != 200:
            return web.json_response({"error": comfy_error_text(res)}, status=400)
        pid = res["prompt_id"]
        self.attach_job(self.new_record(spec, pid), meta=meta, last_output=last_output, titles=titles,
                        preview=preview, graph=graph, stages=stages, scenes=scenes)
        return web.json_response({"prompt_id": pid, "seed": meta["seed"], "queue_number": res.get("number")})

    async def start_video_job(self, spec, *, meta, last_output=False, titles=None, preview=False, key=None):
        """The one submission path of a video job: window and agent, with or without an idempotency key.

        Returns (record, deduplicated). The engine's answer is turned into a service error, never into
        an HTTP response, so both entrances map it their own way.
        """
        await self.prepare_engine(spec.mode)
        record, deduplicated = await self.service.submit_idempotent(
            spec, backend_id=self.backend_identity(), key=key)
        if not deduplicated:
            self.attach_job(record, meta=meta, last_output=last_output, titles=titles, preview=preview)
        return record, deduplicated

    async def need_models(self, mode):
        if self.rh():  # models live on RunningHub
            if not self.model_names:
                self.model_names = {r: catalog.VARIANTS[r][v]["name"] for r, v in self.choice().items()}
            return
        if mode not in self.modes():
            raise web.HTTPBadRequest(text="Этот режим не установлен. Добавьте его в разделе «Настройки».")
        if not self.model_names:
            await self.resolve_models()
        lost = [m["file"] for m in self.missing if m["role"] in catalog.MODE_ROLES[mode]]
        if lost:
            raise web.HTTPBadRequest(text="ComfyUI не видит модели: " + ", ".join(lost)
                                     + ". Откройте раздел «Настройки».")

    # ---------- the user's own workflows (API format), for those with their own ComfyUI ----------

    def wf_path(self, wid):
        if not wid.isalnum():
            raise web.HTTPNotFound()
        return WORKFLOWS_DIR / f"{wid}.json"

    def wf_load(self, wid):
        p = self.wf_path(wid)
        if not p.exists():
            raise web.HTTPNotFound(text="Workflow не найден")
        return json.loads(p.read_text(encoding="utf-8"))

    async def h_wf_list(self, _req):
        items = []
        for p in sorted(WORKFLOWS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True) if WORKFLOWS_DIR.exists() else []:
            try:
                items.append({"id": p.stem, "name": json.loads(p.read_text(encoding="utf-8")).get("name", p.stem)})
            except (OSError, ValueError):
                pass
        return web.json_response(items)

    async def h_wf_add(self, req):
        body = await req.json()
        graph = body.get("graph")
        try:
            customwf.validate_api(graph)
        except ValueError as e:
            raise web.HTTPBadRequest(text=str(e))
        WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
        wid = uuid.uuid4().hex[:10]
        name = (body.get("name") or "Мой workflow").strip()[:80]
        atomic_write_json(self.wf_path(wid), {"name": name, "graph": graph})
        return web.json_response({"id": wid, "name": name})

    async def h_wf_delete(self, req):
        p = self.wf_path(req.match_info["id"])
        p.unlink(missing_ok=True)
        return web.json_response({"ok": True})

    async def wf_infos(self, graph):
        """/object_info of every node class the workflow uses (a class ComfyUI does not know is left out)."""
        async def one(cls):
            try:
                status, data = await self.comfy_json("GET", f"/object_info/{cls}", timeout=aiohttp.ClientTimeout(total=15))
            except Exception:
                raise web.HTTPServiceUnavailable(text="ComfyUI не отвечает")
            return cls, (data[cls] if status == 200 and isinstance(data, dict) and cls in data else None)
        pairs = await asyncio.gather(*(one(c) for c in {n["class_type"] for n in graph.values()}))
        return {c: i for c, i in pairs if i is not None}

    async def h_wf_get(self, req):
        wf_file = self.wf_load(req.match_info["id"])
        report = customwf.analyze(wf_file["graph"], await self.wf_infos(wf_file["graph"]))
        return web.json_response({"id": req.match_info["id"], "name": wf_file["name"], **report})

    async def h_generate_custom(self, req):
        form, uploads = {}, {}
        async for field in await req.multipart():
            if field.name.startswith("f:") and field.filename:
                uploads[field.name[2:]] = await self.upload_input(field)
            else:
                form[field.name] = await field.text()
        wf_file = self.wf_load(form.get("wf", ""))
        graph = wf_file["graph"]
        report = customwf.analyze(graph, await self.wf_infos(graph))
        if report["missing_nodes"]:
            raise web.HTTPBadRequest(text="В вашем ComfyUI нет нод: " + ", ".join(sorted({m["cls"] for m in report["missing_nodes"]})))
        try:
            values = json.loads(form.get("values") or "{}")
        except ValueError:
            values = {}
        graph = customwf.apply_values(graph, values, uploads)
        await self.stop_llm()
        prompt = next((str(values.get(w["key"], w["value"])) for w in report["widgets"] if w["role"] == "prompt"), "")
        seed = next((values.get(w["key"], w["value"]) for w in report["widgets"] if w["role"] == "seed"), None)
        by_key = {w["key"]: w for w in report["widgets"]}
        inputs = []
        for key, comfy in uploads.items():   # a file the simple form of this workflow asked for
            w = by_key.get(key) or {}
            inputs.append(media_input(w.get("title") or w.get("name") or key, w.get("mkind") or "image", comfy=comfy))
        preview = form.get("preview") == "1"   # a result of a custom workflow waits for «Добавить в библиотеку»
        return await self.queue(graph, customwf.stage_map(graph), {
            "mode": "custom", "sub": wf_file["name"], "wf": form.get("wf"), "cw_values": values,
            "prompt": prompt, "aspect": "", "q": "", "len": 0.0, "res": "",
            "seed": seed if isinstance(seed, (int, float)) else None, "inputs": inputs}, last_output=True, preview=preview,
            titles={nid: (n.get("_meta") or {}).get("title", "") for nid, n in graph.items() if n["class_type"].startswith("Save")})

    async def read_image_form(self, req):
        """Multipart form of the picture sections: text fields plus one optional file `image`."""
        if self.rh():
            raise web.HTTPBadRequest(text="Изображения работают только на своём ComfyUI (не в облаке RunningHub).")
        form, image, extra, mask, crop = {}, None, [], None, None
        async for field in await req.multipart():
            if field.name == "image" and field.filename:
                image = await self.upload_input(field)
            elif field.name == "mask" and field.filename:
                mask = await self.upload_input(field)
            elif field.name == "crop" and field.filename:
                crop = await self.upload_input(field)
            elif field.name == "orig" and field.filename:
                form["_orig"] = await self.upload_input(field)
            elif field.name in ("image2", "image3") and field.filename:
                extra.append(await self.upload_input(field))
            else:
                form[field.name] = await field.text()
        form["_extra"] = extra
        if mask:
            form["_mask"] = mask
        if crop:
            form["_crop"] = crop
        seed = int(form["seed"]) if form.get("seed", "").isdigit() else random.randrange(2 ** 50)
        return form, image, seed

    async def h_upscale(self, req):
        """Improve the quality of a finished video or picture from the library (SeedVR2), the sound stays."""
        peek = await req.json()
        soft = peek.get("method") == "soft2k"  # "up to 2K", soft (pictures only)
        relay = peek.get("engine") == "relay"   # the fast RelayVSR x2 (video only) instead of SeedVR2
        await self.need_models("relayvsr" if relay else "upscale")
        if self.rh():
            raise web.HTTPBadRequest(text="Увеличение работает только на своём ComfyUI (не в облаке RunningHub).")
        body = peek
        item = self.find_item(body.get("id", ""))   # a Library picture, or a draft of the picture sections
        if not item:
            raise web.HTTPBadRequest(text="Файл не найден")
        if soft and item.get("kind") != "image":
            raise web.HTTPBadRequest(text="«До 2К» работает для изображений. Для видео выберите «Увеличить в 1,5 или 2 раза».")
        path = self.file_path(item)
        content = None
        if path and path.exists():
            content = path.read_bytes()
            suffix = path.suffix
        else:
            # output folder unknown (own ComfyUI without --output-directory): take the file from ComfyUI itself
            f = item["file"]
            params = {"filename": f.get("filename", ""), "subfolder": f.get("subfolder", ""), "type": f.get("type", "output")}
            try:
                async with self.session.get(self.comfy + "/view", params=params) as r:
                    if r.status == 200:
                        content = await r.read()
            except aiohttp.ClientError:
                content = None
            suffix = Path(f.get("filename", "")).suffix
        if not content:
            raise web.HTTPBadRequest(text="Файл не найден")
        return await self._upscale_core(body, item, content, suffix, relay, soft)

    async def h_upscale_file(self, req):
        """The same enlarge for ANY file of the user (made elsewhere): the file comes with the request, not from the library."""
        form, content, name = {}, None, ""
        async for part in await req.multipart():
            if part.name == "file":
                name = part.filename or ""
                content = await part.read()
            else:
                form[part.name] = (await part.text()).strip()
        if not content:
            raise web.HTTPBadRequest(text="Файл не получен")
        suffix = Path(name).suffix.lower()
        pic = suffix in (".png", ".jpg", ".jpeg", ".webp", ".bmp")
        relay = form.get("engine") == "relay"
        soft = form.get("method") == "soft2k"
        await self.need_models("relayvsr" if relay else "upscale")
        if self.rh():
            raise web.HTTPBadRequest(text="Увеличение работает только на своём ComfyUI (не в облаке RunningHub).")
        if soft and not pic:
            raise web.HTTPBadRequest(text="«До 2К» работает для изображений. Для видео выберите «Увеличить в 1,5 или 2 раза».")
        item = {"id": "", "kind": "image" if pic else "video", "res": form.get("res", ""), "len": float(form.get("len") or 0),
                "prompt": "Увеличение: " + name[:60], "aspect": "", "q": ""}
        return await self._upscale_core(form, item, content, suffix, relay, soft)

    async def _upscale_core(self, body, item, content, suffix, relay, soft):
        pic = item.get("kind") == "image"
        if relay:
            if pic:
                raise web.HTTPBadRequest(text="Быстрое увеличение работает только для видео. Для изображения выберите SeedVR2.")
            try:
                w0, h0 = (int(x) for x in str(item.get("res", "")).split("×"))
                secs = float(item.get("len") or 0)
            except ValueError:
                w0 = h0 = 0; secs = 0.0
            if w0 and secs and w0 * h0 * secs > wf.RELAY_PIXEL_SECONDS:   # decoded in memory as a whole: longer clips do not fit
                ok_secs = wf.RELAY_PIXEL_SECONDS / (w0 * h0)
                raise web.HTTPBadRequest(text=f"Видео слишком большое для быстрого увеличения: оно целиком загружается в память. "
                                              f"При размере {w0}×{h0} можно не больше {ok_secs:.0f} с (у вас {secs:.0f} с). "
                                              f"Выберите SeedVR2 или сделайте ролик короче.")
        scale = 2.0 if relay else wf.UPSCALE_SCALES.get(str(body.get("scale")), 1.5)
        data = aiohttp.FormData()
        name = f"h3s_{uuid.uuid4().hex[:8]}{suffix or ('.png' if pic else '.mp4')}"
        data.add_field("image", content, filename=name, content_type="image/png" if pic else "video/mp4")
        data.add_field("subfolder", "h3studio")
        data.add_field("type", "input")
        status, res = await self.comfy_json("POST", "/upload/image", data=data)
        if status != 200:
            raise web.HTTPBadGateway(text="Не удалось передать файл в ComfyUI")
        video = f'{res["subfolder"]}/{res["name"]}' if res.get("subfolder") else res["name"]
        src_file = {"filename": res["name"], "subfolder": res.get("subfolder", ""), "type": "input"}  # the "before" of the before/after slider
        src_input = media_input("Исходный файл", item.get("kind") or ("image" if pic else "video"), comfy=video)
        if item.get("res"):
            src_input["res"] = item["res"]
        seed = random.randrange(2 ** 40)
        try:
            w, h = (int(x) for x in str(item.get("res", "")).split("×"))
            res = f"{round(w * scale / 2) * 2}×{round(h * scale / 2) * 2}"
        except ValueError:
            res = ""
        if soft:  # long side 2048, SeedVR2 mixed half with the plain enlarge
            try:
                w0, h0 = (int(x) for x in str(item.get("res", "")).split("×"))
            except ValueError:
                raise web.HTTPBadRequest(text="Не знаю размер изображения, увеличить до 2К не получится.")
            scale = max(1.0, 2048 / max(w0, h0))
            graph, stages = wf.upscale_image_graph(self.model_names, video, scale, seed, soften=0.5)
            return await self.queue(graph, stages, {
                "mode": "upscale", "sub": "До 2К, мягко", "prompt": item.get("prompt", "") or "Увеличение до 2К",
                "aspect": item.get("aspect", ""), "q": "2K", "len": 0.0, "res": f"{round(w0 * scale / 2) * 2}×{round(h0 * scale / 2) * 2}", "seed": seed,
                "src": item.get("id", ""), "src_file": src_file, "inputs": [src_input]})
        def num(key, default):   # a slider value 0..1 from the window (a string when it comes with an uploaded file)
            try:
                return min(1.0, max(0.0, float(body.get(key))))
            except (TypeError, ValueError):
                return default
        if relay:
            graph, stages = wf.relay_graph(self.model_names, video, seed, detail=num("detail", 0.7))
        else:
            soften = round(1.0 - num("sharp", 1.0), 2)   # 0 = full sharpness of SeedVR2, more = mixed with the plain enlarge
            graph, stages = (wf.upscale_image_graph if pic else wf.upscale_graph)(self.model_names, video, scale, seed, soften=soften)
        return await self.queue(graph, stages, {
            "mode": "upscale", "sub": "Быстрое увеличение ×2 (RelayVSR)" if relay else f"Увеличение ×{body.get('scale', '1.5')}", "prompt": item.get("prompt", "") or "Увеличение",
            "aspect": item.get("aspect", ""), "q": item.get("q", ""), "len": item.get("len", 0.0), "res": res, "seed": seed,
            "src": item.get("id", ""), "src_file": src_file, "inputs": [src_input]})

    async def h_generate_krea(self, req):
        await self.need_models("krea")
        form, _, seed = await self.read_image_form(req)
        prompt = form.get("prompt", "").strip()
        if not prompt:
            raise web.HTTPBadRequest(text="Напишите промпт")
        preview = form.get("preview") == "1"   # from the picture sections: the Library waits for «Добавить в библиотеку»
        lab = {"label": form["batch_label"][:20]} if form.get("batch_label") else {}  # "3 из 8" for a series from a prompt list
        aspect = form.get("aspect", "16:9")
        if aspect not in wf.KREA_SIZES:
            raise web.HTTPBadRequest(text="Неизвестный формат")
        width, height = wf.KREA_SIZES[aspect]
        custom = round_size(form)
        if custom:
            width, height = custom
        engine = form.get("engine", "krea")
        if engine == "flux":
            await self.need_models("flux")
            graph, stages = wf.flux_t2i_graph(self.model_names, prompt, width, height, seed)
            return await self.queue(graph, stages, {
                "mode": "krea", "sub": "Изображение (FLUX.2)", "prompt": prompt, "aspect": aspect, "q": "1K",
                "len": 0.0, "res": f"{width}×{height}", "seed": seed, "inputs": [], **lab}, preview=preview)
        if engine == "qwen":
            await self.need_models("qwen")
            graph, stages = wf.qwen_t2i_graph(self.model_names, prompt, form.get("negative", "").strip()[:600], width, height, seed)
            return await self.queue(graph, stages, {
                "mode": "krea", "sub": "Изображение (Qwen 2.1)", "prompt": prompt, "aspect": aspect, "q": "1K",
                "len": 0.0, "res": f"{width}×{height}", "seed": seed, "inputs": [], **lab}, preview=preview)
        if form.get("style") == "real":
            await self.need_models("kreareal")
            scale = wf.KREA_REAL_SCALES.get(form.get("scale", "2k"), 1.5)
            graph, stages = wf.krea_real_graph(self.model_names, prompt, width, height, seed, scale)
            return await self.queue(graph, stages, {
                "mode": "krea", "sub": "Изображение: реализм", "prompt": prompt, "aspect": aspect, "q": "4K" if scale > 2 else "2K",
                "len": 0.0, "res": f"{int(width * scale)}×{int(height * scale)}", "seed": seed, "inputs": [], **lab}, preview=preview)
        graph, stages = wf.krea_graph(self.model_names, prompt, width, height, seed)
        return await self.queue(graph, stages, {
            "mode": "krea", "sub": "Изображение по тексту", "prompt": prompt, "aspect": aspect, "q": "1K",
            "len": 0.0, "res": f"{width}×{height}", "seed": seed, "inputs": [], **lab}, preview=preview)

    async def h_generate_qwen(self, req):
        form, image, seed = await self.read_image_form(req)
        preview = form.get("preview") == "1"   # from the picture sections: the Library waits for «Добавить в библиотеку»
        flux = form.get("kind") != "sheet" and form.get("engine") == "flux"
        await self.need_models("flux" if flux else "qwen")

        def pics(main_role):
            out = [media_input(main_role, "image", comfy=image)] if image else []
            out += [media_input(f"Дополнительное изображение {i + 2}", "image", comfy=n)
                    for i, n in enumerate(form.get("_extra") or [])]
            return out
        if form.get("kind") == "bg":   # one click: the background becomes plain white, the subject stays untouched
            if not image:
                raise web.HTTPBadRequest(text="Загрузите изображение")
            mp = wf.QWEN_MP.get(form.get("size", "1"), 1.0)
            graph, stages = wf.qwen_edit_graph(self.model_names, wf.BG_REMOVE_PROMPT, image, mp, seed)
            return await self.queue(graph, stages, {
                "mode": "qwen", "sub": "Убрать фон", "prompt": "", "aspect": "",
                "q": "", "len": 0.0, "res": "", "seed": seed}, preview=preview)
        if form.get("kind") == "outpaint":   # extend the picture: client builds the larger canvas and the offset
            if not image or not form.get("_orig"):
                raise web.HTTPBadRequest(text="Загрузите изображение")
            try:
                w, h, x, y = (max(0, int(form.get(k, "0"))) for k in ("width", "height", "x", "y"))
            except ValueError:
                raise web.HTTPBadRequest(text="Неверный размер холста")
            if not (320 <= w <= 4096 and 320 <= h <= 4096):
                raise web.HTTPBadRequest(text="Слишком большой или маленький холст")
            prompt = form.get("prompt", "").strip() or wf.OUTPAINT_PROMPT
            lora = None
            try:
                _, loras = await self.comfy_json("GET", "/models/loras", timeout=aiohttp.ClientTimeout(total=15))
                lora = next((f for f in (loras or []) if f.lower().endswith("qwen-image-2.1-outpaint-v2.safetensors")), None)
            except Exception:
                pass
            graph, stages = wf.qwen_outpaint_graph(self.model_names, prompt, image, form["_orig"], x, y, (w, h), seed, lora)
            return await self.queue(graph, stages, {
                "mode": "qwen", "sub": "Расширение изображения", "prompt": prompt, "aspect": "",
                "q": "", "len": 0.0, "res": f"{w}×{h}", "seed": seed}, preview=preview)
        if form.get("kind") == "story" and form.get("frames") == "1":
            # storyboard frame by frame (FLUX.2 klein): every frame is its own picture, the sheet is glued from them
            use_qwen = form.get("engine") == "qwen"
            await self.need_models("qwen" if use_qwen else "flux")
            frames = split_frames(form.get("prompt", ""))
            if len(frames) not in wf.FRAME_COLS:
                raise web.HTTPBadRequest(text=f"Нужно 2, 3, 4, 6, 8 или 9 кадров, а в описании их {len(frames)}. Напишите каждый кадр с новой строки: «1: ...», «2: ...».")
            refs = [x for x in ([image] if image else []) + list(form.get("_extra") or [])]
            build = wf.qwen_frames_graph if use_qwen else wf.flux_frames_graph
            graph, stages, titles = build(self.model_names, refs, frames, seed, size=round_size(form) or wf.FRAME_SIZE, line=form.get("line", "white"))
            return await self.queue(graph, stages, {
                "mode": "qwen", "sub": "Раскадровка по кадрам", "prompt": form.get("prompt", "").strip(), "aspect": "",
                "q": "", "len": 0.0, "res": "", "seed": seed, "inputs": pics("Референс персонажа")}, titles=titles, preview=preview)
        if not image:
            raise web.HTTPBadRequest(text="Загрузите изображение")
        if form.get("kind") == "story":
            prompt = form.get("prompt", "").strip()
            if not prompt:
                raise web.HTTPBadRequest(text="Опишите историю по кадрам")
            refs = [image] + list(form.get("_extra") or [])
            sz = round_size(form) or wf.STORY_SIZE
            if flux:
                graph, stages = wf.flux_story_graph(self.model_names, refs, prompt, seed, sz)
            else:
                graph, stages = wf.qwen_story_graph(self.model_names, refs, prompt, seed, sz)
            return await self.queue(graph, stages, {
                "mode": "qwen", "sub": "Раскадровка по изображениям", "prompt": prompt, "aspect": "",
                "q": "", "len": 0.0, "res": "%d×%d" % wf.STORY_SIZE, "seed": seed, "inputs": pics("Главный герой")}, preview=preview)
        if flux:
            prompt = form.get("prompt", "").strip()
            if not prompt:
                raise web.HTTPBadRequest(text="Напишите, что изменить на изображении")
            mp = wf.QWEN_MP.get(form.get("size", "1"), 1.0)
            graph, stages = wf.flux_edit_graph(self.model_names, prompt, image, mp, seed, form.get("_extra") or (), round_size(form))
            return await self.queue(graph, stages, {
                "mode": "qwen", "sub": "Правка изображения (FLUX.2)", "prompt": prompt, "aspect": "",
                "q": "", "len": 0.0, "res": "", "seed": seed, "inputs": pics("Исходное изображение")}, preview=preview)
        if form.get("kind") == "sheet":
            panels = 6 if form.get("panels") == "6" else 4
            graph, stages, prompt = wf.qwen_sheet_graph(
                self.model_names, image, seed, headless=form.get("headless", "1") != "0",
                background=form.get("background", "grey"), marks=form.get("marks", "")[:300], panels=panels)
            sub, res = "Лист персонажа", "%d×%d" % (wf.SHEET6_SIZE if panels == 6 else wf.SHEET_SIZE)
            prompt = "Лист персонажа" + (" (6 панелей)" if panels == 6 else "") + (": " + form["marks"].strip()[:80] if form.get("marks", "").strip() else "")
        else:
            prompt = form.get("prompt", "").strip()
            if not prompt:
                raise web.HTTPBadRequest(text="Напишите, что изменить на изображении")
            mp = wf.QWEN_MP.get(form.get("size", "1"), 1.0)
            size = round_size(form) or wf.qwen_edit_size(form.get("aspect", ""), mp)
            graph, stages = wf.qwen_edit_graph(self.model_names, prompt, image, mp, seed, size, form.get("_extra") or ())
            sub, res = "Правка изображения", ("%d×%d" % size if size else "")
        inputs = ([media_input("Фото персонажа", "image", comfy=image)] if form.get("kind") == "sheet" and image
                  else pics("Исходное изображение"))
        return await self.queue(graph, stages, {
            "mode": "qwen", "sub": sub, "prompt": prompt, "aspect": form.get("aspect", "") if form.get("kind") != "sheet" else "",
            "q": "", "len": 0.0, "res": res, "seed": seed, "inputs": inputs}, preview=preview)

    async def fused_model(self):
        """The Fused Turbo checkpoint for the Video section, or None when its file is not in this ComfyUI."""
        await self.resolve_models()  # the file may have just been downloaded: ask ComfyUI again, not the old list
        return self.model_names.get("fused_unet") if "fused_unet" in self.found else None

    async def h_generate_inpaint(self, req):
        form, image, seed = await self.read_image_form(req)
        preview = form.get("preview") == "1"   # the Library waits for «Добавить в библиотеку»
        await self.need_models("qwen")
        if not image:
            raise web.HTTPBadRequest(text="Загрузите изображение")
        if not form.get("_mask") or not form.get("_crop"):
            raise web.HTTPBadRequest(text="Закрасьте кисточкой место, которое нужно переделать")
        prompt = form.get("prompt", "").strip()
        if not prompt:
            raise web.HTTPBadRequest(text="Напишите, что должно быть в закрашенном месте")
        def whole(key):
            """A whole number out of the form. The browser can send "123.4" (mouse coordinates are fractional):
            it must be rounded, never dropped — a dropped offset used to paste the crop at the corner."""
            try:
                return int(round(float(form.get(key) or 0)))
            except (TypeError, ValueError):
                return 0
        x, y = whole("x"), whole("y")
        w, h = whole("crop_w"), whole("crop_h")   # the crop's own size: the result comes back to it, and it caps the model's scale
        size = (w, h) if w > 0 and h > 0 else None
        mp = wf.QWEN_MP.get(form.get("size", "1"), 1.0)
        graph, stages = wf.qwen_inpaint_graph(self.model_names, prompt, image, form["_crop"], form["_mask"],
                                              x, y, mp, seed, size)
        return await self.queue(graph, stages, {
            "mode": "qwen", "sub": "Инпейнт", "prompt": prompt, "aspect": "",
            "q": "", "len": 0.0, "res": "", "seed": seed}, preview=preview)

    async def h_generate_video(self, req):
        await self.need_models("video")
        form, images, prompt, aspect, quality, seed = await self.read_form(req, ("first", "last"))
        preview = form.get("preview") == "1"   # from the video sections: the Library waits for «Добавить в библиотеку»
        width, height = wf.resolution(aspect, quality, form)
        first, last = images.get("first"), images.get("last") if images.get("first") else None
        loop = bool(first) and form.get("loop") == "1"
        if loop:  # seamless loop (live wallpaper): the video ends on the same picture it starts with
            last = first

        fused = None
        fast = None
        if form.get("unet") == "fused":  # one 21 GB file with its turbo LoRA baked in: 4 steps, no extra file
            if self.rh():
                raise web.HTTPBadRequest(text="Fused Turbo работает только на этом компьютере: в облаке RunningHub "
                                              "доступна обычная модель. Выберите «FastH3» или выключите облако.")
            fused = await self.fused_model()
            if not fused:
                raise web.HTTPBadRequest(text="Для модели Fused Turbo нужен её файл (около 21 ГБ). Откройте «Модели» "
                                              "и нажмите «Скачать» в строке «Fused Turbo» или выберите «FastH3».")
        elif form.get("accel") == "sla":
            miss = {mm["role"] for mm in self.missing}
            if "sla_lora" in miss or "sla_lora" not in self.model_names:
                raise web.HTTPBadRequest(text="Для быстрого режима нужен файл Turbo-SLA (около 2 ГБ). Нажмите «Скачать» под переключателем «Ускорение» "
                                              "или выберите «Обычное».")
            fast = self.model_names["sla_lora"]
        sub = "Зацикленное видео" if loop else "Кадр → кадр" if last else "Изображение → видео" if first else "Текст → видео"
        if fused:
            sub += " · Fused Turbo"
        elif fast:
            sub += " · быстрый черновик"
        refs = []
        if first:
            refs.append(InputRef(role="Первый кадр" + (" · зациклено" if loop else ""), kind="image",
                                 comfy=first, key="first_frame"))
        if last and last != first:
            refs.append(InputRef(role="Последний кадр", kind="image", comfy=last, key="last_frame"))
        try:
            # the request is described once, here, and the same description is what the agent API builds:
            # the duration is clamped and checked in JobSpec, not in each entrance
            spec = JobSpec.video(prompt=prompt, aspect=aspect, quality=quality, seconds=form.get("duration", 5),
                                 seed=seed, width=width, height=height, models=dict(self.model_names),
                                 loras=self.custom_loras("video"), inputs=tuple(refs),
                                 model="fused" if fused else "fast", acceleration="sla" if fast else "normal",
                                 result_policy=ResultPolicy.DRAFT if preview else ResultPolicy.LIBRARY,
                                 source="ui", sub=sub)
        except InvalidSpec as e:
            raise web.HTTPBadRequest(text=e.message)
        inputs = []
        if first:
            inputs.append(media_input("Первый кадр" + (" · зациклено" if loop else ""), "image", comfy=first))
        if last and last != first:
            inputs.append(media_input("Последний кадр", "image", comfy=last))
        meta = {"mode": "video", "sub": spec.sub, "prompt": spec.prompt, "aspect": spec.aspect, "q": spec.quality,
                "len": spec.seconds, "res": f"{spec.width}×{spec.height}", "seed": spec.seed, "inputs": inputs}
        try:
            record, _ = await self.start_video_job(spec, meta=meta, preview=preview)
        except SubmitRejected as e:
            raise web.HTTPBadRequest(text=str(e))
        except BackendUnavailable as e:
            raise web.HTTPServiceUnavailable(text=(
                f"{e}. Задание записано как неопределённое: проверьте очередь ComfyUI, прежде чем повторять — "
                "оно могло уйти."))
        except JobConflict as e:
            raise web.HTTPConflict(text=e.message)
        return web.json_response({"prompt_id": record.prompt_id, "seed": spec.seed,
                                  "queue_number": (record.legacy or {}).get("number")})

    async def h_generate_lipsync(self, req):
        await self.need_models("lipsync")
        ref_fields = tuple(f"ref{i}" for i in range(9))
        form, files, prompt, aspect, quality, seed = await self.read_form(req, ref_fields + ("audio",))
        preview = form.get("preview") == "1"   # from the video sections: the Library waits for «Добавить в библиотеку»
        if "audio" not in files:
            raise web.HTTPBadRequest(text="Загрузите аудио")
        refs = [files[f] for f in ref_fields if f in files]
        if not refs:
            raise web.HTTPBadRequest(text="Добавьте хотя бы один референс — лицо персонажа")
        seconds = float(form.get("audio_len", 0))
        if not 1 <= seconds <= 15:
            raise web.HTTPBadRequest(text="Фрагмент аудио должен быть от 1 до 15 секунд")

        ref_size = form.get("ref_size") if form.get("ref_size") in ("match", "max") else "max"
        scale = wf.lipsync_scale(quality)
        out_w, out_h = wf.resolution(aspect, quality, form)
        width, height = wf.r32(out_w / scale), wf.r32(out_h / scale)
        extra = self.optional_loras(form, (("realism", "realism_lora"),)) + self.custom_loras("lipsync")
        graph, stages = wf.lipsync_graph(self.model_names, wf.lipsync_prompt(prompt, len(refs)), width, height,
                                         seconds, seed, files["audio"], refs, scale, ref_size, extra)
        speed = "максимум" if ref_size == "max" else "быстро"
        inputs = [media_input(f"Референс {i + 1}", "image", comfy=name) for i, name in enumerate(refs)]
        inputs.append(media_input("Аудио для губ", "audio", comfy=files["audio"]))
        return await self.queue(graph, stages, {
            "mode": "lipsync", "sub": f"{len(refs)} реф. ({speed}) · аудио {seconds:.1f} с", "prompt": prompt,
            "aspect": aspect, "q": quality, "len": seconds, "res": f"{out_w}×{out_h}", "seed": seed,
            "ref_size": ref_size, "inputs": inputs}, preview=preview)

    async def read_ref_form(self, req, max_images):
        fields = tuple(f"img{i}" for i in range(max_images)) + tuple(f"vid{i}" for i in range(3)) + \
            tuple(f"aud{i}" for i in range(3))
        form, files, prompt, aspect, quality, seed = await self.read_form(req, fields)
        pick = lambda p, n: [files[f"{p}{i}"] for i in range(n) if f"{p}{i}" in files]
        images, videos, audios = pick("img", max_images), pick("vid", 3), pick("aud", 3)
        if not images and not videos:
            raise web.HTTPBadRequest(text="Добавьте хотя бы одно референс-изображение")
        if len(images) + len(videos) + len(audios) > 12:
            raise web.HTTPBadRequest(text="Всего не больше 12 референсов (изображения + видео + аудио)")
        seconds = min(MAX_SECONDS, max(3.0, float(form.get("duration", 6))))
        return form, prompt, aspect, quality, seed, images, videos, audios, seconds

    def custom_loras(self, mode=None):
        """(file, strength) for the LoRAs the user added by hand on the Models screen (not used with the cloud).
        A LoRA can be switched off in single modes (right in the mode, in the «Модель» row): its `skip` lists those modes."""
        if self.rh():
            return []
        return [(x["file"], float(x["strength"])) for x in self.unique_loras() if x.get("on", True) and mode not in (x.get("skip") or [])]

    def unique_loras(self):
        """The user's own LoRAs, one entry per file (older versions let one file be added several times: it was mixed in again and again)."""
        seen, out = set(), []
        for x in self.config.get("custom_loras") or []:
            key = str(x.get("file") or "").replace("/", "\\").lower()
            if key and key not in seen:
                seen.add(key)
                out.append(x)
        return out

    async def h_loras_custom(self, _req):
        try:
            _, files = await self.comfy_json("GET", "/models/loras")
        except Exception:
            raise web.HTTPServiceUnavailable(text="ComfyUI не отвечает")
        items = self.unique_loras()
        if len(items) != len(self.config.get("custom_loras") or []):
            self.config.update(custom_loras=items)   # the doubles are removed for good
        return web.json_response({"files": files or [], "items": items})

    async def h_loras_custom_set(self, req):
        items = []
        for x in (await req.json()).get("items", [])[:10]:
            try:
                strength = max(-4.0, min(4.0, float(x.get("strength", 0.8))))
            except (TypeError, ValueError):
                strength = 0.8
            if isinstance(x.get("file"), str) and safe_lora_name(x.get("file")):
                skip = [m for m in (x.get("skip") or []) if m in catalog.MODE_ROLES][:20]
                items.append({"file": safe_lora_name(x["file"]), "strength": strength, "on": bool(x.get("on", True)), "skip": skip})
        self.config.update(custom_loras=items)
        return web.json_response({"ok": True})

    async def h_loras_custom_mode(self, req):
        """Switch an own LoRA on / off for ONE mode (from the «Модель» row of that mode)."""
        body = await req.json()
        file, mode = body.get("file"), body.get("mode")
        if mode not in catalog.MODE_ROLES:
            raise web.HTTPBadRequest(text="Неизвестный режим")
        items = self.unique_loras()
        hit = next((x for x in items if x.get("file") == file), None)
        if not hit:
            raise web.HTTPBadRequest(text="Такой LoRA нет в списке")
        skip = [m for m in (hit.get("skip") or []) if m != mode]
        if not body.get("on"):
            skip.append(mode)
        hit["skip"] = skip
        self.config.update(custom_loras=items)
        return web.json_response({"ok": True})

    # ---------- LoRA catalog: what a LoRA means, and where to get more ----------
    # The models screen only lists file names. Here a LoRA gets a human name, trigger words and a
    # suggested weight (from a small built-in list, the .safetensors header, or the user's own edits),
    # conflicting combinations are flagged, and Civitai / Hugging Face can be searched and downloaded
    # straight into the LoRA folder of the ComfyUI in use.

    def loras_folder(self, folder=""):
        """The LoRA folder of the ComfyUI in use (created for our own install); None when it is unknown."""
        managed = self.config.get("engine") == "managed"
        if managed:
            base = Path(self.config.get("install_dir") or "") / catalog.COMFY_DIRNAME / "ComfyUI" / "models"
        else:
            raw = str(folder or self.config.get("ext_models_dir") or "").strip()
            if not raw:
                return None            # the user's own ComfyUI: its models folder is chosen on the Models screen
            base = Path(raw)
        p = base / "loras"
        if managed:
            p.mkdir(parents=True, exist_ok=True)
        return p

    def lora_meta(self):
        return self.config.get("lora_meta") or {}

    def lora_conflicts(self, meta=None):
        active = [x for x in self.unique_loras() if x.get("on", True)]
        return loras_registry.analyze_lora_conflicts(active, custom_meta=self.lora_meta() if meta is None else meta)

    async def h_loras_registry(self, _req):
        """Every LoRA ComfyUI sees, with its meaning, trigger words and suggested weight."""
        try:
            _, files = await self.comfy_json("GET", "/models/loras", timeout=aiohttp.ClientTimeout(total=20))
        except Exception:
            files = []
        meta = self.lora_meta()
        root = self.loras_folder()
        mine = {x.get("file") for x in self.unique_loras()}
        items = []
        for f in files or []:
            full = root / f if root and (root / f).is_file() else None
            m = loras_registry.get_lora_metadata(f, full_file_path=str(full) if full else None, custom_meta=meta)
            items.append({**m, "mine": f in mine})
        return web.json_response({"items": items, "conflicts": self.lora_conflicts(meta), "dir": str(root or "")})

    async def h_loras_conflicts(self, _req):
        return web.json_response({"conflicts": self.lora_conflicts()})

    async def h_loras_meta_save(self, req):
        """The user's own name, description, trigger words and weight for a LoRA file."""
        body = await req.json()
        file = safe_lora_name(body.get("file"))
        if not file:
            raise web.HTTPBadRequest(text="Неизвестная LoRA")
        meta = dict(self.lora_meta())
        entry = dict(meta.get(file) or {})
        for key, limit in (("name", 120), ("desc", 400), ("category", 60)):
            if key in body:
                value = str(body.get(key) or "").strip()[:limit]
                if value:
                    entry[key] = value
                else:
                    entry.pop(key, None)
        if "triggers" in body:
            raw = body["triggers"]
            parts = raw if isinstance(raw, list) else str(raw).replace(";", ",").split(",")
            triggers = [str(x).strip()[:80] for x in parts if str(x).strip()][:20]
            if triggers:
                entry["triggers"] = triggers
            else:
                entry.pop("triggers", None)
        if "recommended_weight" in body:
            try:
                entry["recommended_weight"] = max(0.0, min(2.0, float(body["recommended_weight"])))
            except (TypeError, ValueError):
                raise web.HTTPBadRequest(text="Вес должен быть числом от 0 до 2")
        if entry:
            meta[file] = entry
        else:
            meta.pop(file, None)
        self.config.update(lora_meta=meta)
        return web.json_response({"ok": True, "meta": entry})

    async def h_loras_tokens_get(self, _req):
        """Whether an access token is saved (the token itself never comes back to the page)."""
        return web.json_response({"civitai": bool((self.config.get("civitai_token") or "").strip()),
                                  "hf": bool((self.config.get("hf_token") or "").strip())})

    async def h_loras_tokens_save(self, req):
        body = await req.json()
        upd = {}
        for key, cfg in (("civitai", "civitai_token"), ("hf", "hf_token")):
            if key in body:
                upd[cfg] = str(body.get(key) or "").strip()[:200]
        if upd:
            self.config.update(**upd)
        return web.json_response({"ok": True})

    def civitai_token(self):
        return (self.config.get("civitai_token") or "").strip()

    def hf_token(self):
        return (self.config.get("hf_token") or "").strip()

    async def h_civitai_search(self, req):
        try:
            page = max(1, int(req.query.get("page") or 1))
        except ValueError:
            page = 1
        res = await civitai_client.search_loras(self.session, query=req.query.get("q", "").strip(), page=page,
                                                limit=20, token=self.civitai_token())
        return web.json_response(res)

    async def h_civitai_resolve(self, req):
        try:
            res = await civitai_client.resolve_url_or_id(self.session, str((await req.json()).get("text") or ""))
        except Exception as ex:  # noqa: BLE001 - the message is what the user needs
            raise web.HTTPBadRequest(text=str(ex)[:300])
        return web.json_response(res)

    async def h_hf_search(self, req):
        res = await hf_client.search_loras(self.session, query=req.query.get("q", "").strip(), limit=30,
                                           token=self.hf_token())
        return web.json_response(res)

    async def h_hf_resolve(self, req):
        try:
            res = await hf_client.resolve_url_or_repo(self.session, str((await req.json()).get("text") or ""),
                                                      token=self.hf_token())
        except Exception as ex:  # noqa: BLE001
            raise web.HTTPBadRequest(text=str(ex)[:300])
        return web.json_response(res)

    async def h_loras_download(self, req):
        """Download a LoRA file into the LoRA folder of ComfyUI, with progress in the UI."""
        body = await req.json()
        url = str(body.get("url") or "").strip()
        if not url.startswith("https://"):
            raise web.HTTPBadRequest(text="Скачивать можно только по https")
        name = safe_lora_name(body.get("filename") or "")
        if not name:
            name = f"lora_{uuid.uuid4().hex[:8]}.safetensors"
        if not name.lower().endswith(".safetensors"):
            name += ".safetensors"
        if self.lora_dl and not self.lora_dl.get("done"):
            raise web.HTTPConflict(text="Скачивание уже идёт: дождитесь его или остановите")
        folder = self.loras_folder(str(body.get("folder") or ""))
        if not folder or not folder.parent.is_dir():
            raise web.HTTPBadRequest(text="Не найдена папка models вашего ComfyUI: укажите её в разделе «Модели»")
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / name
        if dest.exists():
            raise web.HTTPConflict(text=f"Файл «{name}» уже есть в папке LoRA")
        host = urllib.parse.urlsplit(url).hostname or ""
        token = self.civitai_token() if "civitai" in host else self.hf_token() if "huggingface" in host or host.endswith("hf.co") else ""
        state = {"done": False, "error": None, "name": name, "task": None}
        self.lora_dl = state

        async def work():
            dl = None
            try:
                dl = asyncio.create_task(civitai_client.download_file_stream(self.session, url, str(dest), token))
                state["task"] = dl
                while not dl.done():
                    await asyncio.sleep(0.5)
                    cur = civitai_client.CURRENT_DOWNLOAD
                    await self.broadcast({"type": "loradl", "name": name, "done_bytes": cur.get("done"),
                                          "total": cur.get("total"), "percent": cur.get("percent"),
                                          "speed": cur.get("speed")})
                await dl
                await self.broadcast({"type": "loradl", "name": name, "percent": 100, "finished": True})
            except asyncio.CancelledError:
                # the child download is this task's own: it is stopped and awaited before the file is
                # removed, or it would keep writing into a deleted file (and raise into nobody)
                if dl is not None and not dl.done():
                    dl.cancel()
                    with contextlib.suppress(BaseException):
                        await dl
                dest.unlink(missing_ok=True)
                await self.broadcast({"type": "loradl", "name": name, "cancelled": True})
            except Exception as ex:  # noqa: BLE001
                dest.unlink(missing_ok=True)
                await self.broadcast({"type": "loradl", "name": name, "error": str(ex)[:300]})
            finally:
                state["done"] = True

        self.lora_dl_task = self.tasks.spawn("lora", work())
        return web.json_response({"ok": True, "name": name, "dir": str(folder)})

    async def h_loras_download_cancel(self, _req):
        dl = (self.lora_dl or {}).get("task")
        if dl and not dl.done():
            dl.cancel()
            return web.json_response({"ok": True})
        return web.json_response({"ok": False})

    async def h_loras_open_folder(self, _req):
        folder = self.loras_folder()
        if not folder or not folder.parent.is_dir():
            raise web.HTTPBadRequest(text="Не найдена папка models вашего ComfyUI")
        folder.mkdir(parents=True, exist_ok=True)
        open_in_explorer(folder)
        return web.json_response({"ok": True, "dir": str(folder)})

    def optional_loras(self, form, pairs):
        """(file name, strength) for the optional LoRAs the user switched on and this ComfyUI has."""
        missing = {m["role"] for m in self.missing}
        out = []
        for key, role in pairs:
            v = lora_strength(form, key, None)
            if v is not None and role not in missing and role in self.model_names:
                out.append((self.model_names[role], v))
        return out

    async def h_generate_refs(self, req):
        await self.need_models("refs")
        form, prompt, aspect, quality, seed, images, videos, audios, seconds = await self.read_ref_form(req, 9)
        preview = form.get("preview") == "1"   # from the video sections: the Library waits for «Добавить в библиотеку»
        steps = int(form.get("steps", 6)) if form.get("steps", "6").isdigit() else 6
        steps = min(8, max(4, steps))
        ref_size = form.get("ref_size") if form.get("ref_size") in ("match", "max") else "max"
        extra = self.optional_loras(form, (("realism", "realism_lora"), ("motion", "motion_repair_lora"))) + self.custom_loras("refs")
        width, height = wf.resolution(aspect, quality, form)
        fused = None
        pika = None
        if form.get("unet") == "fused":  # the fused checkpoint does references too: no LoRA, its own 4-step recipe
            if self.rh():
                raise web.HTTPBadRequest(text="Fused Turbo работает только на этом компьютере: в облаке RunningHub "
                                              "доступна обычная модель. Выберите «Singularity» или выключите облако.")
            fused = await self.fused_model()
            if not fused:
                raise web.HTTPBadRequest(text="Для модели Fused Turbo нужен её файл (около 21 ГБ). Откройте «Модели» "
                                              "и нажмите «Скачать» в строке «Fused Turbo» или выберите «Singularity».")
            steps = 4
        elif form.get("accel") == "pika":
            if "pika_lora" in {m["role"] for m in self.missing}:
                raise web.HTTPBadRequest(text="Файл Pika (pika_h3_flex_turbo_8step_hd_ref2va) не найден в ComfyUI. "
                                              "Скачайте его в разделе «Настройки» или выберите прежнее ускорение.")
            pika, steps = self.model_names["pika_lora"], max(6, steps)
        graph, stages = wf.ref_graph(self.model_names, wf.lipsync_prompt(prompt, len(images), len(videos)), width, height, seconds, seed, images, videos, audios,
                                     steps, ref_size, extra, pika, fused)
        sub = f"{len(images)} карт." + (f", {len(videos)} видео" if videos else "") + (f", {len(audios)} аудио" if audios else "") \
            + f" · {steps} шагов" + (" · Fused Turbo" if fused else " · Pika" if pika else "")
        inputs = [media_input(f"Референс-изображение {i + 1}", "image", comfy=name) for i, name in enumerate(images)]
        inputs += [media_input(f"Референс-видео {i + 1}", "video", comfy=name) for i, name in enumerate(videos)]
        inputs += [media_input(f"Аудио {i + 1}", "audio", comfy=name) for i, name in enumerate(audios)]
        return await self.queue(graph, stages, {
            "mode": "refs", "sub": sub, "prompt": prompt, "aspect": aspect, "q": quality, "len": seconds,
            "res": f"{width}×{height}", "seed": seed, "steps": steps, "ref_size": ref_size, "inputs": inputs}, preview=preview)

    async def long_source(self, item_id, uploaded, first_sec):
        """A video the user already has (from the library, or a file from the computer) to continue from: put it into ComfyUI and
        find out how many frames it has. Returns (file name in ComfyUI input, frames)."""
        if uploaded:
            name = uploaded   # read_form already uploaded it to ComfyUI
        else:
            it = self.find_item(item_id)   # a Library clip, or a clip that is still a draft
            if not it or it.get("kind") == "image":
                raise web.HTTPBadRequest(text="Видео для продолжения не найдено в Библиотеке")
            data = await self.item_bytes(it)
            fd = aiohttp.FormData()
            nm = f"h3s_c_{uuid.uuid4().hex[:10]}.{it.get('ext') or 'mp4'}"
            fd.add_field("image", data, filename=nm, content_type="video/mp4")
            fd.add_field("type", "input")
            status, res = await self.comfy_json("POST", "/upload/image", data=fd)
            if status != 200:
                raise web.HTTPBadGateway(text="Не удалось передать видео в ComfyUI")
            name = res["name"]
        # how long is it: a tiny job that only opens the video and reports its number of frames
        probe = {"L": {"class_type": "LoadVideo", "inputs": {"file": name}},
                 "G": {"class_type": "GetVideoComponents", "inputs": {"video": ["L", 0]}},
                 "Z": {"class_type": "GetImageSize", "inputs": {"image": ["G", 0]}},
                 "N": {"class_type": "PreviewAny", "inputs": {"source": ["Z", 2]}},
                 "F": {"class_type": "PreviewAny", "inputs": {"source": ["G", 2]}}}
        status, res = await self.comfy_json("POST", "/prompt", json={"prompt": probe, "client_id": CLIENT_ID})
        if status != 200:
            raise web.HTTPBadRequest(text="ComfyUI не смог открыть это видео: " + comfy_error_text(res))
        pid = res["prompt_id"]
        loop = asyncio.get_running_loop()
        end = loop.time() + 180
        meta = None
        while loop.time() < end and not meta:
            await asyncio.sleep(0.7)
            _, hist = await self.comfy_json("GET", f"/history/{pid}")
            e = (hist or {}).get(pid)
            if e:
                if e.get("status", {}).get("status_str") == "error":
                    break
                try:
                    meta = {"frame_count": int(float(e["outputs"]["N"]["text"][0])), "fps": float(e["outputs"]["F"]["text"][0])}
                except (KeyError, IndexError, ValueError):
                    break
        if not meta:
            raise web.HTTPBadRequest(text="Не удалось прочитать это видео. Подойдёт обычный mp4.")
        frames = int(meta["frame_count"])
        need = wf.long_plan([first_sec], cont_first=True)[0]["ov"]
        if frames < need:
            raise web.HTTPBadRequest(text=f"Видео слишком короткое для продолжения: в нём {frames} кадров, нужно не меньше {need} (около 2,5 секунд).")
        return (name, frames)

    async def preview_opts(self):
        """Settings for the animated live preview, or None when ComfyUI has no KJNodes' Model Preview Override."""
        return None   # switched off: the animated preview made generation noticeably slower; the usual single-frame preview is used
        try:
            status, oi = await self.comfy_json("GET", "/object_info/ModelPreviewOverrideKJ")
        except Exception:
            return None
        node = (oi or {}).get("ModelPreviewOverrideKJ") if status == 200 else None
        if not node:
            return None
        tiny = "none"
        try:
            spec = node["input"].get("optional", {}).get("tiny_vae") or []
            options = (spec[1] or {}).get("options") if len(spec) > 1 and isinstance(spec[1], dict) else spec[0]
            tiny = next((o for o in options or [] if o.replace("\\", "/").split("/")[-1].startswith("taeh3")), "none")
        except Exception:
            pass
        return {"tiny_vae": tiny}

    def keep_long_refs(self, run, captured):
        """The reference pictures (and the sound) of a long video project, next to its saved scene states."""
        if not run:
            return
        d = LONG_DIR / run / "refs"
        d.mkdir(parents=True, exist_ok=True)
        for key, (ext, raw) in captured.items():
            if key.startswith(("img", "aud", "simg")) and len(raw) < 80_000_000:
                for old in d.glob(key + ".*"):
                    old.unlink(missing_ok=True)
                (d / (key + ext)).write_bytes(raw)

    async def h_long_project(self, req):
        """The project of a long video (scenes, settings) is kept on disk too, so it survives anything that happens to the browser storage."""
        if req.method == "POST":
            body = await req.json()
            run = re.sub(r"[^A-Za-z0-9]", "", str(body.get("run", "")))[:32]
            if not run or not isinstance(body.get("project"), dict):
                raise web.HTTPBadRequest(text="bad project")
            d = LONG_DIR / run
            d.mkdir(parents=True, exist_ok=True)
            atomic_write_json(d / "project.json", body["project"])
            return web.json_response({"ok": True})
        run = re.sub(r"[^A-Za-z0-9]", "", req.query.get("run", ""))[:32]
        if not run and req.query.get("last"):   # the most recently saved project
            files = sorted(LONG_DIR.glob("*/project.json"), key=lambda f: f.stat().st_mtime, reverse=True) if LONG_DIR.is_dir() else []
            run = files[0].parent.name if files else ""
        f = LONG_DIR / run / "project.json" if run else None
        if not f or not f.exists():
            return web.json_response({})
        refs = sorted(p.name for p in (LONG_DIR / run / "refs").glob("*")) if (LONG_DIR / run / "refs").is_dir() else []
        return web.json_response({"run": run, "project": json.loads(f.read_text(encoding="utf-8")), "refs": refs})

    async def h_long_stage(self, req):
        """A saved stage of a long video: the scene continuation points and the reference pictures are copied aside, so redoing a
        scene later does not spoil the stage. action: save | restore | delete."""
        body = await req.json()
        run = re.sub(r"[^A-Za-z0-9]", "", str(body.get("run", "")))[:32]
        sid = re.sub(r"[^A-Za-z0-9]", "", str(body.get("id", "")))[:32]
        act = body.get("action")
        if not run or not sid or act not in ("save", "restore", "delete"):
            raise web.HTTPBadRequest(text="bad stage")
        base = LONG_DIR / run
        st = base / "stages" / sid
        names = ("state_*.latent", "state_*.json")

        def work():
            if act == "save":
                if st.exists():
                    shutil.rmtree(st)
                st.mkdir(parents=True, exist_ok=True)
                n = 0
                for pat in names:
                    for f in base.glob(pat):
                        shutil.copy2(f, st / f.name)
                        n += 1
                if (base / "refs").is_dir():
                    shutil.copytree(base / "refs", st / "refs")
                return {"ok": True, "states": n, "refs": (st / "refs").is_dir()}
            if act == "delete":
                if st.exists() and st.parent.name == "stages":
                    shutil.rmtree(st)
                return {"ok": True}
            if not st.is_dir():   # a stage saved by an older version: only the texts and settings come back
                return {"ok": False}
            for pat in names:
                for f in base.glob(pat):
                    f.unlink(missing_ok=True)
            for f in st.glob("state_*"):
                shutil.copy2(f, base / f.name)
            if (st / "refs").is_dir():
                if (base / "refs").is_dir():
                    shutil.rmtree(base / "refs")
                shutil.copytree(st / "refs", base / "refs")
            return {"ok": True, "refs": (st / "refs").is_dir()}
        return web.json_response(await asyncio.to_thread(work))

    async def h_long_ref(self, req):
        run = re.sub(r"[^A-Za-z0-9]", "", req.match_info["run"])[:32]
        name = Path(req.match_info["name"]).name
        f = LONG_DIR / run / "refs" / name
        if not f.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(f)

    async def h_generate_long(self, req):
        await self.need_models("long")
        self._capture = {}
        try:
            return await self.generate_long(req)
        finally:
            captured, self._capture = self._capture or {}, None
            run = getattr(self, "_long_run", None)
            if run:
                self.keep_long_refs(run, captured)
                self._long_run = None

    async def generate_long(self, req):
        form, files, prompt, aspect, quality, seed = await self.read_form(req, ("img0", "img1", "img2", "img3", "img4", "aud0", "srcvid") + tuple(f"simg{i}_{j}" for i in range(12) for j in range(5)), need_prompt=False)   # the texts are in the scenes, there is no single prompt
        preview = form.get("preview") == "1"   # every scene waits for «Добавить в библиотеку»
        self._long_run = re.sub(r"[^A-Za-z0-9]", "", form.get("run", ""))[:32]   # the references are kept even if the request is refused later
        try:
            scenes = [(str(x.get("prompt", "")).strip(), float(x.get("sec", 10)), bool(x.get("fresh"))) for x in json.loads(form.get("scenes") or "[]")]
        except (ValueError, TypeError, AttributeError):
            scenes = []
        scenes = [(p, min(wf.LONG_MAX_SCENE, max(3.0, sec)), [files[f"simg{i}_{j}"] for j in range(5) if f"simg{i}_{j}" in files], fr)
                  for i, (p, sec, fr) in enumerate(scenes) if p][:12]   # every scene may bring its own references (instead of the common ones)
        fresh = [fr for *_, fr in scenes]   # a scene marked as a NEW SHOT does not continue the previous one (no quality loss)
        scenes = [(p, sec, extra) for p, sec, extra, _ in scenes]
        test = form.get("test") == "1"   # a size test of one scene: stand-alone, does not touch the other scenes
        draft = form.get("draft") == "1" and not test   # quick draft: 4 steps, smaller frame; the finished row is made later with the same seed
        sfx = "_d" if draft else ""
        if form.get("run") and not form.get("seed"):   # one seed for the whole project: the draft and the finished version look alike
            seed = int(hashlib.md5(form["run"].encode()).hexdigest()[:12], 16)
        try:   # «redo this scene»: every redo is another variant, otherwise the same seed would give the same clip again
            seed += max(0, min(1000, int(form.get("variant") or 0))) * 1000003
        except ValueError:
            pass
        if not scenes:
            raise web.HTTPBadRequest(text="Напишите хотя бы одну сцену")
        images = [files[f"img{i}"] for i in range(5) if f"img{i}" in files]
        audio = files.get("aud0")
        try:
            start = max(0.0, float(form.get("start") or 0))
        except ValueError:
            start = 0.0
        vocals = None
        if audio and form.get("vocals") == "1":
            if "vocal_model" in {m["role"] for m in self.missing} or "vocal_model" not in self.model_names:
                raise web.HTTPBadRequest(text="Для «Только вокал» нужна модель MelBand. Скачайте её в разделе «Настройки».")
            vocals = self.model_names["vocal_model"]
        no_pika = form.get("accel") == "old"   # «Без Pika»: the quality route without a speed LoRA (12 steps), for those whom Pika gives trouble
        if not no_pika and "pika_lora" in {m["role"] for m in self.missing}:
            raise web.HTTPBadRequest(text="Файл Pika (pika_h3_flex_turbo_8step_hd_ref2va) не найден. Скачайте его в разделе «Настройки» или выберите «Без Pika».")
        fin = None if no_pika else self.model_names["pika_lora"]
        width, height = wf.resolution(aspect, quality, form)
        dmad = None
        if draft:
            pass   # a draft has the size the user chose (it may be enough for a clip that is only to be enlarged)
            _, loras = await self.comfy_json("GET", "/models/loras")
            dmad = next((n for n in loras or [] if re.search(r"dmad[_\-\s]?4[_\-\s]?step", n, re.I)), None)
            if not dmad:
                raise web.HTTPBadRequest(text="Для черновика нужен файл minimax_h3_DMAD_4step_full_lora в папке loras. Или сделайте сцену сразу в хорошем качестве.")
        scenes = [(wf.lipsync_prompt(p, len(own or images)), sec, own) for p, sec, own in scenes]
        long_common = [media_input(f"Общая карта персонажа {i + 1}", "image", comfy=name) for i, name in enumerate(images)]
        if audio:
            long_common.append(media_input("Музыка или звук", "audio", comfy=audio))
        # a scene may bring references of its own instead of the common ones
        scene_refs = {i: [files[f"simg{i}_{j}"] for j in range(5) if f"simg{i}_{j}" in files] for i in range(len(scenes))}
        long_inputs = list(long_common) + [media_input(f"Сцена {i + 1} · референс {j + 1}", "image", comfy=name)
                                           for i, names in scene_refs.items() for j, name in enumerate(names)]
        part = form.get("part", "")
        if part.isdigit():  # one scene at a time: scene k continues from the saved tail of scene k-1
            k = int(part)
            run = re.sub(r"[^A-Za-z0-9]", "", form.get("run", ""))[:32]
            self._long_run = run
            if not run or k >= len(scenes):
                raise web.HTTPBadRequest(text="Неверный номер сцены")
            state, cont = None, None
            if k == 0 and (form.get("src_item") or files.get("srcvid")):
                cont = await self.long_source(form.get("src_item"), files.get("srcvid"), scenes[0][1])
            if test:
                fresh = list(fresh); fresh[k] = True
            if k and not fresh[k]:
                src = LONG_DIR / run / f"state_{k - 1}{sfx}.latent"
                if not src.exists():
                    raise web.HTTPBadRequest(text=f"Сначала сделайте сцену {k}: следующая продолжает её конец.")
                try:  # the saved tail only fits a scene of the same frame size
                    was = json.loads(src.with_suffix(".json").read_text(encoding="utf-8")).get("res")
                except (OSError, ValueError):
                    was = None
                if was and was != f"{width}×{height}":
                    raise web.HTTPBadRequest(text=f"Сцена {k} сделана в формате {was}, а сейчас выбран {width}×{height}. "
                                                  "Верните прежний формат и качество или переделайте сцену 1 в новом.")
                state = await self.upload_state(src, f"h3s_long_{run}_{k - 1}.latent")
            graph, stages = wf.long_part_graph(self.model_names, scenes, k, width, height, seed, images, audio, start,
                                               dmad if draft else fin, self.custom_loras("long"), "match", vocals, state,
                                               await self.preview_opts(), cont, form.get("src_project") == "1", fresh, draft)
            plan = wf.long_plan([sc[1] for sc in scenes], cont_first=bool(cont) or form.get("src_project") == "1", fresh=fresh)
            sub = f"сцена {k + 1} из {len(scenes)}" + (" · музыка" if audio else " · звук от модели")
            inputs = list(long_common) + [media_input(f"Референс {j + 1}", "image", comfy=name)
                                          for j, name in enumerate(scene_refs.get(k, []))]
            if cont:
                inputs.append(media_input("Видео, с которого продолжаем", "video", comfy=cont[0]))
            elif form.get("src_item"):
                src_it = self.find_item(form.get("src_item"))
                if src_it:
                    inputs.append(media_input("Видео, с которого продолжаем", "video", item=src_it))
            elif files.get("srcvid"):
                inputs.append(media_input("Видео, с которого продолжаем", "video", comfy=files["srcvid"]))
            return await self.queue(graph, stages, {
                "mode": "long", "sub": sub + (" · черновик" if draft else f" · проба {width}×{height}" if test else ""), "prompt": scenes[k][0], "aspect": aspect, "q": quality,
                "len": round((plan[k]["total"] - plan[k]["ov"]) / wf.FPS, 1), "res": f"{width}×{height}", "seed": seed,
                "long": {"run": run, "part": k, "n": len(scenes), "draft": draft, "test": test}, "inputs": inputs}, preview=preview)
        graph, stages = wf.long_graph(self.model_names, scenes, width, height, seed, images, audio, start,
                                      dmad if draft else fin, self.custom_loras("long"), "match", vocals, await self.preview_opts(), fresh, False, draft)
        run = re.sub(r"[^A-Za-z0-9]", "", form.get("run", ""))[:32]
        self._long_run = run
        total = wf.long_plan([sc[1] for sc in scenes], fresh=fresh)[-1]["acc"] / wf.FPS
        sub = f"{len(scenes)} сцен · {len(images)} карт." + (", музыка" if audio else ", звук от модели") + (" · черновик" if draft else "")
        meta = {"mode": "long", "sub": sub, "prompt": "\n\n".join(sc[0] for sc in scenes), "aspect": aspect, "q": quality,
                "len": round(total, 1), "res": f"{width}×{height}", "seed": seed, "inputs": long_inputs}
        if run:
            meta["long"] = {"run": run, "part": len(scenes) - 1, "n": len(scenes), "all": True}
        return await self.queue(graph, stages, meta, preview=preview)

    async def h_long_state(self, req):
        """Which scenes of a long video project have a saved continuation point (so the project can go on after a restart)."""
        run = re.sub(r"[^A-Za-z0-9]", "", req.query.get("run", ""))[:32]
        d = LONG_DIR / run
        names = [f.name for f in d.glob("state_*.latent")] if run and d.is_dir() else []
        parts = sorted(int(m.group(1)) for n in names if (m := re.match(r"state_(\d+)\.latent$", n)))
        dparts = sorted(int(m.group(1)) for n in names if (m := re.match(r"state_(\d+)_d\.latent$", n)))
        return web.json_response({"parts": parts, "draft_parts": dparts})

    async def item_bytes(self, item):
        """The file of a library item: from disk, or from ComfyUI when its output folder is not known to the app."""
        p = self.file_path(item)
        if p and p.exists():
            return await asyncio.to_thread(p.read_bytes)
        f = item["file"]
        async with self.session.get(self.comfy + "/view", params={
                "filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": f.get("type", "output")}) as r:
            if r.status != 200:
                raise web.HTTPBadRequest(text="Не удалось найти один из клипов: он удалён с диска?")
            return await r.read()

    async def h_montage(self, req):
        """Join library clips in the given order with ComfyUI (no ffmpeg in the app)."""
        form, files = {}, {}
        async for field in await req.multipart():
            if field.name.startswith("file_"):   # a clip taken from the user's computer
                files[field.name[5:]] = (field.filename or "clip.mp4", bytes(await field.read()))
            else:
                form[field.name] = await field.text()
        try:
            ids = [i for i in json.loads(form.get("ids") or "[]") if isinstance(i, str)][:30]
        except ValueError:
            ids = []
        try:
            durs = json.loads(form.get("durs") or "[]")
        except ValueError:
            durs = []
        preview = form.get("preview") == "1"   # the final join waits for «Добавить в библиотеку»
        items = []
        for i in ids:
            if i.startswith("up:"):
                k = i[3:]
                if k not in files:
                    raise web.HTTPBadRequest(text="Файл с компьютера не дошёл до программы, добавьте его ещё раз")
                try:
                    ln = float(form.get("len_" + k) or 0)
                except ValueError:
                    ln = 0.0
                items.append({"kind": "video", "_up": files[k], "res": form.get("res_" + k, ""), "len": ln,
                              "ext": (Path(files[k][0]).suffix.lstrip(".").lower() or "mp4")[:5]})
            else:
                items.append(self.find_item(i))   # a Library clip or a draft of the video sections
        if len(items) < 2 or any(not it or it.get("kind") == "image" for it in items):
            raise web.HTTPBadRequest(text="Добавьте хотя бы два видео")
        if self.rh():
            raise web.HTTPBadRequest(text="Монтаж работает только с локальным ComfyUI, не с облаком.")
        cuts = []
        for n_, it in enumerate(items):   # trimmed length of a clip (seconds) or None
            d = durs[n_] if n_ < len(durs) else None
            cuts.append(round(float(d), 3) if isinstance(d, (int, float)) and 0.3 <= d < 3600 else None)
        names, sizes, timing = [], [], []
        for it in items:
            data = it["_up"][1] if "_up" in it else await self.item_bytes(it)
            tr = mp4info.track_seconds(data)   # picture / sound lengths: the montage fits the sound of every clip to its picture
            timing.append((tr["vide"], tr.get("soun")) if tr.get("vide") else None)
            name = f"h3s_m_{uuid.uuid4().hex[:10]}.{it.get('ext') or 'mp4'}"
            fd = aiohttp.FormData()
            fd.add_field("image", data, filename=name, content_type="video/mp4")
            fd.add_field("type", "input")
            status, res = await self.comfy_json("POST", "/upload/image", data=fd)
            if status != 200:
                raise web.HTTPBadGateway(text="Не удалось передать клип в ComfyUI")
            names.append(res["name"])
            mm = re.match(r"\s*(\d+)\D+(\d+)", it.get("res") or "")
            sizes.append((int(mm.group(1)), int(mm.group(2))) if mm else None)
        graph, stages = wf.montage_graph(names, sizes, cuts, timing)
        total = sum((cuts[n_] if cuts[n_] is not None else float(it.get("len") or 0)) for n_, it in enumerate(items))
        inputs = []
        for n_, it in enumerate(items):
            role = f"Клип {n_ + 1}"
            cut = cuts[n_]
            if "_up" in it:   # a clip taken from the user's computer: it lives in ComfyUI's input folder now
                inputs.append(media_input(role, "video", comfy=names[n_], len=cut if cut is not None else it.get("len") or 0))
            else:
                inputs.append(media_input(role, "video", item=it))
        return await self.queue(graph, stages, {
            "mode": "montage", "sub": f"{len(items)} клипов", "prompt": "", "aspect": items[0].get("aspect", ""), "q": "",
            "len": round(total, 1), "res": items[0].get("res", ""), "seed": 0, "inputs": inputs}, last_output=True, preview=preview)

    async def h_grain(self, req):
        """Film grain on ONE clip (a library clip or a file from the computer) as a job of its own: the frames of a clip are decoded
        in memory, so doing the clips one after another keeps memory free between them (all at once, in one graph, it does not fit)."""
        form, content, name = {}, None, ""
        async for part in await req.multipart():
            if part.name == "file":
                name = part.filename or ""
                content = bytes(await part.read())
            else:
                form[part.name] = (await part.text()).strip()
        try:
            strength = min(1.0, max(0.0, float(form.get("strength") or 0) / 100))
        except ValueError:
            strength = 0.0
        if strength <= 0:
            raise web.HTTPBadRequest(text="Зерно выключено (0%)")
        if self.rh():
            raise web.HTTPBadRequest(text="Зерно работает только с локальным ComfyUI.")
        try:
            status, info = await self.comfy_json("GET", "/object_info/ImageNoiseAugmentation", timeout=aiohttp.ClientTimeout(total=15))
        except Exception:
            raise web.HTTPBadGateway(text="ComfyUI не отвечает. Проверьте, что он запущен.")
        if status != 200 or not isinstance(info, dict) or "ImageNoiseAugmentation" not in info:
            raise web.HTTPBadRequest(text="Для зерна плёнки в вашем ComfyUI нужна нода ImageNoiseAugmentation (пакет ComfyUI-KJNodes). "
                                          "Поставьте пакет (например, через ComfyUI Manager) или уберите ползунок «Зерно плёнки» в 0.")
        item = {"kind": "video", "res": form.get("res", ""), "prompt": "", "aspect": ""}
        if content is None:
            item = self.find_item(form.get("id", ""))   # a Library clip or a draft
            if not item or item.get("kind") == "image":
                raise web.HTTPBadRequest(text="Видео не найдено")
            content = await self.item_bytes(item)
            ext = item.get("ext") or "mp4"
        else:
            ext = Path(name).suffix.lstrip(".").lower()[:5] or "mp4"
        fname = f"h3s_g_{uuid.uuid4().hex[:10]}.{ext}"
        fd = aiohttp.FormData()
        fd.add_field("image", content, filename=fname, content_type="video/mp4")
        fd.add_field("type", "input")
        status, res = await self.comfy_json("POST", "/upload/image", data=fd)
        if status != 200:
            raise web.HTTPBadGateway(text="Не удалось передать клип в ComfyUI")
        graph, stages = wf.grain_graph(res["name"], strength, random.randrange(2 ** 31))
        try:
            ln = float(item.get("len") or form.get("len") or 0)
        except ValueError:
            ln = 0.0
        return await self.queue(graph, stages, {
            "mode": "montage", "sub": "Зерно плёнки", "prompt": (item.get("prompt") or name or "")[:100], "aspect": item.get("aspect", ""), "q": "",
            "len": ln, "res": item.get("res", ""), "seed": 0,
            "inputs": [media_input("Клип", "video", comfy=res["name"], len=ln)]}, last_output=True)

    # ---------- FFmpeg: the portable build and the fast montage studio ----------
    # The app itself never needs FFmpeg: ComfyUI joins the clips. This studio is for the case when a join
    # must take seconds (a long reel, a re-cut, a title) and must not occupy the video card at all.

    def ffmpeg_dir(self):
        """Where the portable FFmpeg lives: the folder the user chose, else <app folder>/tools/ffmpeg."""
        return ffmpeg_mgr.get_ffmpeg_dir(self.config, TOOLS_DIR)

    def ffmpeg_status(self):
        st = ffmpeg_mgr.get_ffmpeg_status(self.config, TOOLS_DIR)
        dl = self.ffmpeg_dl
        st["downloading"] = bool(dl and not dl.get("done"))
        st["progress"] = {"pct": dl.get("pct", 0), "text": dl.get("text", "")} if st["downloading"] else None
        st["error"] = str(dl.get("error") or "") if dl else ""   # why the last install failed
        return st

    async def h_ffmpeg_status(self, _req):
        return web.json_response(self.ffmpeg_status())

    async def h_ffmpeg_download(self, req):
        """Install (or update) the portable FFmpeg into the app's own folder, with progress in the UI."""
        body = {}
        if req.can_read_body:
            try:
                body = await req.json() or {}
            except ValueError:
                body = {}
        if self.ffmpeg_dl and not self.ffmpeg_dl.get("done"):
            raise web.HTTPConflict(text="FFmpeg уже скачивается")
        folder = Path(str(body.get("folder") or self.ffmpeg_dir()))
        cancel = threading.Event()
        state = {"cancel": cancel, "pct": 0, "text": "Начинаем…", "done": False}
        self.ffmpeg_dl = state
        loop = asyncio.get_running_loop()

        def prog(pct, text):
            state["pct"], state["text"] = int(pct), str(text)
            try:
                asyncio.run_coroutine_threadsafe(
                    self.broadcast({"type": "ffmpegdl", "pct": state["pct"], "text": state["text"]}), loop)
            except Exception:
                pass

        async def work():
            try:
                res = await self.stop_aware_thread(ffmpeg_mgr.download_and_extract_ffmpeg_sync, folder, prog,
                                                   cancel, cancel=cancel)
            except Exception as ex:  # noqa: BLE001 - the UI needs a message, not a traceback
                res = {"ok": False, "error": str(ex)}
            state["done"] = True
            if res.get("ok"):
                self.config.update(ffmpeg_dir=str(folder))
                await self.broadcast({"type": "ffmpegdl", "done": True, "pct": 100, "text": "FFmpeg установлен"})
            else:
                state["error"] = str(res.get("error") or "не удалось установить FFmpeg")
                await self.broadcast({"type": "ffmpegdl", "done": True, "error": state["error"],
                                      "cancelled": bool(res.get("cancelled"))})

        self.ffmpeg_dl_task = self.tasks.spawn("ffmpeg-dl", work())
        return web.json_response({"started": True})

    async def h_ffmpeg_download_cancel(self, _req):
        if self.ffmpeg_dl:
            self.ffmpeg_dl["cancel"].set()
        return web.json_response({"ok": True})

    async def h_ffmpeg_set_folder(self, req):
        folder = Path(str((await req.json()).get("folder") or ""))
        ff, fp = ffmpeg_mgr.find_ffmpeg(folder)
        if not (ff and fp):
            raise web.HTTPBadRequest(text="В этой папке нет ffmpeg.exe и ffprobe.exe. Укажите папку, где они лежат.")
        self.config.update(ffmpeg_dir=str(folder))
        return web.json_response(self.ffmpeg_status())

    async def h_ffmpeg_reset_folder(self, _req):
        self.config.update(ffmpeg_dir="")
        return web.json_response(self.ffmpeg_status())

    async def h_ffmpeg_open_folder(self, _req):
        folder = self.ffmpeg_dir()
        folder.mkdir(parents=True, exist_ok=True)
        open_in_explorer(folder)
        return web.json_response({"ok": True, "dir": str(folder)})

    async def h_ffmpeg_check_update(self, _req):
        st = self.ffmpeg_status()
        res = await asyncio.to_thread(ffmpeg_mgr.check_ffmpeg_update, st.get("version"))
        return web.json_response({**res, "current": st.get("version")})

    async def ffmpeg_clip_file(self, item, tmp):
        """A file on disk for one clip of the montage (a Library item or a waiting draft)."""
        p = self.file_path(item)
        if p and p.exists():
            return p
        f = item.get("file") or {}
        if f.get("type") == "local":
            raise web.HTTPBadRequest(text="Один из клипов удалён с диска: «" + str(item.get("prompt") or item.get("id"))[:40] + "»")
        dest = tmp / f"h3s_ff_{uuid.uuid4().hex[:10]}.{item.get('ext') or 'mp4'}"
        params = {"filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": f.get("type", "output")}
        async with self.session.get(self.comfy + "/view", params=params) as r:
            if r.status != 200:
                raise web.HTTPBadRequest(text="Не удалось найти один из клипов: он удалён с диска?")
            with open(dest, "wb") as out:
                async for chunk in r.content.iter_chunked(1 << 20):
                    out.write(chunk)
        return dest

    async def h_ffmpeg_montage(self, req):
        """Join clips with the portable FFmpeg (fast, no video card): returns the id of a background job."""
        st = self.ffmpeg_status()
        if not st["installed"]:
            raise web.HTTPBadRequest(text="Сначала установите FFmpeg: одна кнопка, около 100 МБ, ставится в папку программы.")
        form, files = {}, {}
        async for field in await req.multipart():
            if field.name.startswith("file_") or field.name == "music":
                data = await field.read()
                if field.name == "music" and len(data) > 40 * 1024 * 1024:
                    raise web.HTTPBadRequest(text="Музыкальный файл больше 40 МБ: возьмите дорожку покороче.")
                key = "music" if field.name == "music" else field.name[5:]
                files[key] = (field.filename or "", data)
            else:
                form[field.name] = await field.text()
        try:
            ids = [i for i in json.loads(form.get("ids") or "[]") if isinstance(i, str)][:30]
        except ValueError:
            ids = []
        try:
            durs = json.loads(form.get("durs") or "[]")
        except ValueError:
            durs = []
        if not ids:
            raise web.HTTPBadRequest(text="Добавьте хотя бы один клип")
        tmp = DATA_DIR / "cache" / "ffmpeg"
        tmp.mkdir(parents=True, exist_ok=True)
        paths, cleanup = [], []
        for i in ids:
            if i.startswith("up:"):
                k = i[3:]
                if k not in files:
                    raise web.HTTPBadRequest(text="Файл с компьютера не дошёл до программы, добавьте его ещё раз")
                ext = (Path(files[k][0]).suffix.lstrip(".").lower() or "mp4")[:5]
                p = tmp / f"h3s_ff_{uuid.uuid4().hex[:10]}.{ext}"
                p.write_bytes(files[k][1])
                cleanup.append(p)
            else:
                item = self.find_item(i)   # a Library clip or a draft
                if not item or item.get("kind") == "image":
                    raise web.HTTPBadRequest(text="Один из клипов не найден: возможно, его убрали из Библиотеки")
                p = await self.ffmpeg_clip_file(item, tmp)
                if p.parent == tmp:
                    cleanup.append(p)
            paths.append(p)

        def num(key, default, low=None, high=None):
            try:
                v = float(form.get(key)) if form.get(key) not in (None, "") else default
            except ValueError:
                v = default
            if low is not None:
                v = max(low, v)
            if high is not None:
                v = min(high, v)
            return v

        options = {
            "cuts": [round(float(d), 3) if isinstance(d, (int, float)) and 0.3 <= d < 3600 else None
                     for d in (durs[:len(ids)] + [None] * len(ids))[:len(ids)]],
            "speed": num("speed", 1.0, 0.25, 4.0),
            "transition": form.get("transition") or "none",
            "transition_dur": num("transition_dur", 0.5, 0.1, 3.0),
            "aspect": form.get("aspect") or "original",
            "fit_mode": form.get("fit_mode") or "blur",
            "color": {"brightness": num("brightness", 0.0, -1.0, 1.0),
                      "contrast": num("contrast", 1.0, 0.0, 3.0),
                      "saturation": num("saturation", 1.0, 0.0, 3.0),
                      "filter": form.get("filter") or "none"},
            "title": {"text": (form.get("title") or "")[:200],
                      "position": form.get("title_pos") or "bottom",
                      "size": num("title_size", 42, 12, 160),
                      "color": form.get("title_color") or "#ffffff"},
        }
        if "music" in files:
            music = tmp / f"h3s_ff_music_{uuid.uuid4().hex[:8]}{Path(files['music'][0]).suffix or '.mp3'}"
            music.write_bytes(files["music"][1])
            cleanup.append(music)
            options["audio_path"] = str(music)
            options["audio_opts"] = {"loop": True, "mode": form.get("music_mode") or "mix",
                                     "music_vol": num("music_vol", 0.35, 0.0, 1.5),
                                     "video_vol": num("video_vol", 1.0, 0.0, 1.5),
                                     "fade": num("music_fade", 1.0, 0.0, 10.0)}
        preview = form.get("preview") == "1"   # the Library waits for «Добавить в библиотеку», as in the ComfyUI join
        out_dir = self.results_dir()
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            probe = out_dir / f".h3s_{uuid.uuid4().hex[:6]}.tmp"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError:
            raise web.HTTPBadRequest(text=f"Папка результатов недоступна для записи: {out_dir}. Выберите другую в разделе «Настройки».")
        dest = out_dir / f"{datetime.now():%Y%m%d}_{uuid.uuid4().hex[:8]}.mp4"
        pid = "ff-" + uuid.uuid4().hex[:10]
        cancel = threading.Event()
        task = self.tasks.spawn(f"ffmpeg:{pid}",
                                self.run_ffmpeg_job(pid, dest, paths, cleanup, options, cancel, len(ids), preview))
        self.ffmpeg_jobs[pid] = {"cancel": cancel, "task": task, "dest": dest}
        return web.json_response({"prompt_id": pid})

    async def run_ffmpeg_job(self, pid, dest, paths, cleanup, options, cancel, count, preview):
        """The montage itself: FFmpeg in a worker thread, progress into the queue dock, the result into the Library."""
        st = self.ffmpeg_status()
        loop = asyncio.get_running_loop()
        last = {"pct": -1}

        def prog(pct, _text):
            pct = max(0, min(100, int(pct)))
            if pct == last["pct"]:
                return                      # one message per percent, not per line of ffmpeg output
            last["pct"] = pct
            try:
                asyncio.run_coroutine_threadsafe(
                    self.broadcast({"type": "stage", "prompt_id": pid, "stage": "ffmpeg", "step": pct, "max": 100}), loop)
            except Exception:
                pass

        try:
            await self.stop_aware_thread(ffmpeg_mgr.run_studio_montage, st["ffmpeg_path"], st["ffprobe_path"],
                                          [str(p) for p in paths], str(dest), options, prog, cancel, cancel=cancel)
            info = await asyncio.to_thread(ffmpeg_mgr.probe_media, st["ffprobe_path"], str(dest))
            v = (info or {}).get("video") or None
            res = f'{v["width"]}×{v["height"]}' if v else ""
            word = "клип" if count == 1 else "клипа" if count < 5 else "клипов"
            item = {"id": uuid.uuid4().hex[:12], "kind": "video", "mode": "montage",
                    "sub": f"FFmpeg · {count} {word}", "prompt": "",
                    "aspect": options.get("aspect") if options.get("aspect") != "original" else "",
                    "q": "", "len": round(float((info or {}).get("duration") or 0), 1), "res": res,
                    "seed": 0, "prompt_id": pid, "date": datetime.now().isoformat(timespec="seconds"),
                    "file": {"type": "local", "path": str(dest), "filename": dest.name},
                    "origin": {"kind": "ffmpeg"}}
            if preview:
                self.hold_preview(item)
            else:
                try:
                    self.library.add(item)
                except STORE_ERRORS as e:
                    # the built clip is on disk: a failed list write must not delete it as if the
                    # build itself had failed
                    self.note_error(f"Сборка готова, но не записалась в Библиотеку: {e}")
                    await self.broadcast({"type": "error", "prompt_id": pid, "message": (
                        f"Видео собрано и сохранено в файл {dest}, но в Библиотеку не попало: {e}")})
                    return
            await self.broadcast({"type": "done", "prompt_id": pid, "item": self.public(item)})
        except Exception as ex:  # noqa: BLE001 - cancel and ffmpeg errors both land here
            dest.unlink(missing_ok=True)
            msg = "Сборка остановлена" if cancel.is_set() else f"FFmpeg не справился: {ex}"
            await self.broadcast({"type": "error", "prompt_id": pid, "message": str(msg)[:300]})
        finally:
            for p in cleanup:
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError:
                    pass
            self.ffmpeg_jobs.pop(pid, None)

    async def h_ffmpeg_cancel(self, req):
        body = await req.json()
        job = self.ffmpeg_jobs.get(str(body.get("prompt_id") or ""))
        if not job:
            raise web.HTTPNotFound()
        job["cancel"].set()
        return web.json_response({"ok": True})

    async def h_generate_actor(self, req):
        await self.need_models("actor")
        form, prompt, aspect, quality, seed, images, videos, audios, seconds = await self.read_ref_form(req, 3)
        preview = form.get("preview") == "1"   # from the video sections: the Library waits for «Добавить в библиотеку»
        realism = None
        v = lora_strength(form, "realism", 0.7)  # on by default, as in the original workflow
        missing = {m["role"] for m in self.missing}
        if v is not None and "realism_lora" not in missing and "realism_lora" in self.model_names:
            realism = (self.model_names["realism_lora"], v)
        width, height = wf.resolution(aspect, quality, form)
        graph, stages = wf.actor_graph(self.model_names, wf.lipsync_prompt(prompt, len(images)), width, height, seconds, seed,
                                       images, audios, realism, self.custom_loras("actor"))
        sub = f"{len(images)} карт." + (f", голос ({len(audios)})" if audios else ", без голоса") + " · 10 шагов"
        inputs = [media_input(f"Карта персонажа {i + 1}", "image", comfy=name) for i, name in enumerate(images)]
        inputs += [media_input(f"Образец голоса {i + 1}", "audio", comfy=name) for i, name in enumerate(audios)]
        return await self.queue(graph, stages, {
            "mode": "actor", "sub": sub, "prompt": prompt, "aspect": aspect, "q": quality, "len": seconds,
            "res": f"{width}×{height}", "seed": seed, "inputs": inputs}, preview=preview)

    async def h_generate_swap(self, req):
        """Preset «Замена персонажа»: one video + up to two character pictures, the app writes the prompt."""
        await self.need_models("swap")
        form, prompt, aspect, quality, seed, images, videos, audios, seconds = await self.read_ref_form(req, 2)
        preview = form.get("preview") == "1"   # from the video sections: the Library waits for «Добавить в библиотеку»
        if not videos:
            raise web.HTTPBadRequest(text="Добавьте видео, в котором нужно заменить человека")
        if not images:
            raise web.HTTPBadRequest(text="Добавьте изображение персонажа, на которого заменяем")
        shots = []
        if form.get("shots"):   # several plans (shots): every one is swapped on its own, then they are joined
            try:
                for x in json.loads(form["shots"])[:12]:
                    s0, e0 = float(x["s"]), float(x["e"])
                    if e0 - s0 < 0.5:
                        continue
                    shots.append({"s": s0, "e": e0, "pic": int(x.get("pic", 0)), "who": str(x.get("who") or "").strip(),
                                  "a_end": float(x["a_end"]) if x.get("a_end") not in (None, "") else None})
            except (ValueError, KeyError, TypeError):
                raise web.HTTPBadRequest(text="Не удалось разобрать список планов")
            if any(0 <= x["pic"] and x["e"] - x["s"] < 2 for x in shots):
                raise web.HTTPBadRequest(text="Каждый план для замены должен быть не короче 2 секунд: объедините короткие планы с соседними")
        whos = [(form.get(f"who{i}") or (form.get("who") if i == 0 else "") or "").strip() for i in range(len(images))]
        try:
            cuts = [float(x) for x in (form.get("cuts") or "").replace(";", ",").split(",") if x.strip()]
        except ValueError:
            cuts = []
        text = wf.swap_prompt(whos, (form.get("extra") or "").strip(), cuts, form.get("prompt_style") != "plain")
        if form.get("raw_prompt", "").strip():   # a prompt written by hand in the MiniMax format (tests, experts): used as it is
            text = form["raw_prompt"].strip()
        pika = None
        if form.get("accel") == "pika":
            if "pika_lora" in {m["role"] for m in self.missing}:
                raise web.HTTPBadRequest(text="Файл Pika (pika_h3_flex_turbo_8step_hd_ref2va) не найден в ComfyUI. "
                                              "Скачайте его в разделе «Настройки» или выберите обычное ускорение.")
            pika = self.model_names["pika_lora"]
        names = self.model_names
        if form.get("unet"):   # another base model for this job (tests): only a file this ComfyUI really has
            try:
                _, have = await self.comfy_json("GET", "/models/diffusion_models")
            except Exception:
                have = []
            pick = next((f for f in (have or []) if f.replace("/", "\\") == form["unet"].replace("/", "\\")), None)
            if not pick:
                raise web.HTTPBadRequest(text="Такой модели нет в ComfyUI: " + form["unet"])
            names = {**self.model_names, "lipsync_unet": pick}
        width, height = wf.resolution(aspect, quality, form)
        seconds = min(15.0, max(3.0, seconds))
        loras = self.optional_loras(form, (("realism", "realism_lora"), ("motion", "motion_repair_lora"))) + self.custom_loras("swap")
        if shots:
            snd = form.get("sound") if form.get("sound") in ("original", "lock") else "original"
            graph, stages = wf.swap_shots_graph(names, shots, width, height, seed, images, videos[0],
                                                (form.get("extra") or "").strip(), pika, loras, snd,
                                                form.get("small_ref") == "1")
            seconds = max(x["e"] for x in shots) - min(x["s"] for x in shots)
        else:
            snd = form.get("sound") if form.get("sound") in ("original", "lock", "model") else "original"
            graph, stages = wf.swap_graph(names, text, width, height, seconds, seed, images, videos[0], loras, pika, snd,
                                             small_ref=form.get("small_ref") == "1")
        inputs = [media_input("Видео для замены", "video", comfy=videos[0])]
        inputs += [media_input(f"Новый персонаж {i + 1}", "image", comfy=name) for i, name in enumerate(images)]
        return await self.queue(graph, stages, {
            "mode": "swap", "sub": "Замена персонажа" + (f", планов: {len(shots)}" if shots else (f" ({len(images)})" if len(images) > 1 else "")) + (" · Pika" if pika else ""),
            "prompt": prompt or whos[0], "aspect": aspect, "q": quality, "len": seconds,
            "res": f"{width}×{height}", "seed": seed, "inputs": inputs}, preview=preview)

    async def action_setup(self):
        """What the original action workflow needs and what this ComfyUI has."""
        if not self.model_names:
            await self.resolve_models()
        _, oi = await self.comfy_json("GET", "/object_info/SelfLiftH3Sampler")
        _, oi2 = await self.comfy_json("GET", "/object_info/H3SigmaRefiner")
        _, loras = await self.comfy_json("GET", "/models/loras")
        dmad = next((n for n in loras or [] if catalog.ACTION_DMAD_LORA in n), None)
        missing = {m["role"] for m in self.missing}
        have_up = "action_upscaler" not in missing
        nodes = bool(oi) and bool(oi2)
        return {
            "faithful": nodes and have_up,
            "selflift": bool(oi), "refiner": bool(oi2), "refiner_def": (oi2 or {}).get("H3SigmaRefiner"),
            "upscaler": have_up, "dmad": dmad,
            "speed": None if "speed_slider_lora" in missing else self.model_names.get("speed_slider_lora"),
        }

    async def h_action_info(self, _req):
        try:
            s = await self.action_setup()
        except aiohttp.ClientError:
            return web.json_response({"error": "ComfyUI не отвечает"}, status=503)
        s.pop("refiner_def", None)
        return web.json_response(s)

    async def h_generate_action(self, req):
        await self.need_models("action")
        form, prompt, aspect, quality, seed, images, videos, audios, seconds = await self.read_ref_form(req, 6)
        preview = form.get("preview") == "1"   # from the video sections: the Library waits for «Добавить в библиотеку»
        if self.rh():
            # on RunningHub only built-in nodes are assumed; LoRAs are used if the user's RH workflow has them
            rh_files = {n.replace("\\", "/").split("/")[-1].lower(): n for n in (getattr(self, "rh_models", None) or [])}
            speed = rh_files.get(catalog.VARIANTS["speed_slider_lora"]["v1.1"]["file"].lower())
            dmad = next((n for b, n in rh_files.items() if catalog.ACTION_DMAD_LORA in b), None)
            s = {"faithful": False, "dmad": dmad, "speed": speed}
        else:
            s = await self.action_setup()
        loras = []
        for key, name, default in (("dmad", s["dmad"], 0.8), ("speed", s["speed"], 4.0)):
            v = lora_strength(form, key, default)  # the user can switch them off or change the strength
            if name and v is not None:
                loras.append((name, v))
        loras += self.custom_loras("action")
        names = dict(self.model_names)
        width, height = wf.resolution(aspect, quality, form)
        graph, stages = wf.action_graph(names, wf.lipsync_prompt(prompt, len(images)), width, height, seconds, seed, images, audios,
                                        s["faithful"], loras)
        if s["faithful"]:
            # the refiner's input names come from the installed node; values as in the original workflow
            req_inputs = [k for k in (s["refiner_def"]["input"].get("required") or {}) if k != "sigmas"]
            graph["13"]["inputs"] = {"sigmas": ["10", 0], **dict(zip(req_inputs, [1, 0.7, 0.0, "cosine"]))}
        sub = ("точная копия workflow" if s["faithful"] else "упрощённая схема") + f" · {len(images)} карт."
        inputs = [media_input(f"Карта персонажа {i + 1}", "image", comfy=name) for i, name in enumerate(images)]
        inputs += [media_input(f"Аудио {i + 1}", "audio", comfy=name) for i, name in enumerate(audios)]
        return await self.queue(graph, stages, {
            "mode": "action", "sub": sub, "prompt": prompt, "aspect": aspect, "q": quality, "len": seconds,
            "res": f"{width}×{height}", "seed": seed, "faithful": s["faithful"], "inputs": inputs}, preview=preview)

    # ---------- audio: voice / music separation (MelBand RoFormer through ComfyUI) ----------

    async def separation_ready(self):
        """(ok, reason) - is the node pack there and is the model visible to ComfyUI."""
        if self.rh():
            return False, "Отделение голоса работает на этом компьютере, а сейчас включено облако."
        try:
            _, node = await self.comfy_json("GET", "/object_info/MelBandRoFormerSampler")
        except aiohttp.ClientError:
            return False, "ComfyUI не запущен."
        if not node:
            return False, "В ComfyUI нет ноды MelBandRoFormer (ComfyUI-MelBandRoFormer)."
        if not self.model_names:
            await self.resolve_models()
        if "vocal_model" in {m["role"] for m in self.missing}:
            return False, "Не найдена модель MelBandRoformer_fp16.safetensors (папка diffusion_models)."
        return True, ""

    async def h_audio_info(self, _req):
        ok, reason = await self.separation_ready()
        return web.json_response({"available": ok, "reason": reason})

    async def h_audio_separate(self, req):
        """WAV in -> the voice only (FLAC) out. Blocks until ComfyUI is done (usually well under a minute)."""
        ok, reason = await self.separation_ready()
        if not ok:
            return web.json_response({"error": reason}, status=400)
        name = None
        async for field in await req.multipart():
            if field.name == "file" and field.filename:
                name = await self.upload_input(field)
        if not name:
            return web.json_response({"error": "Нет аудио"}, status=400)
        await self.stop_llm()  # the agent's model would compete for video memory
        graph = {
            "1": {"class_type": "LoadAudio", "inputs": {"audio": name}},
            "2": {"class_type": "MelBandRoFormerModelLoader", "inputs": {"model_name": self.model_names["vocal_model"]}},
            "3": {"class_type": "MelBandRoFormerSampler", "inputs": {"model": ["2", 0], "audio": ["1", 0]}},
            "4": {"class_type": "SaveAudio", "inputs": {"audio": ["3", 0], "filename_prefix": "H3Workspace/audio/vocals"}},
        }
        status, res = await self.comfy_json("POST", "/prompt", json={"prompt": graph, "client_id": CLIENT_ID})
        if status != 200:
            return web.json_response({"error": comfy_error_text(res)}, status=400)
        pid = res["prompt_id"]
        loop = asyncio.get_running_loop()
        end = loop.time() + 600
        while loop.time() < end:
            await asyncio.sleep(1.5)
            _, hist = await self.comfy_json("GET", f"/history/{pid}")
            entry = (hist or {}).get(pid)
            if not entry:
                continue
            st = entry.get("status", {})
            if st.get("status_str") == "error":
                msgs = [m[1].get("exception_message", "") for m in st.get("messages", []) if m[0] == "execution_error"]
                return web.json_response({"error": "Не удалось отделить голос: " + (msgs[0].strip() if msgs else "ошибка ComfyUI")}, status=500)
            audio = next((f for out in entry.get("outputs", {}).values() for f in out.get("audio", [])), None)
            if audio:
                async with self.session.get(self.comfy + "/view", params={
                        "filename": audio["filename"], "subfolder": audio.get("subfolder", ""), "type": audio.get("type", "output")}) as r:
                    data = await r.read()
                return web.Response(body=data, content_type="audio/flac")
        return web.json_response({"error": "Отделение голоса заняло слишком много времени"}, status=504)

    async def h_interrupt(self, req):
        body = await req.json()
        pid = body.get("prompt_id")
        if pid:
            await self.cancel_job(pid)
        else:
            await self.stop_current()   # «остановить текущее» — отдельная явная операция, не побочный эффект
        return web.json_response({"ok": True})

    async def cancel_job(self, pid):
        """Stop one job: a cloud task, a waiting one, or the running one (ComfyUI has one running prompt at a time).

        An id this program does not know is not a reason to interrupt whatever the engine happens to be
        running: the window's «stop the current thing» is its own operation (`stop_current`).
        """
        job = self.jobs.get(pid) if pid else None
        if job and job.get("finalizing"):
            # the generation is already over and its result is being written: interrupting the engine
            # now would throw away a clip that exists on disk
            await self.broadcast({"type": "notice", "text": "Генерация уже закончилась: результат сохраняется."})
            return
        if not job:
            await self.broadcast({"type": "notice", "text": "Этого задания уже нет в очереди: останавливать нечего."})
            return
        record = job.get("record")
        if record is not None:
            try:
                accepted = await self.service.request_cancel(record)
            except JobConflict as e:
                await self.broadcast({"type": "notice", "text": e.message})
                return
            except BackendUnavailable:
                self.note_error(f"Отмена задания {pid} записана, но не подтверждена: ComfyUI не отвечает")
                await self.broadcast({"type": "notice", "text": (
                    "ComfyUI не отвечает: отмена записана, но не подтверждена. Задание останется в очереди.")})
                return
            except BackendError as e:
                await self.broadcast({"type": "notice", "text": f"Остановить не получилось: {e}"})
                return
            if record.state is JobState.CANCELED:   # a queued job is removed at once
                self.keep_meta(job)
                self.jobs.pop(pid, None)
                await self.broadcast({"type": "interrupted", "prompt_id": pid})
            elif accepted:
                # running: the engine accepted the request, the outcome comes from its event or the watch
                self.tasks.spawn(f"cancel:{pid}", self.interrupt_watch(pid), replace=True)
            return
        # cloud tasks and jobs of an older run have no record: their own paths stay as they were
        if job.get("task"):
            self.try_set_job_state(job, JobState.CANCELED)
            self.keep_meta(job)
            self.jobs.pop(pid, None)
            rh = self.rh()
            if rh:
                try:
                    await rh.cancel(job["task"])
                except cloud.CloudError:
                    pass
            await self.broadcast({"type": "interrupted", "prompt_id": pid})
        elif job["state"] == "queued":
            await self.comfy_json("POST", "/queue", json={"delete": [pid]})
            self.try_set_job_state(job, JobState.CANCELED)
            self.keep_meta(job)
            self.jobs.pop(pid, None)
            await self.broadcast({"type": "interrupted", "prompt_id": pid})
        else:
            await self.comfy_json("POST", "/interrupt", json={"prompt_id": pid})
            self.tasks.spawn(f"cancel:{pid}", self.interrupt_watch(pid), replace=True)

    async def stop_current(self):
        """The window's «Остановить»: stop whatever the engine is running right now, by explicit request."""
        await self.comfy_json("POST", "/interrupt", json={})

    async def interrupt_watch(self, pid, wait=15):
        """ComfyUI sometimes ignores «Cancel» (stuck on a heavy step). If the job is still there after `wait`
        seconds the engine is not answering us any more, so it is replaced: our own process, or the user's
        own ComfyUI started again from the command line it reports (only when that is possible)."""
        await asyncio.sleep(wait)
        if pid not in self.jobs:
            return
        if await self.stopped_after_all(pid):
            return
        text = "ComfyUI не отвечает на «Отмена». Перезапускаю движок, это займёт около минуты."
        self.note_error(text)
        await self.broadcast({"type": "notice", "text": text})
        ok, why = await self.restart_engine()
        if not ok:
            if why:
                text = ("ComfyUI не отвечает на «Отмена», и перезапустить его не получилось: " + why +
                        (" Закройте окно ComfyUI и запустите его снова." if self.watchdog_local() else ""))
                self.note_error(text)
                await self.broadcast({"type": "notice", "text": text})
            return
        # the restarted engine has an empty queue: nothing of what was running or waiting is there any more
        await self.drop_engine_jobs(kind="interrupted")

    async def stopped_after_all(self, pid):
        """The engine honoured the cancel a moment after we stopped watching.

        ComfyUI can take the interrupt slowly: by the time we are ready to replace the whole engine the
        prompt may already be gone from its queue. Restarting then would throw away a finished result and
        the other jobs behind it, so ask first — and take the result if the job finished while cancelling.
        """
        try:
            _, q = await self.comfy_json("GET", "/queue", timeout=aiohttp.ClientTimeout(total=5))
            alive = {str(x[1]) for key in ("queue_running", "queue_pending") for x in (q or {}).get(key, []) if len(x) > 1}
        except Exception:
            return False  # cannot ask at all: the engine really is unresponsive, replace it
        if pid in alive:
            return False
        job = self.jobs.get(pid)
        if job:
            try:
                _, hist = await self.comfy_json("GET", f"/history/{pid}", timeout=aiohttp.ClientTimeout(total=10))
            except Exception:
                hist = None
            entry = (hist or {}).get(pid) or {}
            if entry.get("outputs") and entry.get("status", {}).get("status_str") != "error":
                await self.finish_job(job)  # it finished just as we were cancelling: keep the clip
                return True
            self.keep_meta(job)
            self.jobs.pop(pid, None)
            await self.broadcast({"type": "interrupted", "prompt_id": pid})
        return True

    async def jobs_watchdog(self):
        """Kill jobs that ComfyUI has lost: e.g. it was restarted while the app waited for events that will
        never come («Ждёт, пока освободится ComfyUI» forever). A job sitting in ComfyUI's queue is legitimately
        waiting, so only a job ABSENT from the queue and from the history is stale."""
        while True:
            await asyncio.sleep(60)
            try:
                if self.rh() or not self.jobs:
                    continue
                _, q = await self.comfy_json("GET", "/queue", timeout=aiohttp.ClientTimeout(total=10))
                queued_ids = {str(item[1]) for item in (q or {}).get("queue_running", []) + (q or {}).get("queue_pending", [])}
            except Exception:
                continue   # ComfyUI unreachable or restarting: not the watchdog's case
            now = datetime.now().timestamp()  # t0 and last_ev are wall-clock seconds (the details window shows them)
            for pid, job in list(self.jobs.items()):
                if job.get("task"):   # cloud (RunningHub) jobs are watched by their own poller
                    continue
                if job.get("finalizing"):   # the result is being written: this is not a silent generation
                    continue
                age, silent = now - job.get("t0", now), now - job.get("last_ev", job.get("t0", now))
                if pid in queued_ids:
                    if job["state"] == "running" and silent > 600:
                        # ComfyUI still holds the job but has been silent for 10 minutes: a hung node.
                        # (Legit quiet phases like loading a huge model from disk are covered by the threshold.)
                        if self.watchdog_local():   # our own engine or ComfyUI on this computer: replace it
                            text = "Генерация зависла: ComfyUI молчит больше 10 минут. Перезапускаем движок, это займёт около минуты."
                            self.note_error(text)
                            await self.broadcast({"type": "notice", "text": text})
                            # the one restart path of the app: it holds the «restart in progress» flag, so this watch
                            # and «Сторож видеокарты» never restart the engine at the same time
                            ok, why = await self.restart_engine()
                            if ok:
                                self.jobs.pop(pid, None)
                                await self.broadcast({"type": "error", "prompt_id": pid,
                                                      "message": "Генерация зависла в ComfyUI и была снята при перезапуске движка. "
                                                                 "Отправьте её ещё раз."})
                                await self.drop_engine_jobs(None, "interrupted")  # everything else died with the engine
                            elif why:
                                self.note_error(f"Не удалось перезапустить движок после зависания: {why}")
                        else:
                            await self._stale_job(job, "Генерация зависла в вашем ComfyUI (больше 10 минут без ответа). "
                                                       "Закройте окно ComfyUI и запустите его заново, затем отправьте генерацию ещё раз.")
                    continue
                if job["state"] == "queued" and age > 240:
                    _, hist = await self.comfy_json("GET", f"/history/{pid}")
                    if (hist or {}).get(pid):   # it actually finished while nobody watched: collect the result
                        try:
                            await self.finish_job(job)
                        except Exception:
                            self.jobs.pop(pid, None)
                            await self.broadcast({"type": "interrupted", "prompt_id": pid})
                    else:
                        await self._stale_job(job, "ComfyUI это задание не получил (скорее всего, он перезапускался). "
                                                   "Отправьте генерацию ещё раз.")
                elif job["state"] == "running" and silent > 900:
                    _, hist = await self.comfy_json("GET", f"/history/{pid}")
                    if (hist or {}).get(pid):
                        try:
                            await self.finish_job(job)
                        except Exception:
                            self.jobs.pop(pid, None)
                            await self.broadcast({"type": "interrupted", "prompt_id": pid})
                    else:
                        await self._stale_job(job, "Связь с ComfyUI прервалась: задание потеряно. "
                                                   "Отправьте генерацию ещё раз.")

    async def _stale_job(self, job, message):
        pid = job["prompt_id"]
        self.try_set_job_state(job, JobState.FAILED, error=message)
        self.note_error("Задание зависло и снято: " + message)
        await self.broadcast({"type": "error", "prompt_id": pid, "message": message})
        self.jobs.pop(pid, None)

    async def h_library(self, _req):
        return web.json_response([self.public(x) for x in self.library.items])

    async def h_preview_keep(self, req):
        """«Добавить в библиотеку»: the picture waits in `pending` until this moment, so nothing was archived before.

        The record leaves `pending` only once it is in the Library. A second press (a double click, or a
        retry after a failed save) returns the same item instead of a duplicate or an error, and a failed
        write leaves the draft where it was so the button still works.
        """
        iid = str((await req.json()).get("id") or "")
        item = self.pending.get(iid)
        if item is None:
            known = self.library.get(iid)
            if known is not None:   # already kept: the same answer, no second copy
                return web.json_response({"item": self.public(known), "kept": True})
            raise web.HTTPBadRequest(text="Эта картинка уже не ждёт: возможно, её уже добавили в Библиотеку. Сгенерируйте заново")
        if self.library.get(iid) is not None:   # the list was written, the draft was not dropped yet
            self.pending.pop(iid, None)
            return web.json_response({"item": self.public(self.library.get(iid)), "kept": True})
        item.pop("preview", None)
        try:
            item = await self.media.keep(item)   # one row, then its copy; raises if the row cannot be written
        except STORE_ERRORS as e:
            item["preview"] = True          # still a draft: «Добавить в библиотеку» can be pressed again
            self.note_error(f"Не удалось добавить картинку в Библиотеку: {e}")
            raise web.HTTPInternalServerError(text=f"Не удалось записать Библиотеку: {e}. Попробуйте ещё раз.")
        self.pending.pop(iid, None)
        return web.json_response({"item": self.public(item)})

    async def h_library_patch(self, req):
        item = self.library.get(req.match_info["id"])
        if not item:
            raise web.HTTPNotFound()
        body = await req.json()
        if "fav" in body:
            item["fav"] = bool(body["fav"])
            self.library.save(item)
        return web.json_response(self.public(item))

    async def h_library_delete(self, req):
        item = self.library.get(req.match_info["id"])
        if not item:
            raise web.HTTPNotFound()
        res = await self.delete_items([item])
        if res["failed"]:
            return web.json_response({"error": "Не удалось переместить файл в корзину"}, status=500)
        return web.json_response({"ok": True, "recycled": res["recycled"]})

    async def delete_items(self, items):
        """Library items to the Recycle Bin (the original and the program's own copy), all files in one go."""
        todo, files = [], {}
        for item in items:
            paths = {p for p in (self.file_path(item), Path(item["local"]) if item.get("local") else None) if p and p.exists()}
            files[item["id"]] = paths
            todo += paths
        gone = set(await asyncio.to_thread(send_to_recycle_bin, todo)) if todo else set()
        done, failed = [], []
        for item in items:
            if files[item["id"]] <= gone:
                self.library.remove(item["id"])
                done.append(item["id"])
            else:
                failed.append(item["id"])
        return {"done": done, "failed": failed, "recycled": bool(gone)}

    async def h_library_delete_many(self, req):
        ids = [i for i in (await req.json()).get("ids", []) if isinstance(i, str)][:2000]
        items = [x for x in (self.library.get(i) for i in ids) if x]
        return web.json_response(await self.delete_items(items))

    async def h_results_get(self, _req):
        items = self.library.items
        lost = sum(1 for x in items if (p := self.file_path(x)) and not p.exists())
        nocopy = sum(1 for x in items if not (x.get("local") and Path(x["local"]).exists()) and (p := self.file_path(x)) and p.exists())
        return web.json_response({"dir": str(self.results_dir()), "keep": self.keep_copies(), "lost": lost, "nocopy": nocopy,
                                  "default": str(self.default_results_dir())})

    async def h_results_set(self, req):
        body = await req.json()
        if "keep" in body:
            self.config.update(keep_copies=bool(body["keep"]))
        d = (body.get("dir") or "").strip().strip('"')
        if d:
            try:
                Path(d).mkdir(parents=True, exist_ok=True)
            except OSError:
                return web.json_response({"error": "Не получается создать папку. Проверьте путь и диск"}, status=400)
            self.config.update(results_dir=d)
        elif body.get("reset"):
            self.config.update(results_dir=None)
        return await self.h_results_get(req)

    async def h_results_backfill(self, req):
        """Make copies of the files that are still in ComfyUI's output folder but have no copy of their own yet."""
        n = 0
        for x in list(self.library.items):
            p = self.file_path(x)
            if p and p.exists() and not (x.get("local") and Path(x["local"]).exists()):
                n += 1 if await self.archive_item(x) else 0
        return web.json_response({"copied": n, **json.loads((await self.h_results_get(req)).text)})

    async def h_results_clean(self, req):
        """Remove the Library records whose files are gone from the disk (nothing on the disk is touched)."""
        gone = [x["id"] for x in self.library.items if (p := self.file_path(x)) and not p.exists()]
        for i in gone:
            self.library.remove(i)
        return web.json_response({"removed": len(gone), **json.loads((await self.h_results_get(req)).text)})

    async def h_file(self, req):
        item = self.find_item(req.match_info["id"])
        if not item:
            raise web.HTTPNotFound()
        f = item["file"]
        headers = {}
        if req.query.get("download"):
            headers["Content-Disposition"] = f'attachment; filename="{f["filename"]}"'
        path = self.file_path(item)
        if path and path.exists():
            return web.FileResponse(path, headers=headers)
        # output folder unknown: stream through ComfyUI
        params = {"filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": f.get("type", "output")}
        async with self.session.get(self.comfy + "/view", params=params) as r:
            if r.status != 200:
                raise web.HTTPNotFound(text="Файл не найден")
            resp = web.StreamResponse(headers={"Content-Type": r.headers.get("Content-Type", "video/mp4"), **headers})
            await resp.prepare(req)
            async for chunk in r.content.iter_chunked(1 << 16):
                await resp.write(chunk)
            return resp

    async def h_srcfile(self, req):
        """The file an enlarged result was made from (the 'before' of the before/after slider)."""
        item = self.library.get(req.match_info["id"])
        if not item:
            raise web.HTTPNotFound()
        src = self.find_item(item.get("src") or "")
        if src and (p := self.file_path(src)) and p.exists():
            return web.FileResponse(p)
        f = item.get("src_file")
        if not f:
            raise web.HTTPNotFound()
        # ComfyUI's own copy: keep one of our own next to the app and serve that, because a player
        # can only seek (and so stay in step with the result) a file that answers byte-range
        # requests. A plain body without ranges makes every jump restart the download.
        ext = Path(f.get("filename") or "").suffix or item.get("ext") or (".png" if item.get("kind") == "image" else ".mp4")
        cached = DATA_DIR / "cache" / "src" / f'{item["id"]}{ext}'
        try:
            have = cached.stat().st_size > 0
        except OSError:
            have = False
        if not have:
            try:
                cached.parent.mkdir(parents=True, exist_ok=True)
                async with self.session.get(self.comfy + "/view", params=f) as r:
                    if r.status != 200:
                        raise web.HTTPNotFound(text="Исходный файл не найден")
                    part = cached.with_name(f'{cached.name}.{uuid.uuid4().hex[:8]}.part')
                    with open(part, "wb") as out:
                        async for chunk in r.content.iter_chunked(1 << 20):
                            out.write(chunk)
                part.replace(cached)
            except web.HTTPException:
                raise
            except (OSError, aiohttp.ClientError):   # no room for a copy: play it through, as before
                async with self.session.get(self.comfy + "/view", params=f) as r:
                    if r.status != 200:
                        raise web.HTTPNotFound(text="Исходный файл не найден")
                    body = await r.read()
                    return web.Response(body=body, content_type=r.headers.get("Content-Type", "video/mp4"))
        return web.FileResponse(cached)

    async def h_events(self, req):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(req)
        self.browsers.add(ws)
        await ws.send_str(json.dumps({"type": "engine", "online": self.comfy_online}))
        try:
            async for _ in ws:
                pass
        finally:
            self.browsers.discard(ws)
        return ws

    async def h_agent_allow(self, req):
        """The window's switch «Агенты»: whether other programs on this computer may drive the studio."""
        if req.method == "POST":
            body = await req.json()
            self.config.update(allow_agents=bool(body.get("enabled", True)))
        return web.json_response({"enabled": self.agents.enabled(), "port": self.port,
                                  "file": str(agentapi.agent_file())})

    # ---------- agent ----------

    def skill_public(self, s):
        return {k: s[k] for k in ("id", "name", "description", "builtin", "mode", "keywords")} | \
               {"refs": [r.name for r in s["refs"]]}

    async def h_agent_status(self, _req):
        variant = await self.llm_variant()
        v = ag.LLM_VARIANTS[variant]
        custom = self.config.get("llm_custom")
        vram = (self.hw or {}).get("vram_gb") or 0
        candidates = []
        if custom and custom.get("dir") and Path(custom["dir"]).is_dir():
            _, _, found = await asyncio.to_thread(ag.find_gguf, custom["dir"])
            candidates = [{"path": str(p), "name": p.name, "gb": round(p.stat().st_size / 1e9, 1),
                           "fits": ag.fits(p, vram), "current": str(p) == custom["model"]} for p in found]
        options = []
        for k, ov in ag.LLM_VARIANTS.items():
            mp, pp = ag.variant_files(self.llm.root, ov)
            options.append({"key": k, "title": ov["title"], "uncensored": bool(ov.get("uncensored")),
                            "size": ov["model_size"] + ov["mmproj_size"], "have": mp.exists() and pp.exists(),
                            "current": k == variant and not custom, "fits": vram >= ov.get("min_vram", 0),
                            "min_vram": ov.get("min_vram", 0)})
        return web.json_response({
            "variants": options,
            "my_model": ({"title": Path((custom or self.config.get("llm_custom_saved") or {}).get("model", "")).stem,
                          "current": bool(custom)} if (custom or (self.config.get("llm_custom_saved") or {}).get("model")) else None),
            "variant": variant, "title": Path(custom["model"]).stem if custom else v["title"],
            "size": 37e6 if custom else v["model_size"] + v["mmproj_size"], "candidates": candidates, "vram": vram,
            "custom": custom, "vision": self.llm.vision(variant), "has_model": self.llm.has_model(variant),
            "speech_ready": self.speech_status()["ready"], "speech_size": speech.MODEL_SIZE + speech.WHISPER_ZIP_SIZE,
            "speech": self.speech_status(),
            "installed": self.llm.installed(variant), "running": self.llm.alive(),
            "installing": bool(self.agent_installer), "dir": str(self.llm.root), "managed": self.config.get("engine") == "managed",
            "dir_chosen": bool(self.config.get("llm_dir")) or self.config.get("engine") == "managed",
            "skills": [self.skill_public(s) for s in self.skills.list()],
        })

    async def h_agent_install(self, req):
        if self.agent_installer:
            return web.json_response({"error": "Уже скачивается"}, status=409)
        variant = await self.llm_variant()
        try:
            wanted = ((await req.json()).get("dir") or "").strip()
        except ValueError:
            wanted = ""
        if self.config.get("engine") != "managed" and not self.llm.installed(variant):
            # with the user's own ComfyUI nothing goes anywhere the user did not name
            if not wanted and not self.config.get("llm_dir"):
                return web.json_response({"error": "Сначала укажите папку, куда скачать помощника"}, status=400)
            if wanted:
                target = Path(wanted) if Path(wanted).name == "MinimaxH3_assistant" else Path(wanted) / "MinimaxH3_assistant"
                try:
                    target.mkdir(parents=True, exist_ok=True)
                except OSError:
                    return web.json_response({"error": "Не получается создать папку. Проверьте путь и диск"}, status=400)
                self.config.update(llm_dir=str(target))
                await self.llm_variant()

        async def emit(ev):
            await self.broadcast(dict(ev, type="agent_setup"))

        self.agent_installer = engine.Installer(ROOT, self.session, emit)

        async def run():
            try:
                await ag.install(self.agent_installer, self.llm.root, variant,
                                 runtime_only=bool(self.config.get("llm_custom")),
                                 whisper_dir=self.config.get("whisper_dir"))
                await emit({"step": "agent", "state": "done", "label": "Модель агента установлена"})
            except engine.Cancelled:
                await emit({"step": "agent", "state": "cancelled", "label": "Скачивание остановлено"})
            except Exception as e:
                await emit({"step": "agent", "state": "error", "label": str(e)})
            finally:
                self.agent_installer = None

        self.tasks.spawn("agent-install", run())
        return web.json_response({"ok": True})

    def speech_status(self):
        s = speech.locate(self.llm.root, self.config.get("whisper_dir"))
        return {"ready": s["ready"], "missing": s["missing"], "custom": s["custom"],
                "exe": str(s["exe"]) if s["exe"] else None, "model": s["model"].name if s["model"] else None}

    async def h_speech_folder(self, req):
        """Use whisper files the user already has (or go back to the app's own copy)."""
        body = await req.json()
        await self.llm_variant()
        if body.get("reset"):
            self.config.update(whisper_dir=None)
        else:
            folder = (body.get("dir") or "").strip().strip('"')
            if not Path(folder).is_dir():
                return web.json_response({"error": f"Папка не найдена: {folder}"}, status=400)
            found = await asyncio.to_thread(speech.locate, self.llm.root, folder)
            if not (speech._find_exe(folder) or speech._find_model(folder)):
                return web.json_response({"error": "В папке нет ни whisper-cli.exe, ни модели ggml-*.bin "
                                                   "(нужна многоязычная модель, не «.en»)."}, status=400)
            self.config.update(whisper_dir=folder)
        return await self.h_agent_status(req)

    async def h_transcribe(self, req):
        """Words from an audio clip (WAV from the browser) - lyrics for lipsync prompts and voice input."""
        await self.llm_variant()  # keeps llm.root in sync with the install folder
        if not self.speech_status()["ready"]:
            return web.json_response({"error": "Распознавание речи ещё не скачано (карточка «Модель» в разделе «Агент»)."},
                                     status=400)
        wav, lang = None, "auto"
        async for field in await req.multipart():
            if field.name == "file":
                wav = await field.read()
            elif field.name == "lang":
                lang = (await field.text()).strip() or "auto"
        if not wav:
            return web.json_response({"error": "Нет аудио"}, status=400)
        try:
            text, code, name = await speech.transcribe(self.llm.root, wav, lang, self.config.get("whisper_dir"))
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
        return web.json_response({"text": text, "lang": code, "lang_name": name})

    async def h_agent_variant(self, req):
        """Choose which of the app's own models the agent uses (Gemma / Qwen, with or without refusals)."""
        key = (await req.json()).get("variant")
        if key not in ag.LLM_VARIANTS:
            return web.json_response({"error": "Неизвестная модель"}, status=400)
        cur = self.config.get("llm_custom")
        # the user's own model is not lost: it stays as «Моя модель» and can be switched back on
        self.config.update(llm_variant=key, llm_custom=None, **({"llm_custom_saved": cur} if cur else {}))
        await self.stop_llm()
        return await self.h_agent_status(req)

    async def h_agent_model(self, req):
        """Use a GGUF model the user already has (or go back to the app's own Gemma)."""
        body = await req.json()
        if body.get("reset"):
            cur = self.config.get("llm_custom")
            self.config.update(llm_custom=None, **({"llm_custom_saved": cur} if cur else {}))
        elif body.get("restore"):
            saved = self.config.get("llm_custom_saved")
            if not saved or not Path(saved.get("model", "")).exists():
                return web.json_response({"error": "Файл вашей модели не найден"}, status=400)
            self.config.update(llm_custom=saved)
        else:
            folder = (body.get("dir") or "").strip().strip('"')
            if not Path(folder).is_dir():
                return web.json_response({"error": f"Папка не найдена: {folder}"}, status=400)
            if self.hw is None:
                self.hw = await asyncio.to_thread(engine.detect_hardware)
            model, proj, models = await asyncio.to_thread(ag.find_gguf, folder, body.get("model"), self.hw.get("vram_gb"))
            if not model:
                return web.json_response({"error": "В папке нет моделей .gguf. Нужна обычная Gemma в формате GGUF "
                                                   "(версия для LTX в text_encoders не подойдёт)."}, status=400)
            self.config.update(llm_custom={"model": str(model), "mmproj": str(proj) if proj else None, "dir": folder})
        await self.stop_llm()
        return await self.h_agent_status(req)

    async def h_agent_install_cancel(self, _req):
        if self.agent_installer:
            self.agent_installer.cancelled = True
        return web.json_response({"ok": True})

    async def h_agent_chat(self, req):
        body = await req.json()
        messages = body.get("messages") or []
        target = body.get("target") if body.get("target") in ag.AGENT_TARGETS else "video"
        ctx = body.get("ctx") or {}
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await resp.prepare(req)

        async def send(obj):
            await resp.write(f"data: {ag.dumps(obj)}\n\n".encode())

        try:
            if self.jobs:
                raise RuntimeError("Сейчас идёт генерация видео — агент запустится, когда она закончится.")
            variant = await self.llm_variant()
            if not self.llm.installed(variant):
                raise RuntimeError("Модель агента ещё не скачана.")
            if not self.llm.alive():
                await send({"type": "status", "text": "Освобождаем видеопамять…"})
                try:
                    await self.comfy_json("POST", "/free", json={"unload_models": True, "free_memory": True})
                except Exception:
                    pass
                await send({"type": "status", "text": f'Загружаем {ag.LLM_VARIANTS[variant]["title"]}… (до минуты)'})
                self.llm.start(variant)
                await ag.wait_ready(self.session, self.llm)

            if not self.llm.vision(variant) and any(m.get("images") for m in messages):
                # without a vision projector the model cannot take pictures
                messages = [dict(m, images=None) for m in messages]
                await send({"type": "status", "text": "У этой модели нет файла «зрения» (mmproj) — изображения она не увидит"})
            # frames cut out of a video are the last pictures of a message and are not hero pictures (Picture N)
            ctx["images"] = sum(max(0, len(m.get("images") or []) - int(m.get("frames") or 0)) for m in messages if m["role"] == "user")
            active = self.skills.active(ag.chat_text(messages), target)
            if target == "actor" and not any(s["id"] == "akterskaya-igra" for s in active):
                # this mode is acting-first: its skill is always on here
                active += [s for s in self.skills.list() if s["id"] == "akterskaya-igra" and s["mode"] != "off"]
            await send({"type": "skills", "names": [s["name"] for s in active]})
            system = ag.system_prompt("\n\n".join(self.skills.text(s) for s in active), target, ctx)
            await send({"type": "status", "text": "Читаю скиллы и думаю…"})

            thinking = False
            payload = {"messages": [{"role": "system", "content": system}] + ag.to_openai(messages),
                       "stream": True, "temperature": 0.6, "cache_prompt": True}
            if body.get("think") != "deep":
                # "fast" (default): no hidden reasoning before the answer. On the user's machine a light request took 17 s with it
                # (about 2000 characters of thoughts) and 1.5 s without; the long-video rules took minutes with it
                payload["chat_template_kwargs"] = {"enable_thinking": False}
                payload["thinking_budget_tokens"] = 0
            async with self.session.post(self.llm.url + "/v1/chat/completions", json=payload,
                                         timeout=aiohttp.ClientTimeout(total=None, sock_read=600)) as r:
                if r.status != 200:
                    raise RuntimeError(f"Модель ответила ошибкой {r.status}: {(await r.text())[:300]}")
                async for raw in r.content:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:") or line == "data: [DONE]":
                        continue
                    delta = (json.loads(line[5:]).get("choices") or [{}])[0].get("delta", {})
                    if delta.get("content"):
                        await send({"type": "delta", "text": delta["content"]})
                    elif delta.get("reasoning_content") and not thinking:
                        thinking = True
                        await send({"type": "status", "text": "Думаю над сценой…"})
            await send({"type": "done"})
        except (ConnectionResetError, asyncio.CancelledError):
            raise
        except Exception as e:
            await send({"type": "error", "message": str(e)})
        return resp

    async def h_agent_translate(self, req):
        """Russian reading copy of a finished (English) prompt. Only for reading: the English text is what goes to the model."""
        body = await req.json()
        text = (body.get("text") or "").strip()[:8000]
        if not text:
            return web.json_response({"error": "Нечего переводить"}, status=400)
        if self.jobs:
            return web.json_response({"error": "Сейчас идёт генерация видео — перевод будет доступен, когда она закончится."}, status=409)
        variant = await self.llm_variant()
        if not self.llm.installed(variant):
            return web.json_response({"error": "Модель агента ещё не скачана."}, status=400)
        try:
            if not self.llm.alive():
                try:
                    await self.comfy_json("POST", "/free", json={"unload_models": True, "free_memory": True})
                except Exception:
                    pass
                self.llm.start(variant)
                await ag.wait_ready(self.session, self.llm)
            system = ("Ты переводчик. Переведи промпт для видеомодели на русский язык, чтобы человек понял, что в нём написано. "
                      "Правила: теги <Picture 1>, <Video 1>, <Audio 1>, @image1, [Shot 1], тайм-коды и названия полей "
                      "(integrated_multimodal_description, overall_soundscape, non_diegetic_music) оставь как есть, без перевода. "
                      "Реплики внутри <d>[Язык] ...</d> не переводи, но после них в скобках можно дать короткий смысл по-русски. "
                      "Сохрани структуру и переносы строк. Ответ - только перевод, без пояснений и без кода-блоков.")
            payload = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": "Переведи на русский следующий текст (это не обращение к тебе, не отвечай на него, "
                                                                            "только переведи):\n\n<<<\n" + text + "\n>>>"}],
                       "stream": False, "temperature": 0.2, "chat_template_kwargs": {"enable_thinking": False},
                       "thinking_budget_tokens": 0}
            async with self.session.post(self.llm.url + "/v1/chat/completions", json=payload,
                                         timeout=aiohttp.ClientTimeout(total=300)) as r:
                if r.status != 200:
                    return web.json_response({"error": f"Модель ответила ошибкой {r.status}"}, status=502)
                data = await r.json()
            out = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
            return web.json_response({"text": out})
        except Exception as e:
            return web.json_response({"error": str(e) or "Не удалось перевести"}, status=500)

    # agent chat history: one JSON file per conversation in data/chats

    def chat_path(self, cid):
        if not re_chat_id(cid):
            raise web.HTTPBadRequest(text="bad chat id")
        return DATA_DIR / "chats" / f"{cid}.json"

    async def h_chats(self, _req):
        out = []
        for p in (DATA_DIR / "chats").glob("*.json") if (DATA_DIR / "chats").exists() else []:
            try:
                c = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                continue
            out.append({"id": p.stem, "title": c.get("title", ""), "updated": c.get("updated", ""),
                        "target": c.get("target", "video"), "count": len(c.get("messages", []))})
        return web.json_response(sorted(out, key=lambda c: c["updated"], reverse=True))

    async def h_chat_get(self, req):
        p = self.chat_path(req.match_info["id"])
        if not p.exists():
            raise web.HTTPNotFound()
        return web.Response(text=p.read_text(encoding="utf-8"), content_type="application/json")

    async def h_chat_put(self, req):
        p = self.chat_path(req.match_info["id"])
        body = await req.json()
        msgs = [{k: m.get(k) for k in ("role", "content", "images", "skills", "error", "nframes") if m.get(k)}
                for m in body.get("messages") or [] if m.get("role") in ("user", "assistant")]
        first = next((m.get("content", "") for m in msgs if m["role"] == "user"), "") or "Без названия"
        data = {"title": (body.get("title") or first).strip().replace("\n", " ")[:80], "target": body.get("target", "video"),
                "updated": datetime.now().isoformat(timespec="seconds"), "messages": msgs}
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(p, data)
        return web.json_response({"ok": True, "title": data["title"]})

    async def h_chat_delete(self, req):
        self.chat_path(req.match_info["id"]).unlink(missing_ok=True)
        return web.json_response({"ok": True})

    async def h_chats_clear(self, _req):
        for p in (DATA_DIR / "chats").glob("*.json") if (DATA_DIR / "chats").exists() else []:
            p.unlink(missing_ok=True)
        return web.json_response({"ok": True})

    async def h_skill_upload(self, req):
        files = []
        async for field in await req.multipart():
            if field.filename:
                files.append((field.filename.replace("\\", "/"), await field.read()))
        try:
            sid = await asyncio.to_thread(self.skills.save_upload, files)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"ok": True, "id": sid})

    async def h_skill_patch(self, req):
        body = await req.json()
        self.skills.save_settings(req.match_info["id"], body.get("mode"), body.get("keywords"))
        return web.json_response({"ok": True})

    async def h_skill_delete(self, req):
        self.skills.delete(req.match_info["id"])
        return web.json_response({"ok": True})

    async def h_index(self, _req):
        return web.FileResponse(UI_DIR / "index.html", headers={"Cache-Control": "no-store"})


def lora_strength(form, key, default):
    """Strength sent by the UI: a number when the LoRA is on, "off" when off, nothing -> the default (None = off)."""
    v = form.get(f"lora_{key}")
    if v in (None, ""):
        return default
    if v == "off":
        return None
    try:
        return float(v)
    except ValueError:
        return default


def re_chat_id(cid):
    return cid.isalnum() and 4 <= len(cid) <= 40


def gpu_name(raw):
    # "cuda:0 NVIDIA GeForce RTX 4060 Ti : cudaMallocAsync" -> "NVIDIA GeForce RTX 4060 Ti"
    name = raw.split(" : ")[0]
    return name.split(" ", 1)[1] if name.startswith("cuda:") and " " in name else name


def comfy_error_text(res):
    if not isinstance(res, dict):
        return "ComfyUI отклонил задание"
    err = res.get("error") or {}
    parts = [err.get("message", "") + (": " + err.get("details", "") if err.get("details") else "")]
    for node_id, ne in (res.get("node_errors") or {}).items():
        for e in ne.get("errors", []):
            parts.append(f'{ne.get("class_type", node_id)}: {e.get("message")} {e.get("details", "")}'.strip())
    return "\n".join(p for p in parts if p) or "ComfyUI отклонил задание"


def send_to_recycle_bin(paths):
    """Move files to the Recycle Bin with ONE hidden PowerShell (before: one visible blue window per file). Returns the paths that are gone."""
    paths = [Path(p) for p in ([paths] if isinstance(paths, (str, Path)) else paths)]
    if not paths:
        return []
    ps = ("Add-Type -AssemblyName Microsoft.VisualBasic; "
          "foreach ($p in ($env:H3_DEL -split '\\|')) { if ($p) { try { [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($p, 'OnlyErrorDialogs', 'SendToRecycleBin') } catch {} } }")   # '|' cannot be in a Windows path
    subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps], capture_output=True,
                   env={**os.environ, "H3_DEL": "|".join(str(p) for p in paths)}, creationflags=engine.CREATE_NO_WINDOW)
    return [p for p in paths if not p.exists()]


def build_app(comfy_override=None, port=None):
    state = App(comfy_override, port)
    app = web.Application(client_max_size=300 * 1024 * 1024)
    app.middlewares.append(same_origin_only)  # a foreign page must not drive the server (form POST, WebSocket)
    app.middlewares.append(state.agents.middleware)  # only /api/agent/v1/ is guarded: the window needs no key
    app.on_startup.append(state.start)
    app.on_cleanup.append(state.stop)
    app.add_routes([
        web.get("/", state.h_index),
        web.get("/api/status", state.h_status),
        web.get("/api/system", state.h_system),
        web.get("/api/audio/info", state.h_audio_info),
        web.post("/api/audio/separate", state.h_audio_separate),
        web.get("/api/setup/info", state.h_setup_info),
        web.post("/api/setup/external", state.h_setup_external),
        web.post("/api/setup/install", state.h_setup_install),
        web.post("/api/mode/add", state.h_mode_add),
        web.post("/api/settings/mirror", state.h_mirror),
        web.post("/api/settings/watchdog", state.h_watchdog_set),
        web.post("/api/engine/restart", state.h_engine_restart),
        web.post("/api/mode/offered", state.h_mode_offered),
        web.post("/api/setup/cancel", state.h_setup_cancel),
        web.post("/api/open-driver-page", state.h_open_driver),
        web.post("/api/clipboard", state.h_clipboard),
        web.get("/api/clipboard/has-image", state.h_clipboard_has_image),
        web.route("*", "/api/clipboard/image", state.h_clipboard_image),
        web.post("/api/support", state.h_support),
        web.post("/api/rent/open", state.h_rent_open),
        web.get("/api/rent/info", state.h_rent_info),
        web.get("/api/queue/saved", state.h_queue_saved),
        web.post("/api/queue/resume", state.h_queue_resume),
        web.post("/api/queue/discard", state.h_queue_discard),
        web.get("/api/job/{pid}", state.h_job),
        web.get("/api/input", state.h_input),
        web.post("/api/search-web", state.h_search_web),
        web.post("/api/report", state.h_report),
        web.post("/api/setup/reset", state.h_setup_reset),
        web.post("/api/generate/video", state.h_generate_video),
        web.post("/api/generate/krea", state.h_generate_krea),
        web.post("/api/upscale", state.h_upscale),
        web.post("/api/upscale_file", state.h_upscale_file),
        web.post("/api/grain", state.h_grain),
        web.get("/api/version", lambda _req: web.json_response({"version": APP_VERSION,
                                                                 "built": build_stamp()})),
        web.get("/api/update/check", state.h_update_check),
        web.post("/api/update/install", state.h_update_install),
        web.post("/api/generate/qwen", state.h_generate_qwen),
        web.post("/api/generate/inpaint", state.h_generate_inpaint),
        web.post("/api/generate/lipsync", state.h_generate_lipsync),
        web.get("/api/nodes", state.h_nodes),
        web.get("/api/models/files", state.h_models_files),
        web.post("/api/models/pick", state.h_models_pick),
        web.get("/api/models/compat", state.h_models_compat),
        web.post("/api/loras/custom/mode", state.h_loras_custom_mode),
        web.post("/api/models/open", state.h_models_open),
        web.post("/api/models/download", state.h_models_download),
        web.post("/api/models/download/cancel", state.h_models_download_cancel),
        web.post("/api/models/variant", state.h_models_variant),
        web.get("/api/wf/list", state.h_wf_list),
        web.post("/api/wf/add", state.h_wf_add),
        web.get("/api/wf/{id}", state.h_wf_get),
        web.delete("/api/wf/{id}", state.h_wf_delete),
        web.post("/api/generate/custom", state.h_generate_custom),
        web.get("/api/loras/custom", state.h_loras_custom),
        web.post("/api/loras/custom", state.h_loras_custom_set),
        web.post("/api/generate/refs", state.h_generate_refs),
        web.post("/api/generate/actor", state.h_generate_actor),
        web.post("/api/generate/swap", state.h_generate_swap),
        web.post("/api/generate/long", state.h_generate_long),
        web.post("/api/montage", state.h_montage),
        web.get("/api/ffmpeg/status", state.h_ffmpeg_status),
        web.post("/api/ffmpeg/download", state.h_ffmpeg_download),
        web.post("/api/ffmpeg/download/cancel", state.h_ffmpeg_download_cancel),
        web.post("/api/ffmpeg/set-folder", state.h_ffmpeg_set_folder),
        web.post("/api/ffmpeg/reset-folder", state.h_ffmpeg_reset_folder),
        web.post("/api/ffmpeg/open-folder", state.h_ffmpeg_open_folder),
        web.get("/api/ffmpeg/check-update", state.h_ffmpeg_check_update),
        web.post("/api/ffmpeg/montage", state.h_ffmpeg_montage),
        web.post("/api/ffmpeg/cancel", state.h_ffmpeg_cancel),
        web.get("/api/loras/registry", state.h_loras_registry),
        web.get("/api/loras/conflicts", state.h_loras_conflicts),
        web.post("/api/loras/meta", state.h_loras_meta_save),
        web.get("/api/loras/tokens", state.h_loras_tokens_get),
        web.post("/api/loras/tokens", state.h_loras_tokens_save),
        web.post("/api/loras/open-folder", state.h_loras_open_folder),
        web.post("/api/loras/download", state.h_loras_download),
        web.post("/api/loras/download/cancel", state.h_loras_download_cancel),
        web.get("/api/civitai/search", state.h_civitai_search),
        web.post("/api/civitai/resolve", state.h_civitai_resolve),
        web.get("/api/hf/search", state.h_hf_search),
        web.post("/api/hf/resolve", state.h_hf_resolve),
        web.get("/api/long/state", state.h_long_state),
        web.get("/api/long/project", state.h_long_project),
        web.post("/api/long/project", state.h_long_project),
        web.get("/api/long/ref/{run}/{name}", state.h_long_ref),
        web.post("/api/long/stage", state.h_long_stage),
        web.post("/api/generate/action", state.h_generate_action),
        web.get("/api/action/info", state.h_action_info),
        web.get("/api/cloud", state.h_cloud_get),
        web.post("/api/cloud", state.h_cloud_set),
        web.post("/api/cloud/test", state.h_cloud_test),
        web.post("/api/setup/cloud", state.h_setup_cloud),
        web.post("/api/interrupt", state.h_interrupt),
        web.get("/api/agent/status", state.h_agent_status),
        web.post("/api/agent/translate", state.h_agent_translate),
        web.post("/api/agent/install", state.h_agent_install),
        web.post("/api/agent/install/cancel", state.h_agent_install_cancel),
        web.post("/api/agent/model", state.h_agent_model),
        web.post("/api/agent/variant", state.h_agent_variant),
        web.post("/api/speech/transcribe", state.h_transcribe),
        web.post("/api/speech/folder", state.h_speech_folder),
        web.post("/api/agent/chat", state.h_agent_chat),
        web.get("/api/agent/chats", state.h_chats),
        web.delete("/api/agent/chats", state.h_chats_clear),
        web.get("/api/agent/chats/{id}", state.h_chat_get),
        web.put("/api/agent/chats/{id}", state.h_chat_put),
        web.delete("/api/agent/chats/{id}", state.h_chat_delete),
        web.post("/api/agent/skills", state.h_skill_upload),
        web.patch("/api/agent/skills/{id}", state.h_skill_patch),
        web.delete("/api/agent/skills/{id}", state.h_skill_delete),
        web.get("/api/results", state.h_results_get),
        web.post("/api/results", state.h_results_set),
        web.post("/api/results/backfill", state.h_results_backfill),
        web.post("/api/results/clean", state.h_results_clean),
        web.get("/api/library", state.h_library),
        web.post("/api/preview/keep", state.h_preview_keep),
        web.patch("/api/library/{id}", state.h_library_patch),
        web.delete("/api/library/{id}", state.h_library_delete),
        web.post("/api/library/delete", state.h_library_delete_many),
        web.get("/api/file/{id}", state.h_file),
        web.get("/api/srcfile/{id}", state.h_srcfile),
        web.get("/api/events", state.h_events),
        web.get("/api/agent/allow", state.h_agent_allow),
        web.post("/api/agent/allow", state.h_agent_allow),
        web.static("/ui", UI_DIR),
    ])
    app.add_routes(state.agents.routes())  # /api/agent/v1/...: the contract other programs use
    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--comfy", help="always use this ComfyUI URL (skips the setup wizard)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true", help="open the UI in the default browser")
    args = ap.parse_args()

    if not instance.acquire(DATA_DIR):
        other = instance.published(DATA_DIR) or {}
        where = other.get("url") or (f"http://127.0.0.1:{other['port']}" if other.get("port") else "")
        print("Minimax H3 Workspace уже запущена с этой папкой данных: " + str(DATA_DIR)
              + (f"\nОткрытая программа: {where}" if where else ""))
        return 3   # one folder, one copy: a second one would resume the same queue
    try:
        instance.publish(DATA_DIR, args.port)
        app = build_app(args.comfy, args.port)
        if args.open:
            async def open_browser(_app):
                asyncio.get_running_loop().call_later(1, webbrowser.open, f"http://127.0.0.1:{args.port}")
            app.on_startup.append(open_browser)
        web.run_app(app, host="127.0.0.1", port=args.port, print=lambda *_: print(f"Minimax H3 Workspace: http://127.0.0.1:{args.port}"))
    finally:
        instance.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
