"""Agent access to the studio: discovery, capabilities, named inputs and job status.

Contract v1, step P0 (the full description is in docs/agent-api.md).

The program is a desktop app: every launch takes a free port and a fresh access key,
so another program cannot be configured once and forever. It finds the app through
`agent.json` (see `agent_file()` below), takes the port and the key from it and calls
`/api/agent/v1/...` on 127.0.0.1:

    GET  /api/agent/v1/ping              — is the app alive and which instance
    GET  /api/agent/v1/capabilities      — modes, models, limits, where files may be read
    POST /api/agent/v1/assets            — a file on disk becomes a named input
    GET  /api/agent/v1/jobs/{id}         — state, progress, results
    GET  /api/agent/v1/library           — finished work

Nothing here renders anything of its own: it reads the same jobs and the same library
the window shows, so an agent's work is ordinary work the user can watch and stop.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import random
import secrets
import shutil
import struct
import subprocess
import sys
import time
import urllib.parse
import uuid
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import web

import workflows as wf
from backends.base import BackendUnavailable, SubmitRejected
from domain.errors import DomainError, InvalidSpec, JobConflict
from domain.jobs import InputRef, JobSpec, JobState, ResultPolicy, public_state
from http_errors import agent_error
from storage.atomic import atomic_write_json
from version import APP_VERSION

PREFIX = "/api/agent/v1/"
API_VERSION = 1
APP_NAME = "minimax-h3-workspace"
MAX_ASSET_BYTES = 2 * 1024 ** 3  # a video of a long film may be big; a picture is far below this

KINDS = {
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image", ".bmp": "image", ".gif": "image",
    ".mp4": "video", ".webm": "video", ".mov": "video", ".mkv": "video",
    ".wav": "audio", ".mp3": "audio", ".flac": "audio", ".m4a": "audio", ".ogg": "audio",
    ".latent": "latent",
}

# how far a job is by its stage; the same numbers the window's queue uses (QBASE in ui/index.html)
QBASE = {"queued": .02, "load": .05, "encode": .12, "sample": .15, "upscale": .67, "sample2": .7,
         "decode": .92, "save": .98, "cloud_queued": .05, "cloud_run": .4}


def agent_file():
    """Where the app publishes its port and one-time key. `H3STUDIO_AGENT_FILE` overrides it (tests, portable setups)."""
    override = os.environ.get("H3STUDIO_AGENT_FILE")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "MinimaxH3Workspace" / "agent.json"


def _restrict_to_user(path):
    """Only the current Windows user may read the key. Best effort: a failure must never stop the app."""
    if sys.platform != "win32":
        try:
            Path(path).chmod(0o600)
        except OSError:
            pass
        return
    user = os.environ.get("USERNAME")
    if not user:
        return
    try:
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        pass


def image_size(path):
    """Width and height of a picture without any library: the PNG header, else Pillow if it happens to be there."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
        return struct.unpack(">II", head[16:24])
    try:
        from PIL import Image  # not required by the app, only speeds this up when installed
        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


def percent(stage, step, total, scene=None, scenes=None):
    """The same rough share the window shows: the exact number is known only to ComfyUI."""
    s = str(stage or "queued")
    if s.startswith("sample") and total:
        f = (step or 0) / total
        p = .7 + .2 * f if s == "sample2" else .15 + .75 * f
        if scene and scenes:
            p = .15 + .75 * ((scene - 1 + f) / scenes)
        return round(p, 3)
    return QBASE.get(s, .3)


class AgentError(Exception):
    """A refusal with a code an agent can branch on. The HTTP handlers turn it into the usual error body."""

    def __init__(self, status, code, message, hint=None):
        super().__init__(message)
        self.status, self.code, self.message, self.hint = status, code, message, hint


