"""Montage and FFmpeg: the reel assembled on the CPU, its plan, the download of FFmpeg and
the folder it lives in.

Part of the window's server: the methods below are mixed into `application.App` (see server.py).
Each one is an aiohttp handler or an assistant used only by them; the routes themselves are listed
in one place, `server.build_app`.
"""

from webcommon import (
    DATA_DIR, STORE_ERRORS, TOOLS_DIR, media_input, open_in_explorer
)

import asyncio
import json
import random
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import web

import ffmpeg_mgr
import mp4info
import workflows as wf
from domain.jobs import JobState


class MontageRoutes:
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
        record = self.begin_submission(None, None, {}, {"mode": "montage", "seed": 0}, preview,
                                       backend="ffmpeg", backend_id="ffmpeg:local")
        record.prompt_id = record.backend_job_id = pid
        record.apply(JobState.QUEUED, now=datetime.now().timestamp())
        self.jobs_repo.save(record)
        cancel = threading.Event()
        task = self.tasks.spawn(f"ffmpeg:{pid}",
                                self.run_ffmpeg_job(pid, dest, paths, cleanup, options, cancel, len(ids), preview))
        self.ffmpeg_jobs[pid] = {"cancel": cancel, "task": task, "dest": dest, "record": record}
        return web.json_response({"prompt_id": pid})

    async def run_ffmpeg_job(self, pid, dest, paths, cleanup, options, cancel, count, preview):
        """The montage itself: FFmpeg in a worker thread, progress into the queue dock, the result into the Library."""
        st = self.ffmpeg_status()
        loop = asyncio.get_running_loop()
        last = {"pct": -1}
        record = self.jobs_repo.by_prompt(pid)
        if record is not None:
            record.apply(JobState.RUNNING, now=datetime.now().timestamp())
            self.jobs_repo.save(record)
        # the project the montage was asked in: the record keeps it, the item carries it
        project = ((record.legacy or {}).get("meta") or {}).get("project") if record is not None else None
        rendered = False

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
            rendered = True
            res = f'{v["width"]}×{v["height"]}' if v else ""
            word = "клип" if count == 1 else "клипа" if count < 5 else "клипов"
            item = {"id": uuid.uuid4().hex[:12], "kind": "video", "mode": "montage",
                    "sub": f"FFmpeg · {count} {word}", "prompt": "",
                    "aspect": options.get("aspect") if options.get("aspect") != "original" else "",
                    "q": "", "len": round(float((info or {}).get("duration") or 0), 1), "res": res,
                    "seed": 0, "prompt_id": pid, "date": datetime.now().isoformat(timespec="seconds"),
                    "file": {"type": "local", "path": str(dest), "filename": dest.name},
                    "project": project or self.active_project(),
                    "origin": {"kind": "ffmpeg"}}
            if record is not None:
                record.legacy["local_item"] = item
                record.apply(JobState.FINALIZING, now=datetime.now().timestamp())
                self.jobs_repo.save(record)
            await self.media.publish_local({"prompt_id": pid, "record": record, "preview": preview},
                                           item, notify=self.broadcast)
        except Exception as ex:  # noqa: BLE001 - cancel and ffmpeg errors both land here
            if rendered:
                self.note_error(f"Монтаж {pid} готов, но ожидает регистрации: {ex}")
                await self.broadcast({"type": "notice", "text": f"Монтаж сохранён в {dest}. Регистрация повторится при запуске."})
                return
            dest.unlink(missing_ok=True)
            if record is not None:
                record.apply(JobState.CANCELED if cancel.is_set() else JobState.FAILED,
                             now=datetime.now().timestamp(), error=str(ex))
                self.jobs_repo.save(record)
            msg = "Сборка остановлена" if cancel.is_set() else f"FFmpeg не справился: {ex}"
            await self.broadcast({"type": "error", "prompt_id": pid, "message": str(msg)[:300]})
        finally:
            if record is not None and not record.state.terminal and record.state is not JobState.FINALIZING:
                record.apply(JobState.CANCELED, now=datetime.now().timestamp())
                self.jobs_repo.save(record)
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
