"""The Library and the files on disk: cards, filtering, copies, results, previews, the raw
input a job was made from, and the sound of a clip.

Part of the window's server: the methods below are mixed into `application.App` (see server.py).
Each one is an aiohttp handler or an assistant used only by them; the routes themselves are listed
in one place, `server.build_app`.
"""

from webcommon import (
    CLIENT_ID, DATA_DIR, STORE_ERRORS, comfy_error_text, send_to_recycle_bin
)

import asyncio
import hashlib
import json
import re
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web


class LibraryRoutes:
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
            self.drafts_repo.delete(iid)
            self.pending.pop(iid, None)
            return web.json_response({"item": self.public(self.library.get(iid)), "kept": True})
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

    async def h_preview_delete(self, req):
        """Delete a draft: the record, its media row and its files (files go to the Recycle Bin).

        Only a draft: an item that reached the Library is deleted there, where it is visible. A file
        that could not be recycled leaves the draft in place — the window must not show a draft whose
        clip is still on the disk but no longer reachable.
        """
        item_id = str(req.match_info["id"])
        if self.library.get(item_id) is not None:
            raise web.HTTPConflict(text="Этот клип уже в Библиотеке: удаляйте его там")
        item = self.pending.get(item_id)
        if item is None:
            raise web.HTTPNotFound()
        if not await self.recycle_draft(item):
            raise web.HTTPInternalServerError(text="Не удалось переместить файл в корзину: черновик оставлен на месте")
        await self.broadcast({"type": "draft_removed", "id": item_id})
        return web.json_response({"ok": True})

    async def recycle_draft(self, item):
        """A draft and its files: True when the record may be forgotten.

        The files go to the Recycle Bin first; if one of them would not move, nothing is forgotten
        (see `h_preview_delete` and `delete_project`, which keeps such a draft instead of losing it).
        """
        paths = {path for path in (self.file_path(item), Path(item["local"]) if item.get("local") else None)
                 if path and path.exists()}
        gone = set(await asyncio.to_thread(send_to_recycle_bin, list(paths))) if paths else set()
        if paths - gone:
            return False
        self.pending.pop(item["id"], None)
        with self.db.transaction():
            self.drafts_repo.delete(item["id"])
            self.media_repo.delete(item["id"])
        return True

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
