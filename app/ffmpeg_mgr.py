"""Portable FFmpeg for the workspace: installation, probing, concat and studio montage.

FFmpeg always comes from a folder of its own, never from PATH:
  - get_ffmpeg_dir() resolves <install folder>\\ffmpeg, the folder chosen in the settings, or the
    app's own ffmpeg folder;
  - download_and_extract_ffmpeg_sync() fetches a GyanD essentials build, retries it through a
    mirror, then falls back to a BtbN build; the archive is extracted into a staging folder and
    swapped in only when the new ffmpeg.exe and ffprobe.exe both start, so a failed update never
    touches a working installation;
  - probe_media() and has_nvenc() describe clips and the hardware encoder;
  - run_concat() joins clips: stream copy when they already match, otherwise a scale/pad encode;
  - run_studio_montage() adds transitions, aspect conversion, speed, colour, a title and music.

Everything here is synchronous: the server calls these functions through asyncio.to_thread.
Percentage comes back as progress_cb(percent, text) and every long loop polls cancel_event
(a threading.Event), so the UI can stop a download or an encode at any moment.
"""

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

__all__ = [
    "get_default_ffmpeg_dir", "get_ffmpeg_dir", "find_ffmpeg", "parse_version_tuple",
    "get_ffmpeg_version", "get_ffmpeg_status", "check_ffmpeg_update", "probe_media",
    "has_nvenc", "download_and_extract_ffmpeg_sync", "run_ffmpeg_with_progress",
    "run_concat", "run_studio_montage",
]

# Official static Windows builds (GyanD) with a mirror and a BtbN fallback.
DEFAULT_RELEASE_URL = "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip"
DEFAULT_RELEASE_VERSION = "9.0.2"
MIRROR_RELEASE_URL = "https://ghproxy.net/https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip"
BTBN_FALLBACK_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip"

WORKSPACE_FOLDER_NAME = "MinimaxH3Workspace"
USER_AGENT = "MinimaxH3Workspace"

# Transitions ffmpeg's xfade filter knows; anything else would make the whole graph fail.
XFADE_TRANSITIONS = frozenset({
    "fade", "fadeblack", "fadewhite", "fadegrays", "dissolve", "pixelize",
    "wipeleft", "wiperight", "wipeup", "wipedown", "wipetl", "wipetr", "wipebl", "wipebr",
    "slideleft", "slideright", "slideup", "slidedown",
    "smoothleft", "smoothright", "smoothup", "smoothdown",
    "circlecrop", "rectcrop", "circleopen", "circleclose",
    "vertopen", "vertclose", "horzopen", "horzclose", "radial",
    "diagtl", "diagtr", "diagbl", "diagbr",
    "hlslice", "hrslice", "vuslice", "vdslice",
    "hblur", "squeezeh", "squeezev", "zoomin", "distance",
})


class _Cancelled(Exception):
    """Raised inside the download/extract loops when the caller's cancel event is set."""


# ---------- small helpers ----------

def _cfg(config, key, default=None):
    """value of config[key] for engine.Config, a plain dict, None or anything with get()."""
    if config is None:
        return default
    getter = getattr(config, "get", None)
    if getter is None:
        try:
            return config[key]
        except (KeyError, TypeError, IndexError):
            return default
    try:
        return getter(key, default)
    except TypeError:
        try:
            return getter(key)
        except Exception:
            return default
    except Exception:
        return default


def _to_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _no_window():
    """CREATE_NO_WINDOW on Windows so console windows never flash; 0 elsewhere."""
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _app_root():
    """Folder that holds the program (the repository root in a checkout)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _existing_file(value):
    """Path when value points at an existing file, otherwise None (never looks at PATH)."""
    if not value:
        return None
    try:
        path = Path(value)
    except (TypeError, ValueError):
        return None
    try:
        return path if path.is_file() else None
    except OSError:
        return None


def _is_cancelled(event):
    try:
        return bool(event is not None and event.is_set())
    except Exception:
        return False


def _report(progress_cb, percent, text):
    """One progress step; a broken UI callback must not kill a running export."""
    if not progress_cb:
        return
    try:
        progress_cb(max(0, min(100, int(percent))), str(text))
    except Exception:
        pass


def _progress_download(progress_cb, info):
    """One download step as percent/text.

    A one-argument callback that expects the whole info dict (bytes, total, speed) is accepted as
    well: the call with two arguments fails with TypeError, and the dict is passed instead.
    """
    if not progress_cb:
        return
    percent = max(0, min(100, int(info.get("pct") or 0)))
    text = info.get("text") or f"Скачивание FFmpeg: {percent}%"
    try:
        progress_cb(percent, text)
        return
    except TypeError:
        pass
    except Exception:
        return
    try:
        progress_cb(dict(info))
    except Exception:
        pass


def _same_dir(a, b):
    try:
        return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))
    except (OSError, ValueError):
        return False


def _remove_file(path):
    if not path:
        return
    try:
        target = Path(path)
        if target.is_file():
            target.unlink()
    except (OSError, TypeError, ValueError):
        pass


def _stamp():
    return f"{int(time.time() * 1000)}_{os.getpid()}_{threading.get_ident()}"


# ---------- location ----------

def get_default_ffmpeg_dir(workspace_root=None):
    """Default FFmpeg folder: <workspace_root>\\ffmpeg, the app's own folder, or a known install."""
    if workspace_root:
        return Path(workspace_root) / "ffmpeg"

    local = _app_root() / "ffmpeg"
    try:
        if local.is_dir():
            return local
    except OSError:
        pass

    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        candidate = Path(f"{letter}:\\{WORKSPACE_FOLDER_NAME}")
        try:
            if candidate.is_dir():
                return candidate / "ffmpeg"
        except OSError:
            continue

    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / WORKSPACE_FOLDER_NAME / "ffmpeg"


def get_ffmpeg_dir(config, workspace_root=None):
    """Active FFmpeg folder: chosen by the user, else <install folder>\\ffmpeg, else the default."""
    custom = _cfg(config, "ffmpeg_dir")
    if custom is not None and str(custom).strip():
        try:
            return Path(str(custom).strip())
        except (OSError, ValueError):
            pass

    install_dir = _cfg(config, "install_dir")
    if install_dir:
        try:
            if Path(str(install_dir)).is_dir():
                return Path(str(install_dir)) / "ffmpeg"
        except (OSError, ValueError):
            pass
    return get_default_ffmpeg_dir(workspace_root)


