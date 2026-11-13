"""Generation: the form of every mode (video, lipsync, references, actor, swap, action, the
long studio, pictures, own workflows), the inputs a job is made from and the progress of
a CPU pass (upscale, grain).

Part of the window's server: the methods below are mixed into `application.App` (see server.py).
Each one is an aiohttp handler or an assistant used only by them; the routes themselves are listed
in one place, `server.build_app`.
"""

from webcommon import (
    CLIENT_ID, LONG_DIR, WORKFLOWS_DIR, comfy_error_text, freshest_long_run, http_refusal,
    lora_strength, media_input, round_size, split_frames
)

import asyncio
import hashlib
import json
import random
import re
import shutil
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web

import catalog
import customwf
import engine
import report
import workflows as wf
from backends.base import BackendUnavailable, SubmitRejected
from domain.errors import DomainError
from domain.jobs import InputRef, JobSpec, MAX_SECONDS, ResultPolicy
from storage.atomic import atomic_write_json


class GenerateRoutes:
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
            user_text = form.get("prompt", "").strip()
            lora = None
            try:
                _, loras = await self.comfy_json("GET", "/models/loras", timeout=aiohttp.ClientTimeout(total=15))
                lora = next((f for f in (loras or []) if f.lower().endswith("qwen-image-2.1-outpaint-v2.safetensors")), None)
            except Exception:
                pass
            # the LoRA wants its own sentence and the flat gray fill the client put on the canvas; without the LoRA
            # the old, plainer instruction works better (and the client fills the edges with a blurred copy)
            prompt = (wf.OUTPAINT_PROMPT + (f" Scene: {user_text}" if user_text else "")) if lora \
                else (user_text or wf.OUTPAINT_PROMPT_LEGACY)
            try:
                ow, oh = int(form.get("ow", "0")), int(form.get("oh", "0"))
            except ValueError:
                ow = oh = 0
            graph, stages = wf.qwen_outpaint_graph(self.model_names, prompt, image, form["_orig"], x, y, (w, h), seed, lora,
                                                   (ow, oh) if ow > 0 and oh > 0 else None)
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
        except DomainError as e:
            http_refusal(e)
        inputs = []
        if first:
            inputs.append(media_input("Первый кадр" + (" · зациклено" if loop else ""), "image", comfy=first))
        if last and last != first:
            inputs.append(media_input("Последний кадр", "image", comfy=last))
        meta = {"mode": "video", "sub": spec.sub, "prompt": spec.prompt, "aspect": spec.aspect, "q": spec.quality,
                "len": spec.seconds, "res": f"{spec.width}×{spec.height}", "seed": spec.seed, "inputs": inputs,
                # the parameters themselves, not only the caption: a restore must not parse "· Fused Turbo"
                "model": spec.model, "acceleration": spec.acceleration}
        try:
            record, _ = await self.start_video_job(spec, meta=meta, preview=preview)
        except SubmitRejected as e:
            raise web.HTTPBadRequest(text=str(e))
        except BackendUnavailable as e:
            raise web.HTTPServiceUnavailable(text=(
                f"{e}. Задание записано как неопределённое: проверьте очередь ComfyUI, прежде чем повторять — "
                "оно могло уйти."))
        except DomainError as e:
            http_refusal(e)
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
            "res": f"{width}×{height}", "seed": seed, "steps": steps, "ref_size": ref_size, "inputs": inputs,
            "model": "fused" if fused else "fast", "acceleration": "normal"}, preview=preview)

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
        """The project of a long video (scenes, settings) is kept on disk too, so it survives anything that happens to the browser storage.

        A film belongs to a project, exactly like a clip: the record carries `project`, and a GET
        without a run id answers with the freshest film of that project, so the studio never opens
        another project's work. `?last=1` stays for the films saved before a film knew its project.
        """
        if req.method == "POST":
            body = await req.json()
            run = re.sub(r"[^A-Za-z0-9]", "", str(body.get("run", "")))[:32]
            project = body.get("project")
            if not run or not isinstance(project, dict):
                raise web.HTTPBadRequest(text="bad project")
            # stamped here and never moved afterwards: the project the film was started in
            wanted = str(body.get("project_id") or project.get("project") or "")[:40]
            if not wanted or not self.projects_repo.exists(wanted):
                wanted = self.active_project()
            project["project"] = wanted
            d = LONG_DIR / run
            d.mkdir(parents=True, exist_ok=True)
            atomic_write_json(d / "project.json", project)
            return web.json_response({"ok": True, "project": wanted})
        run = re.sub(r"[^A-Za-z0-9]", "", req.query.get("run", ""))[:32]
        wanted = str(req.query.get("project") or "")[:40]
        if not run and wanted:   # the freshest film of this project
            run = freshest_long_run(wanted)
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
