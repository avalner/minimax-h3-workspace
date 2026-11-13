"""What every part of the server shares: where the data lives, the window's own paths,
the small helpers (a refusal as an HTTP answer, the build stamp, rounding, the clipboard), the
Library object and the same-origin guard.

Nothing here knows about aiohttp routes; `routes/` and `application.py` import from this module.
"""
import ctypes
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.parse
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path

from aiohttp import web

import engine
from http_errors import window_error
from storage.atomic import atomic_write_json
from storage.repositories import LibraryRepository
def http_refusal(error):
    """A domain refusal as the window's own HTTP answer (statuses live in `http_errors`)."""
    message, status = window_error(error)
    raise {
        400: web.HTTPBadRequest, 404: web.HTTPNotFound, 409: web.HTTPConflict,
        422: web.HTTPUnprocessableEntity, 500: web.HTTPInternalServerError,
    }.get(status, web.HTTPBadRequest)(text=message)


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

# frozen builds keep code inside the bundle but user data next to the exe
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
HOME = Path(sys.executable).parent if getattr(sys, "frozen", False) else ROOT
UI_DIR = ROOT / "ui"
# The window's files are served with an explicit type: `web.static` guesses from the Windows registry,
# where `.js` is often `text/plain` — a module script is then refused and the window opens blank.
# Only these extensions are served from /ui at all.
UI_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8", ".json": "application/json",
            ".mp4": "video/mp4", ".webm": "video/webm", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp", ".svg": "image/svg+xml", ".ico": "image/x-icon"}
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


def long_films():
    """The saved films of the Видеостудия, the newest first. Each record is the folder's project.json
    plus `run`, `folder` and `saved`: the films belong to a project, so listing them by project and
    moving them to another one is the same scan (see `project_assets` / `delete_project`)."""
    films = []
    if not LONG_DIR.is_dir():
        return films
    for path in LONG_DIR.glob("*/project.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            saved = path.stat().st_mtime
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        films.append({**data, "run": path.parent.name, "folder": path.parent, "saved": saved})
    films.sort(key=lambda film: film["saved"], reverse=True)
    return films


def freshest_long_run(project):
    """The film of this project that was saved last; empty when the project has no film."""
    for film in long_films():
        if str(film.get("project") or "") == str(project):
            return film["run"]
    return ""


def set_long_project(run, project):
    """The film moves to another project: only its label changes, the folder stays where it is."""
    path = LONG_DIR / str(run) / "project.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    data["project"] = str(project)
    atomic_write_json(path, data)
    return True


def drop_long_film(run):
    """A film goes with its project: the folder holds the scene states, the references and the stages."""
    folder = LONG_DIR / str(run)
    if not folder.is_dir() or folder.parent != LONG_DIR:
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return not folder.exists()

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