def find_ffmpeg(folder):
    """(ffmpeg.exe, ffprobe.exe) inside folder/bin or folder itself; (None, None) when absent.

    Only the given folder is inspected - the system PATH is deliberately ignored.
    """
    if not folder:
        return None, None
    try:
        folder = Path(folder)
        if not folder.is_dir():
            return None, None
    except (OSError, TypeError, ValueError):
        return None, None

    dirs = [folder / "bin", folder]
    ffmpeg_exe = None
    ffprobe_exe = None
    for directory in dirs:
        try:
            if ffmpeg_exe is None:
                candidate = directory / "ffmpeg.exe"
                if candidate.is_file():
                    ffmpeg_exe = candidate
            if ffprobe_exe is None:
                candidate = directory / "ffprobe.exe"
                if candidate.is_file():
                    ffprobe_exe = candidate
        except OSError:
            continue

    if ffmpeg_exe and not ffprobe_exe:
        try:
            sibling = ffmpeg_exe.parent / "ffprobe.exe"
            if sibling.is_file():
                ffprobe_exe = sibling
        except OSError:
            pass
    return ffmpeg_exe, ffprobe_exe


def _resolve_ffprobe(ffmpeg_exe, ffprobe_exe=None):
    """ffprobe next to ffmpeg (same folder), with a short fallback search around it.

    The probe always belongs to the same portable build as ffmpeg; nothing is taken from PATH.
    """
    probe = _existing_file(ffprobe_exe)
    if probe:
        return probe
    ffmpeg = _existing_file(ffmpeg_exe)
    if not ffmpeg:
        return None

    candidates = [
        ffmpeg.parent / "ffprobe.exe",
        ffmpeg.parent.parent / "ffprobe.exe",
        ffmpeg.parent.parent / "bin" / "ffprobe.exe",
        ffmpeg.parent / "bin" / "ffprobe.exe",
    ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue

    for base in (ffmpeg.parent, ffmpeg.parent.parent):
        try:
            _, found = find_ffmpeg(base)
        except OSError:
            found = None
        if found:
            return found
    return None


# ---------- version and status ----------

def parse_version_tuple(v_str):
    """Numeric version from a version string.

    '9.0.2-essentials_build' -> (9, 0, 2); 'N-118000-g1234abc' -> (118000,);
    text without digits ('не найден') -> (0,). Never raises.
    """
    if not v_str:
        return (0,)
    text = str(v_str).strip()
    if not text:
        return (0,)

    build = re.search(r"\bN-(\d+)", text, re.I)
    if build:
        return (int(build.group(1)),)

    dotted = re.search(r"(\d+(?:\.\d+)+)", text)
    if dotted:
        try:
            return tuple(int(part) for part in dotted.group(1).split("."))
        except ValueError:
            return (0,)

    digits = re.findall(r"\d+", text)
    if not digits:
        return (0,)
    try:
        return tuple(int(part) for part in digits)
    except ValueError:
        return (0,)


def get_ffmpeg_version(ffmpeg_exe):
    """Clean version string from 'ffmpeg -version'; None when the binary is missing or broken."""
    exe = _existing_file(ffmpeg_exe)
    if not exe:
        return None
    try:
        res = subprocess.run(
            [str(exe), "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", timeout=10, creationflags=_no_window())
    except (OSError, subprocess.SubprocessError):
        return None

    first_line = (res.stdout or "").splitlines()[0].strip() if res.stdout else ""
    if not first_line:
        return None

    match = re.search(r"ffmpeg version\s+(\S+)", first_line, re.I)
    if not match:
        return first_line[:40]
    token = match.group(1)
    dotted = re.match(r"[vn]?(\d+(?:\.\d+)+)", token, re.I)
    if dotted:
        return dotted.group(1)
    return token


def get_ffmpeg_status(config, workspace_root=None):
    """Full status of the portable FFmpeg for the UI; never raises when nothing is installed."""
    current_dir = get_ffmpeg_dir(config, workspace_root)
    default_dir = get_default_ffmpeg_dir(workspace_root or _cfg(config, "install_dir"))

    ffmpeg_exe, ffprobe_exe = find_ffmpeg(current_dir)
    if ffmpeg_exe and not ffprobe_exe:
        ffprobe_exe = _resolve_ffprobe(ffmpeg_exe, None)

    return {
        "installed": bool(ffmpeg_exe and ffprobe_exe),
        "ffmpeg_path": str(ffmpeg_exe) if ffmpeg_exe else None,
        "ffprobe_path": str(ffprobe_exe) if ffprobe_exe else None,
        "version": get_ffmpeg_version(ffmpeg_exe) if ffmpeg_exe else None,
        "dir": str(current_dir),
        "default_dir": str(default_dir),
        "is_default": _same_dir(current_dir, default_dir),
    }


def check_ffmpeg_update(current_version=None):
    """Latest GyanD release tag from GitHub; {'ok': False, 'error': ...} when offline."""
    current = ".".join(map(str, parse_version_tuple(current_version))) if current_version else ""

    try:
        request = urllib.request.Request(
            "https://api.github.com/repos/GyanD/codexffmpeg/releases/latest",
            headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        tag = str(data.get("tag_name") or "").strip().lstrip("vn").strip()
        version_current = parse_version_tuple(current_version)
        version_tag = parse_version_tuple(tag)
        return {
            "ok": True,
            "latest_version": tag or DEFAULT_RELEASE_VERSION,
            "has_update": bool(current_version and tag and version_tag > version_current),
            "current_version": current,
            "url": data.get("html_url") or "https://github.com/GyanD/codexffmpeg/releases",
        }
    except Exception as e:   # offline, rate limited, malformed answer - the UI only needs a reason
        return {
            "ok": False,
            "latest_version": DEFAULT_RELEASE_VERSION,
            "has_update": False,
            "current_version": current,
            "error": str(e),
        }


# ---------- media ----------

def probe_media(ffprobe_exe, file_path):
    """Duration, size, first video and first audio stream of a file.

    {} when ffprobe is missing, {'error': ...} when the file cannot be probed; never raises.
    """
    probe = _existing_file(ffprobe_exe)
    if not probe:
        return {}

    try:
        target = Path(file_path)
    except (TypeError, ValueError):
        return {"error": "Некорректный путь к файлу"}
    try:
        if not target.is_file():
            return {"error": f"Файл не найден: {file_path}"}
    except OSError:
        return {"error": f"Файл не найден: {file_path}"}

    cmd = [
        str(probe),
        "-v", "error",
        "-show_entries",
        "stream=index,codec_type,codec_name,width,height,r_frame_rate,duration:format=duration,size",
        "-of", "json",
        str(target),
    ]
    try:
        res = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", timeout=20, creationflags=_no_window())
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": f"ffprobe: {e}"}

    raw = (res.stdout or "").strip()
    if not raw:
        detail = (res.stderr or "").strip()[:300] or f"код {res.returncode}"
        return {"error": f"ffprobe: {detail}"}
    try:
        data = json.loads(raw)
    except ValueError as e:
        return {"error": f"ffprobe: {e}"}
    if not isinstance(data, dict):
        return {"error": "ffprobe: неожиданный ответ"}

    fmt = data.get("format") if isinstance(data.get("format"), dict) else {}
    info = {
        "duration": _to_float(fmt.get("duration")),
        "size": _to_int(fmt.get("size")),
        "video": None,
        "audio": None,
    }
    for stream in data.get("streams") or []:
        if not isinstance(stream, dict):
            continue
        if stream.get("codec_type") == "video" and info["video"] is None:
            width = _to_int(stream.get("width"))
            height = _to_int(stream.get("height"))
            fps = 24.0
            rate = str(stream.get("r_frame_rate") or "")
            if "/" in rate:
                try:
                    num, den = rate.split("/", 1)
                    if float(den) > 0:
                        fps = round(float(num) / float(den), 2)
                except (TypeError, ValueError):
                    pass
            info["video"] = {
                "width": width,
                "height": height,
                "codec": str(stream.get("codec_name") or "h264"),
                "fps": fps,
                "duration": _to_float(stream.get("duration"), info["duration"]),
            }
        elif stream.get("codec_type") == "audio" and info["audio"] is None:
            info["audio"] = {
                "codec": str(stream.get("codec_name") or "aac"),
                "duration": _to_float(stream.get("duration"), info["duration"]),
            }
    return info


def has_nvenc(ffmpeg_exe):
    """True when this portable build lists the h264_nvenc encoder."""
    exe = _existing_file(ffmpeg_exe)
    if not exe:
        return False
    try:
        res = subprocess.run(
            [str(exe), "-hide_banner", "-encoders"], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=15,
            creationflags=_no_window())
    except (OSError, subprocess.SubprocessError):
        return False
    return "h264_nvenc" in (res.stdout or "")


# ---------- download and install ----------

def _download_file(url, destination, progress_cb, cancel_event):
    """Download one URL to destination, polling cancel_event between 512 KB chunks."""
    _progress_download(progress_cb, {"status": "starting", "pct": 0, "text": "Скачивание FFmpeg…"})
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    downloaded = 0
    started = time.time()
    last_report = 0.0

    with urllib.request.urlopen(request, timeout=30) as response:
        total = _to_int(response.headers.get("Content-Length"))
        with open(destination, "wb") as stream:
            while True:
                if _is_cancelled(cancel_event):
                    raise _Cancelled()
                chunk = response.read(512 * 1024)
                if not chunk:
                    break
                stream.write(chunk)
                downloaded += len(chunk)

                now = time.time()
                if progress_cb and (now - last_report >= 0.25 or (total and downloaded >= total)):
                    last_report = now
                    percent = min(95, int(downloaded * 100 / total)) if total > 0 else min(95, downloaded // (512 * 1024))
                    megabytes = downloaded / (1024 * 1024)
                    total_megabytes = total / (1024 * 1024) if total else 0.0
                    speed = megabytes / max(0.001, now - started)
                    if total:
                        text = f"Скачивание FFmpeg: {percent}% ({megabytes:.1f} из {total_megabytes:.1f} МБ, {speed:.1f} МБ/с)"
                    else:
                        text = f"Скачивание FFmpeg: {megabytes:.1f} МБ ({speed:.1f} МБ/с)"
                    _progress_download(progress_cb, {
                        "status": "downloading", "pct": percent, "bytes": downloaded,
                        "total": total, "speed_mb": round(speed, 1), "text": text,
                    })

    if _is_cancelled(cancel_event):
        raise _Cancelled()
    if total and downloaded < total:
        raise RuntimeError(f"загружено {downloaded} из {total} байт")


def _safe_extract(archive, destination, cancel_event):
    """Extract a zip entry by entry, stopping on cancel and refusing paths outside destination."""
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            if _is_cancelled(cancel_event):
                raise _Cancelled()
            name = item.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                raise RuntimeError(f"недопустимый путь в архиве: {item.filename}")
            target = (root / name).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError(f"недопустимый путь в архиве: {item.filename}")
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(item) as source, open(target, "wb") as output:
                shutil.copyfileobj(source, output)


def _install_staged(target_dir, staged_dir):
    """Swap staged binaries into target_dir\\bin, keeping the previous install on any failure."""
    bin_dir = target_dir / "bin"
    backup = target_dir / "_bin_old"

    # Recover from an interrupted earlier swap instead of throwing the old binaries away.
    if not bin_dir.exists() and backup.is_dir():
        try:
            backup.rename(bin_dir)
        except OSError:
            pass
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)

    moved_old = False
    try:
        if bin_dir.exists():
            bin_dir.rename(backup)
            moved_old = True
        staged_dir.rename(bin_dir)
    except OSError:
        if moved_old and not bin_dir.exists():
            try:
                backup.rename(bin_dir)
            except OSError:
                pass
        raise
    if moved_old:
        shutil.rmtree(backup, ignore_errors=True)

    # Convenience copies next to the folder, so a user can find ffmpeg.exe without opening bin.
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        source = bin_dir / name
        if source.is_file():
            try:
                shutil.copy2(source, target_dir / name)
            except OSError:
                pass


def download_and_extract_ffmpeg_sync(target_dir, progress_cb=None, cancel_event=None):
    """Download and install portable FFmpeg into target_dir (synchronous, for asyncio.to_thread).

    Returns {'ok': True, 'version', 'ffmpeg_path', 'ffprobe_path', 'dir'} or
    {'ok': False, 'error'} (with 'cancelled': True when the user stopped it).
    Progress is reported as progress_cb(percent, text).
    """
    target_dir = Path(target_dir)
    if _is_cancelled(cancel_event):
        return {"ok": False, "error": "Отменено пользователем", "cancelled": True}

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": f"Не удалось создать папку FFmpeg: {e}"}

    work_dir = target_dir / f"_install_{_stamp()}"
    archive = work_dir / "ffmpeg.zip"
    extract_dir = work_dir / "files"
    staged_dir = work_dir / "bin"
    last_error = None

    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        downloaded = False
        for index, url in enumerate((DEFAULT_RELEASE_URL, MIRROR_RELEASE_URL, BTBN_FALLBACK_URL)):
            if _is_cancelled(cancel_event):
                return {"ok": False, "error": "Отменено пользователем", "cancelled": True}
            if index:
                _progress_download(progress_cb, {
                    "status": "starting", "pct": 0, "text": "Пробую зеркало FFmpeg…"})
            try:
                _download_file(url, archive, progress_cb, cancel_event)
            except _Cancelled:
                return {"ok": False, "error": "Отменено пользователем", "cancelled": True}
            except Exception as e:
                last_error = e
                _remove_file(archive)
                continue
            if not zipfile.is_zipfile(archive):
                last_error = RuntimeError("скачанный файл не является zip-архивом")
                _remove_file(archive)
                continue
            downloaded = True
            break

        if not downloaded:
            return {"ok": False, "error": f"Не удалось скачать FFmpeg: {last_error}"}
        if _is_cancelled(cancel_event):
            return {"ok": False, "error": "Отменено пользователем", "cancelled": True}

        _progress_download(progress_cb, {
            "status": "extracting", "pct": 96, "text": "Распаковка FFmpeg…"})
        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            _safe_extract(archive, extract_dir, cancel_event)
        except _Cancelled:
            return {"ok": False, "error": "Отменено пользователем", "cancelled": True}
        except (zipfile.BadZipFile, OSError, RuntimeError) as e:
            return {"ok": False, "error": f"Ошибка распаковки: {e}"}

        found_ffmpeg = next((p for p in extract_dir.rglob("ffmpeg.exe") if p.is_file()), None)
        found_ffprobe = next((p for p in extract_dir.rglob("ffprobe.exe") if p.is_file()), None)
        if not found_ffmpeg or not found_ffprobe:
            return {"ok": False, "error": "В скачанном архиве нет ffmpeg.exe и ffprobe.exe"}

        # Stage both binaries together, so a shared build brings its DLLs along.
        staged_dir.mkdir(parents=True, exist_ok=True)
        source_dir = found_ffmpeg.parent
        for item in source_dir.iterdir():
            if item.is_file():
                shutil.copy2(item, staged_dir / item.name)
            elif item.is_dir():
                shutil.copytree(item, staged_dir / item.name, dirs_exist_ok=True)
        if not (staged_dir / "ffprobe.exe").is_file():
            shutil.copy2(found_ffprobe, staged_dir / "ffprobe.exe")

        if not (staged_dir / "ffmpeg.exe").is_file() or not (staged_dir / "ffprobe.exe").is_file():
            return {"ok": False, "error": "В скачанном архиве нет ffmpeg.exe и ffprobe.exe"}

        # The new build has to start before it replaces anything that already works.
        staged_version = get_ffmpeg_version(staged_dir / "ffmpeg.exe")
        if not staged_version:
            return {"ok": False, "error": "Скачанный ffmpeg.exe не запускается"}
        if _is_cancelled(cancel_event):
            return {"ok": False, "error": "Отменено пользователем", "cancelled": True}

        try:
            _install_staged(target_dir, staged_dir)
        except OSError as e:
            return {"ok": False, "error": f"Не удалось установить FFmpeg (он сейчас используется?): {e}"}

        ffmpeg_exe, ffprobe_exe = find_ffmpeg(target_dir)
        version = get_ffmpeg_version(ffmpeg_exe) or staged_version
        return {
            "ok": True,
            "version": version,
            "ffmpeg_path": str(ffmpeg_exe) if ffmpeg_exe else "",
            "ffprobe_path": str(ffprobe_exe) if ffprobe_exe else "",
            "dir": str(target_dir),
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ---------- running ffmpeg ----------

def _kill_process(proc):
    try:
        if proc.poll() is None:
            proc.kill()
    except OSError:
        pass


def _wait_quiet(proc, timeout=10.0):
    """Wait for the process, killing it when it does not exit on its own."""
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process(proc)
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            pass


def _wait_with_cancel(proc, cancel_event):
    """Wait for the process while polling the cancel event; True when it had to be killed."""
    while True:
        if _is_cancelled(cancel_event):
            _kill_process(proc)
            _wait_quiet(proc)
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.1)


def run_ffmpeg_with_progress(cmd, total_duration, output_path=None, progress_cb=None,
                             cancel_event=None, creationflags=0, label="Монтаж FFmpeg"):
    """Run one ffmpeg command, showing percentage from its -progress pipe:1 output.

    stderr is drained in its own thread and stdout in another, so the pipes can never deadlock.
    The cancel event is polled every 0.25 s even when ffmpeg prints nothing; a killed run removes
    the half-written output. Returns {'ok': True, 'stderr'} or {'ok': False, 'error', 'stderr'}.
    """
    total_duration = _to_float(total_duration)
    if _is_cancelled(cancel_event):
        _remove_file(output_path)
        return {"ok": False, "error": "Отменено пользователем", "stderr": "", "cancelled": True}

    try:
        proc = subprocess.Popen(
            [str(part) for part in cmd],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags or 0,
        )
    except (OSError, ValueError) as e:
        return {"ok": False, "error": f"Не удалось запустить FFmpeg: {e}", "stderr": ""}

    stderr_lines = []
    stderr_lock = threading.Lock()

    def drain_stderr():
        try:
            for line in proc.stderr:
                if not line:
                    continue
                with stderr_lock:
                    stderr_lines.append(line)
                    del stderr_lines[:-200]
        except (OSError, ValueError):
            pass

    progress_lines = queue.Queue()

    def read_progress():
        try:
            for line in proc.stdout:
                progress_lines.put(line)
        except (OSError, ValueError):
            pass
        finally:
            progress_lines.put(None)

    stderr_thread = threading.Thread(target=drain_stderr, name="ffmpeg-stderr", daemon=True)
    progress_thread = threading.Thread(target=read_progress, name="ffmpeg-progress", daemon=True)
    stderr_thread.start()
    progress_thread.start()

    _report(progress_cb, 5, f"{label}: запуск…")
    last_percent = 5
    cancelled = False

    try:
        while True:
            if _is_cancelled(cancel_event):
                cancelled = True
                _kill_process(proc)
                break
            try:
                line = progress_lines.get(timeout=0.25)
            except queue.Empty:
                if proc.poll() is not None and not progress_thread.is_alive():
                    break
                continue
            if line is None:
                break

            text = line.strip()
            key, _, value = text.partition("=")
            current_seconds = None
            if key == "out_time_us":
                current_seconds = _to_float(value) / 1_000_000
            elif key == "out_time_ms":   # ffmpeg prints microseconds here despite the name
                current_seconds = _to_float(value) / 1_000_000
            elif key == "out_time":
                parts = value.split(":")
                if len(parts) == 3:
                    current_seconds = (_to_float(parts[0]) * 3600 + _to_float(parts[1]) * 60
                                       + _to_float(parts[2]))
            if current_seconds is not None and total_duration > 0:
                percent = min(99, max(5, int(current_seconds / total_duration * 100)))
                if percent > last_percent:
                    last_percent = percent
                    _report(progress_cb, percent, f"{label}: {percent}%")
            elif text == "progress=end":
                _report(progress_cb, 100, f"{label}: готово!")

        if not cancelled:
            cancelled = _wait_with_cancel(proc, cancel_event)
    finally:
        _wait_quiet(proc)
        progress_thread.join(timeout=2.0)
        stderr_thread.join(timeout=2.0)

    with stderr_lock:
        full_stderr = "".join(stderr_lines).strip()

    if cancelled:
        _remove_file(output_path)
        return {"ok": False, "error": "Отменено пользователем", "stderr": full_stderr, "cancelled": True}

    if proc.returncode != 0:
        _remove_file(output_path)
        tail = full_stderr[-800:]
        return {
            "ok": False,
            "error": f"FFmpeg завершился с кодом {proc.returncode}: {tail}",
            "stderr": full_stderr,
        }

    if output_path:
        try:
            result = Path(output_path)
            if not result.is_file() or result.stat().st_size == 0:
                _remove_file(result)
                return {"ok": False, "error": "FFmpeg не создал файл результата", "stderr": full_stderr}
        except (OSError, TypeError, ValueError):
            pass

    _report(progress_cb, 100, f"{label}: готово!")
    return {"ok": True, "stderr": full_stderr}


# ---------- montage helpers ----------

def _video_encode_args(use_nvenc):
    """Encoder settings: NVENC when the build has it, libx264 otherwise."""
    if use_nvenc:
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20", "-b:v", "0", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]


def _run_encode(build_cmd, ffmpeg_exe, total_duration, output_path, progress_cb, cancel_event,
                creationflags, label):
    """Encode with NVENC when available and retry on libx264 if that attempt fails."""
    hardware = has_nvenc(ffmpeg_exe)
    result = run_ffmpeg_with_progress(
        build_cmd(hardware), total_duration, output_path, progress_cb=progress_cb,
        cancel_event=cancel_event, creationflags=creationflags, label=label)
    if not result.get("ok") and hardware and not result.get("cancelled"):
        _report(progress_cb, 5, f"{label}: NVENC недоступен, перекодирую на CPU…")
        result = run_ffmpeg_with_progress(
            build_cmd(False), total_duration, output_path, progress_cb=progress_cb,
            cancel_event=cancel_event, creationflags=creationflags, label=label)
    return result


def _concat_line(path):
    """One 'file ...' line for the concat demuxer, with quotes escaped its way."""
    resolved = str(Path(path).resolve()).replace("\\", "/")
    return "file '" + resolved.replace("'", "'\\''") + "'"


def _atempo_chain(speed):
    """atempo accepts 0.5..2.0 only; longer or slower speeds become a chain of them."""
    factors = []
    remaining = float(speed)
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(max(0.5, min(2.0, remaining)))
    return [f"atempo={factor:0.4f}" for factor in factors]


# ---------- concat ----------

def run_concat(ffmpeg_exe, ffprobe_exe, clip_paths, output_path, audio_path=None, audio_opts=None,
               progress_cb=None, cancel_event=None):
    """Join clips quickly: stream copy when they match, otherwise scale/pad and re-encode.

    audio_path adds background music. audio_opts: loop (True), mode ('mix' | 'replace'),
    music_vol (0.8), video_vol (1.0). Returns {'ok', 'fast_copy', 'duration', 'res'} and raises
    RuntimeError when ffmpeg is missing or fails; cancelling also removes the partial output.
    """
    ffmpeg = _existing_file(ffmpeg_exe)
    if not ffmpeg:
        raise RuntimeError(f"FFmpeg не найден: {ffmpeg_exe}")
    if not clip_paths:
        raise ValueError("Нет клипов для монтажа")

    clips = [Path(clip) for clip in clip_paths]
    for clip in clips:
        if not clip.is_file():
            raise RuntimeError(f"Клип не найден: {clip}")
    if _is_cancelled(cancel_event):
        raise RuntimeError("Отменено пользователем")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    audio_opts = audio_opts if isinstance(audio_opts, dict) else {}
    music = _existing_file(audio_path)

    ffprobe = _resolve_ffprobe(ffmpeg, ffprobe_exe)
    probes = []
    for clip in clips:
        if _is_cancelled(cancel_event):
            raise RuntimeError("Отменено пользователем")
        probes.append(probe_media(ffprobe, clip))

    raw_durations = [_to_float(probe.get("duration")) for probe in probes]
    known_total = all(duration > 0 for duration in raw_durations)
    total_duration = (sum(raw_durations) if known_total
                      else sum(duration if duration > 0 else 5.0 for duration in raw_durations))
    first_video = probes[0].get("video") if probes else None
    n = len(clips)

    # Stream copy only when container, codecs, size and audio layout all agree.
    can_stream_copy = bool(first_video) and music is None
    if can_stream_copy:
        first_audio = probes[0].get("audio")
        for probe in probes[1:]:
            video = probe.get("video")
            audio = probe.get("audio")
            if not video:
                can_stream_copy = False
                break
            if (video["width"], video["height"], video["codec"]) != (
                    first_video["width"], first_video["height"], first_video["codec"]):
                can_stream_copy = False
                break
            if bool(audio) != bool(first_audio):
                can_stream_copy = False
                break
            if audio and first_audio and audio.get("codec") != first_audio.get("codec"):
                can_stream_copy = False
                break

    creationflags = _no_window()

    if can_stream_copy:
        _report(progress_cb, 5, "Быстрая склейка без потери качества…")
        concat_txt = output.parent / f"_concat_{_stamp()}.txt"
        try:
            concat_txt.write_text("".join(_concat_line(clip) + "\n" for clip in clips), encoding="utf-8")
            copy_cmd = [
                str(ffmpeg), "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt),
                "-c", "copy", "-movflags", "+faststart", "-progress", "pipe:1", str(output),
            ]
            result = run_ffmpeg_with_progress(
                copy_cmd, total_duration, output, progress_cb=progress_cb,
                cancel_event=cancel_event, creationflags=creationflags, label="Склейка FFmpeg")
        finally:
            _remove_file(concat_txt)

        if result.get("ok"):
            _report(progress_cb, 100, "Готово!")
            return {
                "ok": True,
                "fast_copy": True,
                "duration": round(total_duration, 1),
                "res": f"{first_video['width']}×{first_video['height']}",
            }
        if result.get("cancelled"):
            raise RuntimeError("Отменено пользователем")
        _report(progress_cb, 5, "Быстрая склейка не удалась, перекодирую…")

    target_w = _to_int(first_video.get("width")) if first_video else 1280
    target_h = _to_int(first_video.get("height")) if first_video else 720
    if target_w < 2 or target_h < 2:
        target_w, target_h = 1280, 720
    target_w = (target_w // 2) * 2
    target_h = (target_h // 2) * 2

    inputs = []
    for clip in clips:
        inputs.extend(["-i", str(clip.resolve())])
    if music:
        if audio_opts.get("loop", True):
            inputs.extend(["-stream_loop", "-1"])
        inputs.extend(["-i", str(music.resolve())])
    music_index = n

    filter_parts = []
    for i in range(n):
        filter_parts.append(
            f"[{i}:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24[v{i}]")

    clips_have_audio = any(probe.get("audio") for probe in probes)
    if clips_have_audio:
        for i in range(n):
            if probes[i].get("audio"):
                filter_parts.append(
                    f"[{i}:a]aformat=sample_rates=44100:channel_layouts=stereo[a{i}]")
            else:
                duration = _to_float(probes[i].get("duration")) or 5.0
                filter_parts.append(
                    f"anullsrc=r=44100:cl=stereo,atrim=0:{duration:0.3f}[a{i}]")
        joined = "".join(f"[v{i}][a{i}]" for i in range(n))
        filter_parts.append(f"{joined}concat=n={n}:v=1:a=1[vcat][acat]")
    else:
        joined = "".join(f"[v{i}]" for i in range(n))
        filter_parts.append(f"{joined}concat=n={n}:v=1:a=0[vcat]")

    map_video = "[vcat]"
    map_audio = None
    if music:
        music_volume = max(0.0, min(4.0, _to_float(audio_opts.get("music_vol"), 0.8)))
        video_volume = max(0.0, min(4.0, _to_float(audio_opts.get("video_vol"), 1.0)))
        audio_mode = str(audio_opts.get("mode") or "mix")
        if clips_have_audio and audio_mode == "mix":
            filter_parts.append(
                f"[{music_index}:a]volume={music_volume:0.3f}[mus];"
                f"[acat]volume={video_volume:0.3f}[vid_a];"
                f"[vid_a][mus]amix=inputs=2:duration=first:dropout_transition=2[aout]")
            map_audio = "[aout]"
        else:
            filter_parts.append(f"[{music_index}:a]volume={music_volume:0.3f}[aout]")
            map_audio = "[aout]"
    elif clips_have_audio:
        map_audio = "[acat]"

    filter_complex = ";".join(filter_parts)
    bound_duration = known_total or bool(music)

    def build_cmd(use_nvenc):
        args = [str(ffmpeg), "-y"] + inputs + [
            "-filter_complex", filter_complex, "-map", map_video]
        if map_audio:
            args.extend(["-map", map_audio, "-c:a", "aac", "-b:a", "192k"])
        else:
            args.append("-an")
        args.extend(_video_encode_args(use_nvenc))
        args.extend(["-movflags", "+faststart", "-progress", "pipe:1"])
        if bound_duration:
            args.extend(["-t", f"{total_duration:.2f}"])
        args.append(str(output))
        return args

    if _is_cancelled(cancel_event):
        raise RuntimeError("Отменено пользователем")
    result = _run_encode(build_cmd, ffmpeg, total_duration, output, progress_cb, cancel_event,
                         creationflags, "Монтаж FFmpeg")
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "Ошибка выполнения FFmpeg")

    return {
        "ok": True,
        "fast_copy": False,
        "duration": round(total_duration, 1),
        "res": f"{target_w}×{target_h}",
    }


# ---------- studio montage ----------

def run_studio_montage(ffmpeg_exe, ffprobe_exe, clip_paths, output_path, options=None,
                       progress_cb=None, cancel_event=None):
    """Full studio montage: transitions, aspect conversion, speed, colour, title and music.

    options: cuts, speed, transition (xfade name), transition_dur, aspect ('16:9', '9:16', '1:1',
    '21:9', 'original'), fit_mode ('blur', 'crop', 'pad'), color {brightness, contrast, saturation,
    filter}, title {text, position, size, color}, audio_path, audio_opts {loop, music_vol,
    video_vol, mode, fade}. Returns {'ok', 'fast_copy', 'duration', 'res'} and raises RuntimeError
    when ffmpeg fails; cancelling removes the partial output.
    """
    ffmpeg = _existing_file(ffmpeg_exe)
    if not ffmpeg:
        raise RuntimeError(f"FFmpeg не найден: {ffmpeg_exe}")
    if not clip_paths:
        raise ValueError("Нет клипов для монтажа")

    clips = [Path(clip) for clip in clip_paths]
    for clip in clips:
        if not clip.is_file():
            raise RuntimeError(f"Клип не найден: {clip}")
    if _is_cancelled(cancel_event):
        raise RuntimeError("Отменено пользователем")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    options = options if isinstance(options, dict) else {}
    n = len(clips)

    cuts_raw = options.get("cuts") if isinstance(options.get("cuts"), list) else []
    cuts = []
    for i in range(n):
        try:
            value = cuts_raw[i] if i < len(cuts_raw) else None
            cuts.append(float(value) if value not in (None, "") else None)
        except (TypeError, ValueError):
            cuts.append(None)

    speed = _to_float(options.get("speed"), 1.0)
    if speed <= 0.1 or speed > 5.0:
        speed = 1.0

    transition = str(options.get("transition") or "none").strip().lower()
    if transition and transition != "none" and transition not in XFADE_TRANSITIONS:
        transition = "fade"
    if transition == "none":
        transition = "none"

    transition_dur = _to_float(options.get("transition_dur"), 0.5)
    if transition_dur <= 0:
        transition_dur = 0.5

    aspect = str(options.get("aspect") or "original").strip().lower()
    if aspect not in ("16:9", "9:16", "1:1", "21:9", "original"):
        aspect = "original"

    fit_mode = str(options.get("fit_mode") or "blur").strip().lower()
    if fit_mode not in ("blur", "crop", "pad"):
        fit_mode = "blur"

    color = options.get("color") if isinstance(options.get("color"), dict) else {}
    title = options.get("title") if isinstance(options.get("title"), dict) else {}
    audio_opts = options.get("audio_opts") if isinstance(options.get("audio_opts"), dict) else {}
    music = _existing_file(options.get("audio_path"))

    ffprobe = _resolve_ffprobe(ffmpeg, ffprobe_exe)
    probes = []
    for clip in clips:
        if _is_cancelled(cancel_event):
            raise RuntimeError("Отменено пользователем")
        probes.append(probe_media(ffprobe, clip))

    raw_durations = []
    effective_durations = []
    for i, probe in enumerate(probes):
        duration = _to_float(probe.get("duration"))
        if duration <= 0:
            duration = 5.0
        raw_durations.append(duration)
        cut = cuts[i]
        used = cut if (cut is not None and 0.2 < cut < duration) else duration
        effective_durations.append(used / speed)
    durations_known = all(_to_float(probe.get("duration")) > 0 for probe in probes)

    first_video = probes[0].get("video") if probes else None

    if aspect == "16:9":
        target_w, target_h = 1920, 1080
    elif aspect == "9:16":
        target_w, target_h = 1080, 1920
    elif aspect == "1:1":
        target_w, target_h = 1080, 1080
    elif aspect == "21:9":
        target_w, target_h = 2560, 1080
    else:
        target_w = _to_int(first_video.get("width")) if first_video else 1280
        target_h = _to_int(first_video.get("height")) if first_video else 720
    if target_w < 2 or target_h < 2:
        target_w, target_h = 1280, 720
    target_w = (target_w // 2) * 2
    target_h = (target_h // 2) * 2

    cut_used = [cuts[i] is not None and 0.2 < cuts[i] < raw_durations[i] for i in range(n)]

    brightness = max(-1.0, min(1.0, _to_float(color.get("brightness"), 0.0)))
    contrast = max(0.0, min(3.0, _to_float(color.get("contrast"), 1.0)))
    saturation = max(0.0, min(3.0, _to_float(color.get("saturation"), 1.0)))
    color_filter = str(color.get("filter") or "none").strip().lower()
    text = str(title.get("text") or "").strip()

    can_stream_copy = (
        transition == "none" and speed == 1.0 and aspect == "original" and music is None
        and color_filter in ("", "none") and brightness == 0.0 and contrast == 1.0
        and saturation == 1.0 and not text and not any(cut_used))
    if can_stream_copy:
        if not first_video:
            can_stream_copy = False
        else:
            for probe in probes:
                video = probe.get("video")
                if not video or (video["width"], video["height"], video["codec"]) != (
                        target_w, target_h, first_video["codec"]):
                    can_stream_copy = False
                    break
    if can_stream_copy:
        return run_concat(ffmpeg, ffprobe, clips, output, progress_cb=progress_cb,
                          cancel_event=cancel_event)

    fps = 24
    filter_parts = []

    for i in range(n):
        pre = []
        if cut_used[i]:
            pre.append(f"trim=0:{cuts[i]:0.3f},setpts=PTS-STARTPTS")
        if speed != 1.0:
            pre.append(f"setpts=(1.0/{speed:0.3f})*PTS")
        pre_str = (",".join(pre) + ",") if pre else ""

        clip_video = probes[i].get("video")
        clip_w = _to_int(clip_video.get("width")) if clip_video else target_w
        clip_h = _to_int(clip_video.get("height")) if clip_video else target_h
        if clip_w <= 0 or clip_h <= 0:
            clip_w, clip_h = target_w, target_h
        same_aspect = abs((clip_w / clip_h) - (target_w / target_h)) < 0.02

        if fit_mode == "blur" and not same_aspect:
            blur_w = 160
            blur_h = max(90, int(blur_w * target_h / target_w))
            blur_w = (blur_w // 2) * 2
            blur_h = (blur_h // 2) * 2
            filter_parts.append(
                f"[{i}:v]{pre_str}split[bg_in{i}][fg_in{i}];"
                f"[bg_in{i}]scale={blur_w}:{blur_h}:force_original_aspect_ratio=increase,"
                f"crop={blur_w}:{blur_h},boxblur=2:1,scale={target_w}:{target_h}:flags=fast_bilinear[bg{i}];"
                f"[fg_in{i}]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,setsar=1[fg{i}];"
                f"[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2,fps={fps},format=yuv420p,settb=AVTB[v_prep{i}]")
        elif fit_mode == "crop" and not same_aspect:
            filter_parts.append(
                f"[{i}:v]{pre_str}scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
                f"crop={target_w}:{target_h},setsar=1,fps={fps},format=yuv420p,settb=AVTB[v_prep{i}]")
        elif not same_aspect:
            filter_parts.append(
                f"[{i}:v]{pre_str}scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps},"
                f"format=yuv420p,settb=AVTB[v_prep{i}]")
        else:
            filter_parts.append(
                f"[{i}:v]{pre_str}scale={target_w}:{target_h},setsar=1,fps={fps},"
                f"format=yuv420p,settb=AVTB[v_prep{i}]")

        if probes[i].get("audio"):
            audio_filters = ["aformat=sample_rates=44100:channel_layouts=stereo"]
            if cut_used[i]:
                audio_filters.append(f"atrim=0:{cuts[i]:0.3f}")
            if speed != 1.0:
                audio_filters.extend(_atempo_chain(speed))
            audio_filters.append("asetpts=PTS-STARTPTS")
            filter_parts.append(f"[{i}:a]{','.join(audio_filters)}[a_prep{i}]")
        else:
            filter_parts.append(
                f"anullsrc=r=44100:cl=stereo,atrim=0:{effective_durations[i]:0.3f},"
                f"asetpts=PTS-STARTPTS[a_prep{i}]")

    min_eff = min(effective_durations) if effective_durations else 1.0
    actual_transition = min(transition_dur, max(0.04, min_eff / 2.1))

    if transition != "none" and n >= 2:
        current_video = "v_prep0"
        current_audio = "a_prep0"
        accumulated = effective_durations[0]
        for k in range(1, n):
            offset = max(0.0, accumulated - actual_transition)
            next_video = f"v_x{k}"
            next_audio = f"a_x{k}"
            filter_parts.append(
                f"[{current_video}][v_prep{k}]xfade=transition={transition}:"
                f"duration={actual_transition:0.3f}:offset={offset:0.3f}[{next_video}]")
            filter_parts.append(
                f"[{current_audio}][a_prep{k}]acrossfade=d={actual_transition:0.3f}:c1=tri:c2=tri[{next_audio}]")
            current_video = next_video
            current_audio = next_audio
            accumulated += effective_durations[k] - actual_transition
        total_duration = accumulated
    elif n == 1:
        current_video = "v_prep0"
        current_audio = "a_prep0"
        total_duration = effective_durations[0]
    else:
        joined = "".join(f"[v_prep{i}][a_prep{i}]" for i in range(n))
        filter_parts.append(f"{joined}concat=n={n}:v=1:a=1[v_cat][a_cat]")
        current_video = "v_cat"
        current_audio = "a_cat"
        total_duration = sum(effective_durations)

    effects = []
    if brightness != 0.0 or contrast != 1.0 or saturation != 1.0:
        effects.append(
            f"eq=brightness={brightness:0.3f}:contrast={contrast:0.3f}:saturation={saturation:0.3f}")
    if color_filter == "bw":
        effects.append("hue=s=0")
    elif color_filter == "vignette":
        effects.append("vignette=PI/4")
    elif color_filter == "warm":
        effects.append("colorbalance=rs=0.12:gs=0.04:bs=-0.08")
    elif color_filter == "cold":
        effects.append("colorbalance=rs=-0.08:gs=0.0:bs=0.15")
    elif color_filter == "vintage":
        effects.append("colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131")
    if effects:
        filter_parts.append(f"[{current_video}]{','.join(effects)}[v_col]")
        current_video = "v_col"

    if text:
        # drawtext parses its own escapes on top of the filtergraph ones: a colon separates options even
        # inside quotes (so it is escaped), while a percent is only special for text expansion, which is
        # switched off below - that way "100%" and "%{...}" stay literal text.
        escaped = (text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
                   .replace("\r", " ").replace("\n", " "))
        position = str(title.get("position") or "bottom").strip().lower()
        if position == "top":
            placement = "x=(w-text_w)/2:y=60"
        elif position == "center":
            placement = "x=(w-text_w)/2:y=(h-text_h)/2"
        else:
            placement = "x=(w-text_w)/2:y=h-text_h-60"
        font_sizes = {"small": 30, "medium": 44, "large": 64}
        font_size = font_sizes.get(str(title.get("size") or "medium").strip().lower(), 44)
        font_color = str(title.get("color") or "white").strip().lower()
        if not re.fullmatch(r"[#0-9a-zA-Z@.]+", font_color):
            font_color = "white"
        font_arg = ""
        if sys.platform == "win32" and Path("C:/Windows/Fonts/arial.ttf").exists():
            font_arg = "fontfile='C\\:/Windows/Fonts/arial.ttf':"
        filter_parts.append(
            f"[{current_video}]drawtext={font_arg}expansion=none:text='{escaped}':fontsize={font_size}:"
            f"fontcolor={font_color}:box=1:boxcolor=black@0.6:boxborderw=10:{placement}[v_txt]")
        current_video = "v_txt"

    map_audio = current_audio
    if music:
        music_volume = max(0.0, min(4.0, _to_float(audio_opts.get("music_vol"), 0.8)))
        video_volume = max(0.0, min(4.0, _to_float(audio_opts.get("video_vol"), 1.0)))
        audio_mode = str(audio_opts.get("mode") or "mix")
        fade = audio_opts.get("fade", True)

        music_filters = [f"volume={music_volume:0.3f}"]
        if fade and total_duration > 3.0:
            music_filters.append(
                f"afade=t=in:ss=0:d=1.5,afade=t=out:st={total_duration - 1.5:0.3f}:d=1.5")
        filter_parts.append(f"[{n}:a]{','.join(music_filters)}[mus]")

        if audio_mode == "mix":
            filter_parts.append(
                f"[{current_audio}]volume={video_volume:0.3f}[vid_a];"
                f"[vid_a][mus]amix=inputs=2:duration=first:dropout_transition=2[aout]")
            map_audio = "aout"
        elif audio_mode in ("replace", "music_only"):
            map_audio = "mus"

    filter_complex = ";".join(filter_parts)
    loop_music = bool(audio_opts.get("loop", True))

    # A long montage builds a filtergraph bigger than a Windows command line may carry (~32k characters),
    # so a big graph travels through a temporary UTF-8 file that ffmpeg reads itself.
    script_path = None
    if len(filter_complex) > 6000:
        fd, name = tempfile.mkstemp(prefix="h3s_fc_", suffix=".txt")
        os.close(fd)
        script_path = Path(name)
        script_path.write_text(filter_complex, encoding="utf-8")

    def build_cmd(use_nvenc):
        args = [str(ffmpeg), "-y"]
        for clip in clips:
            if use_nvenc:
                args.extend(["-hwaccel", "auto"])
            args.extend(["-i", str(clip.resolve())])
        if music:
            if loop_music:
                args.extend(["-stream_loop", "-1"])
            args.extend(["-i", str(music.resolve())])
        if script_path:
            args.extend(["-filter_complex_script", str(script_path)])
        else:
            args.extend(["-filter_complex", filter_complex])
        args.extend([
            "-map", f"[{current_video}]",
            "-map", f"[{map_audio}]",
            "-c:a", "aac", "-b:a", "192k",
        ])
        args.extend(_video_encode_args(use_nvenc))
        args.extend(["-movflags", "+faststart", "-progress", "pipe:1"])
        if durations_known:
            args.extend(["-t", f"{total_duration:.2f}"])
        elif music:
            args.append("-shortest")
        args.append(str(output))
        return args

    if _is_cancelled(cancel_event):
        raise RuntimeError("Отменено пользователем")
    try:
        result = _run_encode(build_cmd, ffmpeg, total_duration, output, progress_cb, cancel_event,
                             _no_window(), "Студия FFmpeg")
    finally:
        if script_path:
            _remove_file(script_path)
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "Ошибка монтажа в Студии FFmpeg")

    return {
        "ok": True,
        "fast_copy": False,
        "duration": round(total_duration, 1),
        "res": f"{target_w}×{target_h}",
    }
