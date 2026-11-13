"""LoRA: the registry of what ComfyUI sees, the user's own switches, tokens, the Civitai and
Hugging Face catalogs, downloads.

Part of the window's server: the methods below are mixed into `application.App` (see server.py).
Each one is an aiohttp handler or an assistant used only by them; the routes themselves are listed
in one place, `server.build_app`.
"""

from webcommon import (
    lora_strength, open_in_explorer, safe_lora_name
)

import asyncio
import contextlib
import urllib.parse
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web

import catalog
import civitai_client
import hf_client
import loras_registry


class LoraRoutes:
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
