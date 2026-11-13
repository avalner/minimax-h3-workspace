"""Models and the setup wizard: what is installed, what is missing, downloads, the model pick
per mode, node packs and the wizard's own steps.

Part of the window's server: the methods below are mixed into `application.App` (see server.py).
Each one is an aiohttp handler or an assistant used only by them; the routes themselves are listed
in one place, `server.build_app`.
"""

from webcommon import (
    CLIENT_ID, ROOT
)

import asyncio
import re
import sys
import urllib.parse
import webbrowser
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import web

import catalog
import engine


class ModelRoutes:
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
        """Files ComfyUI sees in the folder this model role lives in, judged for this role.

        Every file gets `fit`: "ok" (clearly this role's file), "unknown" (the name fits, the type
        cannot be told) or "no" (another role's file or another model type). The window shows the
        fitting files at once and keeps the rest behind «Показать все»: a file of another section is
        not offered by accident, but a renamed model of the user stays reachable. `current` marks the
        file this role uses now, so re-opening the picker after a reload shows the same choice.
        """
        role = req.query.get("role", "")
        if role not in catalog.VARIANTS:
            raise web.HTTPBadRequest(text="Неизвестная модель")
        cat = next(iter(catalog.VARIANTS[role].values()))["category"]
        try:
            _, lst = await self.comfy_json("GET", f"/models/{cat}")
        except Exception:
            raise web.HTTPServiceUnavailable(text="ComfyUI не отвечает")
        current = self.model_names.get(role, "")
        files = []
        for name in lst or []:
            fit, why = catalog.file_fit(role, name)
            files.append({"name": name, "fit": fit, "why": why, "current": catalog.same_file(name, current)})
        order = {"ok": 0, "unknown": 1, "no": 2}
        files.sort(key=lambda f: (not f["current"], order.get(f["fit"], 2), f["name"].lower()))
        return web.json_response({"files": files, "folder": cat, "role": role,
                                  "marks": catalog.marks_text(role), "current": current})

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
        current = self.model_names.get(role, "")
        pick = (self.config.get("model_pick") or {}).get(role)
        # a pick whose file disappeared is kept in the settings, but the role falls back to the
        # built-in file: the window must say «встроенная», not claim a model that is not used
        applied = bool(pick) and catalog.same_file(pick, current)
        notes = catalog.compat_notes(mode, role, name or current, self.hw.get("compute_cap") or 0)
        if not applied:  # the notes describe the built-in file now, not a hand-picked one
            notes = [(lv, t) for lv, t in notes if not (lv == "info" and "Это своя модель" in t)]
        if pick and not applied:
            notes.insert(0, ("warn", (
                f"Выбранный файл «{catalog.file_base(pick)}» не найден в папке моделей этого раздела: "
                f"сейчас используется «{catalog.file_base(current)}». Верните файл на место или нажмите «Вернуть встроенную».")))
        missing = {m["role"] for m in self.missing}
        picked = self.config.get("model_pick") or {}
        loras = [{"role": r, "title": catalog.ROLE_TITLES.get(r, r), "name": self.model_names.get(r, ""), "have": r not in missing,
                  "must": r in catalog.MODE_ROLES.get(mode, []), "mine": r in picked}
                 for r in catalog.MODE_ROLES.get(mode, []) + catalog.MODE_OPTIONAL.get(mode, []) if r.endswith("_lora") and r in self.model_names]
        loras += [{"title": "Своя LoRA", "name": x["file"], "have": True, "must": False, "mine": True, "custom": True, "off": mode in (x.get("skip") or [])}
                  for x in self.unique_loras() if x.get("on", True)]
        return web.json_response({"notes": [{"level": lv, "text": t} for lv, t in notes], "loras": loras,
                                  "picked": pick, "applied": applied, "name": current})

    async def h_models_pick(self, req):
        body = await req.json()
        role, name = body.get("role"), body.get("name") or ""
        if role not in catalog.VARIANTS:
            raise web.HTTPBadRequest(text="Неизвестная модель")
        if name:
            # the file of another role would be silently ignored later (resolve_models never feeds it to
            # this role), so say it here instead of saving a choice that cannot be applied
            other = catalog.KNOWN_FILES.get(catalog.file_base(name).lower())
            if other and other != role:
                raise web.HTTPBadRequest(text=(
                    f"Файл «{catalog.file_base(name)}» — это модель «{catalog.ROLE_TITLES.get(other, other)}», "
                    f"а здесь нужна «{catalog.ROLE_TITLES.get(role, role)}». Выберите файл из списка этого раздела."))
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