class Assets:
    """Named inputs. The name is the content hash, so the same picture imported twice is one asset."""

    def __init__(self, root):
        self.root = Path(root)

    def _meta_path(self, asset_id):
        return self.root / asset_id[:2] / f"{asset_id}.json"

    def file(self, meta):
        return self.root / meta["asset"][:2] / meta["filename"]

    def get(self, asset_id):
        if not asset_id or len(asset_id) < 8 or any(c not in "0123456789abcdef" for c in asset_id):
            return None
        try:
            meta = json.loads(self._meta_path(asset_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return meta if self.file(meta).exists() else None

    def list(self, limit=50):
        out = []
        for p in sorted(self.root.glob("*/*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
            try:
                meta = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if self.file(meta).exists():
                out.append(meta)
        return out

    def add(self, src, source="import"):
        """Copy a file into the store. Returns (meta, already_had_it)."""
        sha, size = hashlib.sha256(), 0
        with open(src, "rb") as f:
            while chunk := f.read(1 << 20):
                sha.update(chunk)
                size += len(chunk)
        digest = sha.hexdigest()
        asset_id = digest[:16]
        have = self.get(asset_id)
        if have:
            return have, True
        ext = Path(src).suffix.lower() or ".bin"
        meta = {"asset": asset_id, "kind": KINDS.get(ext, "file"), "ext": ext, "filename": asset_id + ext,
                "bytes": size, "sha256": digest, "source": source, "created": datetime.now().isoformat(timespec="seconds"),
                "width": None, "height": None}
        dest = self.file(meta)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        shutil.copyfile(src, tmp)
        tmp.replace(dest)
        if meta["kind"] == "image":
            size_ = image_size(dest)
            if size_:
                meta["width"], meta["height"] = int(size_[0]), int(size_[1])
        self._meta_path(asset_id).parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._meta_path(asset_id), meta)
        return meta, False


class AgentAPI:
    """The /api/agent/v1 endpoints. One instance per app run: the key is new every time."""

    def __init__(self, app, data_dir, max_seconds):
        self.app = app
        self.data_dir = Path(data_dir)
        self.max_seconds = max_seconds
        self.assets = Assets(self.data_dir / "assets")
        self.token = secrets.token_urlsafe(32)
        self.instance = uuid.uuid4().hex[:12]
        self.published = None

    # ---------- enable / publish ----------

    def enabled(self):
        return self.app.config.get("allow_agents") is not False

    def publish(self):
        """Write agent.json so another program can find this window: the port changes at every launch."""
        port = getattr(self.app, "port", None)
        if not port:
            return None
        path = agent_file()
        data = {"app": APP_NAME, "api": API_VERSION, "version": APP_VERSION, "port": port,
                "token": self.token, "instance": self.instance, "pid": os.getpid(),
                "started": datetime.now().isoformat(timespec="seconds"), "data_dir": str(self.data_dir),
                "url": f"http://127.0.0.1:{port}{PREFIX}"}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(path, data, indent=1)
            _restrict_to_user(path)
        except OSError:
            return None
        self.published = path
        return path

    def unpublish(self):
        path = self.published or agent_file()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("instance") == self.instance:  # do not delete the file of a newer window
            try:
                path.unlink()
            except OSError:
                pass

    # ---------- auth ----------

    def _token_of(self, request):
        given = request.headers.get("X-Agent-Token")
        if not given:
            auth = request.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                given = auth[7:].strip()
        return given or ""

    @web.middleware  # marks it as a request wrapper; without the mark aiohttp reads it as a factory
    async def middleware(self, request, handler):
        path = request.path
        if not path.startswith(PREFIX):
            return await handler(request)
        if not self.enabled():
            return self._err(403, "AGENTS_DISABLED",
                             "Управление агентами выключено в настройках программы",
                             "Включите «Настройки → Агенты → Разрешить управление агентам».")
        if path == PREFIX + "ping":  # discovery may ask who is on this port before it has a key
            return await handler(request)
        if not hmac.compare_digest(self._token_of(request), self.token):
            return self._err(401, "BAD_TOKEN", "Неверный ключ доступа",
                             "Ключ лежит в agent.json: поле token. Он новый после каждого запуска программы.")
        return await handler(request)

    # ---------- helpers ----------

    def _err(self, status, code, message, hint=None):
        body = {"error": {"code": code, "message": message}}
        if hint:
            body["error"]["hint"] = hint
        return web.json_response(body, status=status)

    def _url(self, path):
        port = getattr(self.app, "port", None)
        return f"http://127.0.0.1:{port}{path}" if port else path

    def _allowed_roots(self):
        """Where an agent may take files from. A wrong path here is a mistake, not a feature: everything else is refused."""
        roots = [Path.home(), self.data_dir]
        try:
            roots.append(Path(self.app.results_dir()))
        except Exception:
            pass
        for extra in self.app.config.get("agent_asset_roots") or []:
            if isinstance(extra, str) and extra.strip():
                roots.append(Path(extra.strip()))
        for extra in os.environ.get("H3STUDIO_AGENT_ROOTS", "").split(os.pathsep):
            if extra.strip():
                roots.append(Path(extra.strip()))
        seen, out = set(), []
        for r in roots:
            try:
                r = r.expanduser().resolve(strict=False)
            except OSError:
                continue
            if r not in seen and r.is_dir():
                seen.add(r)
                out.append(r)
        return out

    def _asset_json(self, meta):
        return {"asset": meta["asset"], "kind": meta["kind"], "bytes": meta["bytes"],
                "width": meta["width"], "height": meta["height"], "created": meta["created"],
                "path": str(self.assets.file(meta)),
                "url": self._url(f"{PREFIX}assets/{meta['asset']}/content")}

    # ---------- discovery ----------

    async def h_ping(self, _request):
        return web.json_response({"app": APP_NAME, "api": API_VERSION, "version": APP_VERSION,
                                  "instance": self.instance})

    async def h_capabilities(self, _request):
        app = self.app
        gpu = None
        try:
            gpu = await asyncio.to_thread(app.monitor.gpu)
        except Exception:
            pass
        if app.comfy_online and not app.model_names:
            try:
                await app.resolve_models()
            except Exception:
                pass
        missing = {m["role"] for m in app.missing}
        return web.json_response({
            "app": APP_NAME, "api": API_VERSION, "version": APP_VERSION, "instance": self.instance,
            "agents": {"enabled": self.enabled(), "asset_roots": [str(r) for r in self._allowed_roots()],
                       "max_asset_bytes": MAX_ASSET_BYTES},
            "comfy": {"online": bool(app.comfy_online), "url": app.comfy},
            "cloud": {"enabled": bool(app.config.get("rh_enabled"))},
            "gpu": gpu,
            "modes": app.modes(),
            "jobs": {"active": len(app.jobs), "can_start": [] if app.rh() else [m for m in ("video",) if m in app.modes()]},
            "limits": {"max_seconds": self.max_seconds, "aspects": list(wf.ASPECTS), "qualities": list(wf.QUALITY),
                       "image_sizes": list(wf.KREA_SIZES)},
            "models": {role: {"file": name, "have": role not in missing} for role, name in app.model_names.items()},
        })

    async def h_enabled_get(self, _request):
        return web.json_response({"enabled": self.enabled(), "port": getattr(self.app, "port", None),
                                  "file": str(agent_file())})

    async def h_enabled_set(self, request):
        body = await request.json()
        self.app.config.update(allow_agents=bool(body.get("enabled", True)))
        return web.json_response({"enabled": self.enabled()})

    # ---------- assets ----------

    async def h_assets(self, request):
        limit = request.query.get("limit", "50")
        limit = min(200, int(limit)) if limit.isdigit() else 50
        return web.json_response({"items": [self._asset_json(m) for m in self.assets.list(limit)]})

    async def h_asset(self, request):
        meta = self.assets.get(request.match_info["aid"])
        if not meta:
            return self._err(404, "ASSET_NOT_FOUND", "Такого входного файла нет")
        return web.json_response(self._asset_json(meta))

    async def h_asset_content(self, request):
        meta = self.assets.get(request.match_info["aid"])
        if not meta:
            return self._err(404, "ASSET_NOT_FOUND", "Такого входного файла нет")
        return web.FileResponse(self.assets.file(meta))

    async def h_asset_add(self, request):
        ctype = (request.headers.get("Content-Type") or "").lower()
        if ctype.startswith("multipart/"):
            return await self._import_upload(request)
        if not ctype.startswith("application/json"):
            return self._err(415, "BAD_REQUEST", "Пришлите JSON {\"path\": \"...\"} или файл multipart-ом")
        try:
            body = await request.json()
        except ValueError:
            return self._err(400, "BAD_REQUEST", "Не удалось прочитать JSON")
        try:
            meta, had = await self._import_path_asset(str(body.get("path") or "").strip())
        except AgentError as e:
            return self._err(e.status, e.code, e.message, e.hint)
        return self._asset_created(meta, had)

    def _asset_created(self, meta, had):
        response = web.json_response({**self._asset_json(meta), "deduplicated": had}, status=201)
        response.headers["Location"] = self._url(f"{PREFIX}assets/{meta['asset']}")
        return response

    async def _import_path_asset(self, raw):
        """A file on disk becomes a named input (or an already known one). Raises AgentError."""
        if not raw:
            raise AgentError(400, "BAD_REQUEST", "Укажите path — полный путь к файлу на этом компьютере")
        src = Path(raw).expanduser()
        if not src.is_absolute():
            raise AgentError(400, "BAD_REQUEST", "Нужен полный путь (например E:\\frames\\s01.png)")
        try:
            src = src.resolve(strict=True)
        except OSError:
            raise AgentError(404, "ASSET_NOT_FOUND", f"Файл не найден: {raw}")
        if not src.is_file():
            raise AgentError(400, "BAD_REQUEST", f"Это не файл: {raw}")
        roots = self._allowed_roots()
        if not any(src.is_relative_to(r) for r in roots):
            raise AgentError(403, "ASSET_FORBIDDEN", "Из этой папки приложению файлы читать нельзя",
                             "Разрешено: " + "; ".join(str(r) for r in roots)
                             + ". Свои папки добавьте в config.json → agent_asset_roots.")
        ext = src.suffix.lower()
        if ext not in KINDS:
            raise AgentError(415, "ASSET_BAD_TYPE", f"Такой файл не подойдёт: {ext or 'без расширения'}",
                             "Подходят картинки (png, jpg, webp), видео (mp4, webm, mov) и звук (wav, mp3, flac).")
        try:
            too_big = src.stat().st_size > MAX_ASSET_BYTES
        except OSError:
            too_big = False
        if too_big:
            raise AgentError(413, "ASSET_TOO_BIG", "Файл больше 2 ГБ")
        return self.assets.add(src)

    async def _import_upload(self, request):
        tmp_dir = self.data_dir / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        reader = await request.multipart()
        async for field in reader:
            if not field.filename:
                continue
            name = Path(field.filename).name
            tmp = tmp_dir / f"up-{uuid.uuid4().hex[:10]}{Path(name).suffix.lower()}"
            size = 0
            with open(tmp, "wb") as out:
                while chunk := await field.read_chunk(1 << 20):
                    size += len(chunk)
                    if size > MAX_ASSET_BYTES:
                        out.close()
                        tmp.unlink(missing_ok=True)
                        return self._err(413, "ASSET_TOO_BIG", "Файл больше 2 ГБ")
                    out.write(chunk)
            try:
                if Path(name).suffix.lower() not in KINDS:
                    return self._err(415, "ASSET_BAD_TYPE", f"Такой файл не подойдёт: {Path(name).suffix}")
                meta, had = self.assets.add(tmp, source="upload")
            finally:
                tmp.unlink(missing_ok=True)
            return self._asset_created(meta, had)
        return self._err(400, "BAD_REQUEST", "В запросе не было файла")

    # ---------- jobs: start one ----------

    async def h_job_add(self, request):
        try:
            body = await request.json()
        except ValueError:
            return self._err(400, "BAD_REQUEST", "Не удалось прочитать JSON")
        if not isinstance(body, dict):
            return self._err(400, "BAD_REQUEST", "Ожидался объект JSON")
        try:
            return await self._submit(body)
        except AgentError as e:
            return self._err(e.status, e.code, e.message, e.hint)
        except Exception as e:  # a bug here must not look like a broken request to the caller
            self.app.note_error(f"Задание от агента не удалось создать: {type(e).__name__}: {e}")
            return self._err(500, "INTERNAL", f"{type(e).__name__}: {e}")

    async def _submit(self, body):
        """Start an image-to-video (or text-to-video) job through the same graph builder and queue the window uses."""
        app = self.app
        params = body.get("params") if isinstance(body.get("params"), dict) else {}

        def get(key, default=None):
            return body.get(key, params.get(key, default))

        key = str(body.get("idempotency_key") or "").strip()[:120]
        # an agent may name the project explicitly; without it the job goes where the window works
        project = str(body.get("project") or "").strip()
        if project and not app.projects_repo.exists(project):
            raise AgentError(400, "PROJECT_UNKNOWN", f"Нет проекта «{project}»",
                             "Список проектов — GET " + PREFIX + "projects. Без параметра задание идёт "
                             "в активный проект окна.")

        mode = str(body.get("mode") or "video").strip().lower()
        if mode != "video":
            raise AgentError(400, "MODE_UNSUPPORTED", f"Пока агент может запускать только mode=\"video\", а не \"{mode}\"",
                             "Картинки — в окне; длинное видео и монтаж появятся на следующем шаге.")
        if mode not in app.modes():
            raise AgentError(409, "MODE_UNAVAILABLE", "Раздел «Видео» не установлен в этой программе",
                             "Посмотрите modes в GET /capabilities и добавьте раздел в настройках.")
        if app.rh():  # pictures for the cloud are uploaded another way; the window handles that path
            raise AgentError(409, "CLOUD_UNSUPPORTED", "В режиме облака RunningHub агенты пока не запускают задания",
                             "Выключите облако в настройках или запустите ролик в окне.")

        prompt = str(get("prompt", "") or "").strip()
        if not prompt:
            raise AgentError(400, "INVALID_PARAM", "Нужен prompt — что происходит в кадре",
                             "Промпт H3 описывает сцену, камеру и звук; формат — в skills/hailuo-h3.")
        aspect = str(get("aspect", "16:9"))
        if aspect not in wf.ASPECTS:
            raise AgentError(400, "INVALID_PARAM", f"Неизвестный aspect: {aspect}",
                             "Разрешены: " + ", ".join(wf.ASPECTS))
        quality = str(get("quality", "720p"))
        if quality not in wf.QUALITY:
            raise AgentError(400, "INVALID_PARAM", f"Неизвестное quality: {quality}",
                             "Разрешены: " + ", ".join(wf.QUALITY))
        seed = get("seed")
        try:
            seed = int(seed) if seed not in (None, "") else None
        except (TypeError, ValueError):
            raise AgentError(400, "INVALID_PARAM", "seed должно быть целым числом")
        loop = bool(get("loop", False))

        inputs = body.get("inputs") if isinstance(body.get("inputs"), dict) else {}
        first = await self._ref_to_asset(inputs.get("first_frame", body.get("first_frame")), "first_frame")
        last = await self._ref_to_asset(inputs.get("last_frame", body.get("last_frame")), "last_frame")
        if last and not first:
            raise AgentError(400, "INVALID_PARAM", "last_frame задаётся только вместе с first_frame")
        if loop and not first:
            raise AgentError(400, "INVALID_PARAM", "loop работает только с first_frame")
        if loop and last:
            raise AgentError(400, "INVALID_PARAM", "loop и last_frame вместе не бывает: для зацикливания последний кадр — это первый")

        try:
            seconds = float(get("seconds", 5))
            if seconds != seconds:
                raise ValueError("NaN")
            seconds = round(min(self.max_seconds, max(3.0, seconds)), 2)
        except (TypeError, ValueError):
            raise AgentError(400, "INVALID_PARAM", "seconds должно быть числом")
        intent = {"mode": mode, "prompt": prompt, "aspect": aspect, "quality": quality,
                  "seconds": seconds, "seed": seed, "loop": loop,
                  "first": first["asset"] if first else None, "last": last["asset"] if last else None}
        fingerprint = "request-v1:" + hashlib.sha256(json.dumps(
            intent, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        if key:
            try:
                prior = await app.service.lookup_request(key, fingerprint)
            except DomainError as e:
                raise AgentError(*agent_error(e))
            if prior is not None:
                return self._submission_response(prior, True, key)

        width, height = wf.resolution(aspect, quality, {})
        first_name = last_name = None
        try:
            await app.need_models("video")  # before the files travel: a missing model should fail fast
            if first:
                first_name = await app.put_input(self.assets.file(first))
                if loop:
                    last_name = first_name
                elif last:
                    last_name = await app.put_input(self.assets.file(last))
            refs = []
            if first_name:
                refs.append(InputRef(role="Первый кадр" + (" · зациклено" if loop else ""), kind="image",
                                     comfy=first_name, asset=first["asset"] if first else None,
                                     key="first_frame"))
            if last_name and last_name != first_name:
                refs.append(InputRef(role="Последний кадр", kind="image", comfy=last_name,
                                     asset=last["asset"] if last else None, key="last_frame"))
            # the same description the window builds: the duration is clamped and checked in one place
            spec = JobSpec.video(prompt=prompt, aspect=aspect, quality=quality, seconds=get("seconds", 5),
                                 seed=seed, width=width, height=height, models=dict(app.model_names),
                                 loras=app.custom_loras("video"), inputs=tuple(refs),
                                 result_policy=ResultPolicy.LIBRARY, source="agent",
                                 sub=self._job_subtitle(first_name, last_name, loop),
                                 max_seconds=self.max_seconds)
            inputs = []   # the files the job is made from: the details window in the queue shows them
            if first_name:
                inputs.append(self._job_input("Первый кадр" + (" · зациклено" if loop else ""), "image", first_name))
            if last_name and last_name != first_name:
                inputs.append(self._job_input("Последний кадр", "image", last_name))
            meta = {"mode": "video", "sub": spec.sub,
                    "prompt": spec.prompt, "aspect": spec.aspect, "q": spec.quality, "len": spec.seconds,
                    "res": f"{spec.width}×{spec.height}", "seed": spec.seed, "inputs": inputs,
                    "model": spec.model, "acceleration": spec.acceleration,
                    "project": project or app.active_project(),
                    "origin": {"kind": "agent", "client": str(body.get("client") or "")[:40] or None,
                               "idempotency_key": key or None}}
            record, deduplicated = await app.start_video_job(
                spec, meta=meta, key=key or None, fingerprint=fingerprint)
        except DomainError as e:
            # one table for both faces of the program: the window's statuses and the agent's codes
            status, code, message, hint = agent_error(e)
            raise AgentError(status, code, message, hint)
        except SubmitRejected as e:
            raise AgentError(400, "GENERATION_REFUSED", str(e))
        except BackendUnavailable as e:
            raise AgentError(503, "COMFY_OFFLINE", str(e),
                             "Задание записано как неопределённое: проверьте очередь ComfyUI, прежде чем повторять — "
                             "оно могло уйти. Повтор с тем же idempotency_key не отправит его дважды.")
        except web.HTTPException as e:
            raise AgentError(e.status if 400 <= e.status < 500 else 502, "GENERATION_REFUSED", e.text or e.reason)
        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as e:
            raise AgentError(503, "COMFY_OFFLINE", f"ComfyUI не отвечает: {type(e).__name__}",
                             "Поднимите ComfyUI или дождитесь, пока приложение его запустит, и повторите с тем же idempotency_key.")
        job_id = record.prompt_id
        if not deduplicated:
            # the window's queue row shows a short title, not the whole H3 prompt with its sections
            await app.broadcast({"type": "external_job", "prompt_id": job_id, "mode": "video",
                                 "prompt": self._short_title(prompt)})
        return self._submission_response(record, deduplicated, key)

    def _submission_response(self, record, deduplicated, key):
        spec = record.spec
        refs = {ref.key: ref for ref in spec.inputs}

        def input_view(key):
            ref = refs.get(key)
            return {"asset": ref.asset, "comfy": ref.comfy} if ref else None

        return web.json_response({
            "job": record.prompt_id, "state": public_state(record.state), "deduplicated": deduplicated,
            "seed": spec.seed, "seconds": spec.seconds,
            "aspect": spec.aspect, "quality": spec.quality, "res": f"{spec.width}×{spec.height}",
            "inputs": {"first_frame": input_view("first_frame"), "last_frame": input_view("last_frame")},
            "origin": {"kind": "agent", "idempotency_key": key or None},
            "uncertain": record.state is JobState.UNCERTAIN,
        }, status=200 if deduplicated else 202)   # a duplicate creates nothing, and says so

    @staticmethod
    def _job_subtitle(first, last, loop):
        sub = "Зацикленное видео" if loop else "Кадр → кадр" if last else "Изображение → видео" if first else "Текст → видео"
        return sub + " · агент"

    @staticmethod
    def _job_input(role, kind, comfy):
        """One file of a job as the window's details need it: ComfyUI has its own copy of what the agent brought."""
        path = str(comfy).replace("\\", "/")
        return {"role": role, "kind": kind, "url": "/api/input?name=" + urllib.parse.quote(path), "file": path.rsplit("/", 1)[-1]}

    @staticmethod
    def _short_title(prompt):
        """A queue-row title: the `summary:` line of an H3 prompt, or the beginning of a plain one."""
        lines = [ln.strip() for ln in str(prompt).splitlines()]
        for i, line in enumerate(lines):
            if line.lower().startswith("summary:"):
                rest = line.split(":", 1)[1].strip()
                if rest:
                    return rest[:140]
                nxt = next((x for x in lines[i + 1:] if x), "")
                return (nxt or "Видео")[:140]
        return " ".join(str(prompt).split())[:140]

    async def _ref_to_asset(self, ref, field):
        """{"asset": id} / {"path": "…"} / "id" / "C:\\…" -> asset meta. Raises AgentError."""
        if ref is None or ref == "":
            return None
        asset_id = path = None
        if isinstance(ref, dict):
            asset_id = str(ref.get("asset") or "").strip() or None
            path = str(ref.get("path") or "").strip() or None
        elif isinstance(ref, str):
            value = ref.strip()
            asset_id = value if len(value) == 16 and all(c in "0123456789abcdef" for c in value) else None
            path = None if asset_id else value
        else:
            raise AgentError(400, "INVALID_PARAM", f"{field}: ожидался asset или path")
        if asset_id:
            meta = self.assets.get(asset_id)
            if not meta:
                raise AgentError(404, "ASSET_NOT_FOUND", f"{field}: ассета {asset_id} нет")
        elif path:
            meta, _ = await self._import_path_asset(path)
        else:
            raise AgentError(400, "INVALID_PARAM", f"{field}: нужен asset или path")
        if meta["kind"] != "image":
            raise AgentError(415, "ASSET_BAD_TYPE", f"{field} должен быть картинкой, а это {meta['kind']}",
                             "Кадр — png, jpg или webp.")
        return meta

    async def h_job_cancel(self, request):
        try:
            return await self._cancel(request.match_info["jid"])
        except AgentError as e:
            return self._err(e.status, e.code, e.message, e.hint)

    async def _cancel(self, job_id):
        # one operation, its outcome as data: the service decides what happened to the job, this
        # layer only words it for the agent (no HTTP response is parsed anywhere in between)
        result = await self.app.cancel_job(job_id)
        state = result.get("state")
        if state == "unknown":
            view = self.job_view(job_id)
            if view:
                raise AgentError(409, "ALREADY_FINISHED", f"Задание уже завершено: {view['state']}",
                                 "Готовое лежит в GET /library; отменить можно только ждущее или идущее задание.")
            raise AgentError(404, "JOB_NOT_FOUND", "Такого задания приложение не помнит")
        if state == "finished":
            raise AgentError(409, "ALREADY_FINISHED", result.get("message") or "Задание уже завершено",
                             "Отменить можно только ждущее или идущее задание: результат сохраняется.")
        if state == "unconfirmed":
            raise AgentError(503, "COMFY_OFFLINE", result.get("message") or "ComfyUI не отвечает",
                             "Отмена записана, но движок её не подтвердил: не повторяйте, пока он не ответит.")
        if state == "refused":
            raise AgentError(409, "CANCEL_REFUSED", result.get("message") or "Движок отказался останавливать задание")
        return web.json_response({"job": job_id, "state": state})

    # ---------- jobs ----------

    def job_view(self, job_id):
        """State of a job by its id (the ComfyUI prompt id the window shows in the queue). None when unknown.

        The public states stay the ones the agent has always seen; `phase` adds the internal one
        (finalizing, submitting, uncertain) without turning a live job into a 404 or a premature done.
        """
        app = self.app
        job = app.jobs.get(job_id)
        if job:
            meta = job.get("meta") or {}
            running = job.get("state") == "running"
            scenes = (job.get("scenes") or {}).get("n")
            scene = job.get("scene_now")
            stage = job.get("stage") or ("queued" if not running else "load")
            position = None
            if not running:
                waiting = [k for k, j in app.jobs.items() if j.get("state") != "running"]
                position = waiting.index(job_id) if job_id in waiting else None
            t0 = job.get("t0")
            record = job.get("record")
            return {"job": job_id, "state": "running" if running else "queued",
                    "phase": record.state.value if record is not None else None,
                    "finalizing": bool(job.get("finalizing")),
                    "mode": meta.get("mode"), "prompt": meta.get("prompt"), "seed": meta.get("seed"),
                    "origin": meta.get("origin"), "project": meta.get("project"),
                    "progress": {"stage": stage, "step": job.get("step"), "total": job.get("step_max"),
                                 "scene": scene, "scenes": scenes,
                                 "percent": percent(stage, job.get("step"), job.get("step_max"), scene, scenes)},
                    "queue_position": position,
                    "elapsed": round(time.time() - t0, 1) if t0 else None,
                    "results": []}
        # Includes a reply lost during this run, before a job could enter the UI cache.
        durable = getattr(app, "jobs_repo", None)
        pending_records = list(getattr(app, "pending_resume", ()))
        stored = durable.by_prompt(job_id) if durable is not None else None
        if stored is not None and not stored.state.terminal:
            pending_records.append(stored)
        for pending in pending_records:
            if (pending.prompt_id or pending.backend_job_id) != job_id:
                continue
            meta = (pending.legacy or {}).get("meta") or {}
            return {"job": job_id, "state": "queued", "phase": pending.state.value, "recovering": True,
                    "mode": meta.get("mode"), "prompt": meta.get("prompt"), "seed": meta.get("seed"),
                    "origin": meta.get("origin"), "project": meta.get("project"),
                    "progress": {"stage": "recovering", "step": None, "total": None, "scene": None,
                                 "scenes": None, "percent": 0.0},
                    "queue_position": None, "elapsed": None, "results": []}
        items = [it for it in app.library.items if it.get("prompt_id") == job_id]
        # without a Library item the record is the answer: it keeps how the job ended (a failed or
        # stopped one has no media), so the agent sees the same fact as the window
        recent = app.job_outcome(job_id) if hasattr(app, "job_outcome") else None
        if not items and not recent:
            return None
        state = "done" if items else {"done": "done", "error": "failed", "interrupted": "canceled"}.get(
            (recent or {}).get("state"), "failed")
        view = {"job": job_id, "state": state, "mode": None, "prompt": None, "seed": None, "origin": None,
                "progress": {"stage": state, "step": None, "total": None, "scene": None, "scenes": None,
                             "percent": 1.0 if state == "done" else 0.0},
                "queue_position": None, "elapsed": None, "finished_at": (recent or {}).get("at"),
                "results": [self._result(it) for it in items]}
        if items:
            first = items[0]
            view.update(mode=first.get("mode"), prompt=first.get("prompt"), seed=first.get("seed"),
                        origin=first.get("origin"), project=first.get("project"))
        elif recent:
            view["project"] = (recent.get("meta") or {}).get("project")
        if recent and recent.get("message"):
            view["error"] = {"code": "JOB_FAILED", "message": recent["message"]}
        return view

    def _result(self, item):
        path = self.app.file_path(item)
        return {"library_id": item["id"], "kind": item.get("kind", "video"), "mode": item.get("mode"),
                "prompt": item.get("prompt"), "res": item.get("res"), "seconds": item.get("len"),
                "date": item.get("date"), "project": item.get("project"),
                "path": str(path) if path else None,
                "url": self._url(f"/api/file/{item['id']}")}

    async def h_jobs(self, _request):
        active = [self.job_view(k) for k in list(self.app.jobs)]
        # the outcomes come from the same records the window sees, so they survive a restart
        recent = list(getattr(self.app, "recent_outcomes", lambda n=20: [])(20))
        return web.json_response({"active": [v for v in active if v],
                                  "finished": [{"job": r["prompt_id"], "state": r["state"], "at": r["at"]}
                                               for r in reversed(recent)]})

    async def h_job(self, request):
        view = self.job_view(request.match_info["jid"])
        if not view:
            return self._err(404, "JOB_NOT_FOUND", "Такого задания приложение не помнит",
                             "Задание старше недели или было удалено: готовое ищите в Библиотеке, "
                             "GET " + PREFIX + "library")
        return web.json_response(view)

    async def h_job_wait(self, request):
        raw = request.query.get("timeout", "60")
        try:
            timeout = min(120.0, max(1.0, float(raw)))
        except ValueError:
            timeout = 60.0
        end = time.monotonic() + timeout
        while True:
            view = self.job_view(request.match_info["jid"])
            if not view:
                return self._err(404, "JOB_NOT_FOUND", "Такого задания приложение не помнит")
            if view["state"] not in ("queued", "running") or time.monotonic() >= end:
                return web.json_response(view)
            await asyncio.sleep(0.7)

    # ---------- library ----------

    async def h_projects(self, _request):
        """The workspaces an agent may name in `POST jobs`: the same list the window shows."""
        active = self.app.active_project()
        return web.json_response({"active": active, "projects": [
            {"id": p["id"], "name": p["name"], "active": p["id"] == active}
            for p in self.app.projects()]})

    async def h_library(self, request):
        raw = request.query.get("limit", "20")
        limit = min(100, int(raw)) if raw.isdigit() else 20
        mode = request.query.get("mode")
        project = (request.query.get("project") or "").strip()
        items = [it for it in self.app.library.items
                 if (not mode or it.get("mode") == mode)
                 and (not project or it.get("project") == project)][:limit]
        return web.json_response({"items": [self._result(it) for it in items],
                                  "total": len(self.app.library.items)})

    # ---------- routes ----------

    def routes(self):
        return [
            web.get(PREFIX + "ping", self.h_ping),
            web.get(PREFIX + "capabilities", self.h_capabilities),
            web.get(PREFIX + "enabled", self.h_enabled_get),
            web.post(PREFIX + "enabled", self.h_enabled_set),
            web.get(PREFIX + "assets", self.h_assets),
            web.post(PREFIX + "assets", self.h_asset_add),
            web.get(PREFIX + "assets/{aid}", self.h_asset),
            web.get(PREFIX + "assets/{aid}/content", self.h_asset_content),
            web.get(PREFIX + "jobs", self.h_jobs),
            web.post(PREFIX + "jobs", self.h_job_add),
            web.get(PREFIX + "jobs/{jid}", self.h_job),
            web.get(PREFIX + "jobs/{jid}/wait", self.h_job_wait),
            web.post(PREFIX + "jobs/{jid}/cancel", self.h_job_cancel),
            web.get(PREFIX + "library", self.h_library),
            web.get(PREFIX + "projects", self.h_projects),
        ]
