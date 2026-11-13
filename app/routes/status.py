"""Status, settings and the window's own doors: the status card, the system panel, updates,
the watchdog and the engine restart, the clipboard, support/rent, the update bar, the event
socket and the window's own files.

Part of the window's server: the methods below are mixed into `application.App` (see server.py).
Each one is an aiohttp handler or an assistant used only by them; the routes themselves are listed
in one place, `server.build_app`.
"""

from webcommon import (
    DATA_DIR, HOME, LONG_DIR, PNG_SIGNATURE, RENT_REFERRAL, RENT_SITE, SUPPORT_URL, UI_DIR,
    UI_TYPES, clipboard_has_picture, clipboard_picture, clipboard_set_picture, gpu_name
)

import asyncio
import ctypes
import os
import subprocess
import urllib.parse
import webbrowser
from datetime import datetime

from aiohttp import web

import catalog
import cloud
import engine
import report
import updater


class StatusRoutes:
    async def h_mirror(self, req):
        on = bool((await req.json()).get("on"))
        self.config.update(hf_mirror=on)
        self.apply_mirror()
        return web.json_response({"ok": True, "on": on})

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
            "journal": self.journal.tail(60),
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

    async def h_events(self, req):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(req)
        # the first message is the snapshot: the window syncs from the facts, then follows the events
        sub = self.events.attach(ws, hello=self.ui_snapshot())
        try:
            async for _ in ws:
                pass
        finally:
            await self.events.detach(sub)
        return ws

    async def h_index(self, _req):
        return web.FileResponse(UI_DIR / "index.html", headers={"Cache-Control": "no-store"})

    async def h_ui(self, req):
        """A file of the window (`/ui/...`) with its own content type; nothing outside these types."""
        rel = req.match_info.get("path", "").replace("/", os.sep)
        root = UI_DIR.resolve()
        path = (root / rel).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise web.HTTPNotFound()
        content_type = UI_TYPES.get(path.suffix.lower())
        if content_type is None:
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Content-Type": content_type, "Cache-Control": "no-cache"})
