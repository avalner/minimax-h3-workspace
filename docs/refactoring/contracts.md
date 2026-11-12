# Карта контрактов и точек изменения состояния

Сгенерировано: `python tests/contract_map.py > docs/refactoring/contracts.md`.
Список маршрутов взят из собранного приложения; события и места изменения состояния
найдены поиском по исходникам (ориентир для ревью, не гарантия полноты).

## HTTP: 140 маршрутов

### /

| метод | путь | обработчик |
| --- | --- | --- |
| GET | / | h_index |

### /api

| метод | путь | обработчик |
| --- | --- | --- |
| * | /api/clipboard/image | h_clipboard_image |
| DELETE | /api/agent/chats | h_chats_clear |
| DELETE | /api/agent/chats/{id} | h_chat_delete |
| DELETE | /api/agent/skills/{id} | h_skill_delete |
| DELETE | /api/library/{id} | h_library_delete |
| DELETE | /api/wf/{id} | h_wf_delete |
| GET | /api/action/info | h_action_info |
| GET | /api/agent/allow | h_agent_allow |
| GET | /api/agent/chats | h_chats |
| GET | /api/agent/chats/{id} | h_chat_get |
| GET | /api/agent/status | h_agent_status |
| GET | /api/agent/v1/assets | h_assets |
| GET | /api/agent/v1/assets/{aid} | h_asset |
| GET | /api/agent/v1/assets/{aid}/content | h_asset_content |
| GET | /api/agent/v1/capabilities | h_capabilities |
| GET | /api/agent/v1/enabled | h_enabled_get |
| GET | /api/agent/v1/jobs | h_jobs |
| GET | /api/agent/v1/jobs/{jid} | h_job |
| GET | /api/agent/v1/jobs/{jid}/wait | h_job_wait |
| GET | /api/agent/v1/library | h_library |
| GET | /api/agent/v1/ping | h_ping |
| GET | /api/audio/info | h_audio_info |
| GET | /api/civitai/search | h_civitai_search |
| GET | /api/clipboard/has-image | h_clipboard_has_image |
| GET | /api/cloud | h_cloud_get |
| GET | /api/events | h_events |
| GET | /api/ffmpeg/check-update | h_ffmpeg_check_update |
| GET | /api/ffmpeg/status | h_ffmpeg_status |
| GET | /api/file/{id} | h_file |
| GET | /api/hf/search | h_hf_search |
| GET | /api/input | h_input |
| GET | /api/job/{pid} | h_job |
| GET | /api/library | h_library |
| GET | /api/long/project | h_long_project |
| GET | /api/long/ref/{run}/{name} | h_long_ref |
| GET | /api/long/state | h_long_state |
| GET | /api/loras/conflicts | h_loras_conflicts |
| GET | /api/loras/custom | h_loras_custom |
| GET | /api/loras/registry | h_loras_registry |
| GET | /api/loras/tokens | h_loras_tokens_get |
| GET | /api/models/compat | h_models_compat |
| GET | /api/models/files | h_models_files |
| GET | /api/nodes | h_nodes |
| GET | /api/queue/saved | h_queue_saved |
| GET | /api/rent/info | h_rent_info |
| GET | /api/results | h_results_get |
| GET | /api/setup/info | h_setup_info |
| GET | /api/srcfile/{id} | h_srcfile |
| GET | /api/status | h_status |
| GET | /api/system | h_system |
| GET | /api/update/check | h_update_check |
| GET | /api/version | <lambda> |
| GET | /api/wf/list | h_wf_list |
| GET | /api/wf/{id} | h_wf_get |
| PATCH | /api/agent/skills/{id} | h_skill_patch |
| PATCH | /api/library/{id} | h_library_patch |
| POST | /api/agent/allow | h_agent_allow |
| POST | /api/agent/chat | h_agent_chat |
| POST | /api/agent/install | h_agent_install |
| POST | /api/agent/install/cancel | h_agent_install_cancel |
| POST | /api/agent/model | h_agent_model |
| POST | /api/agent/skills | h_skill_upload |
| POST | /api/agent/translate | h_agent_translate |
| POST | /api/agent/v1/assets | h_asset_add |
| POST | /api/agent/v1/enabled | h_enabled_set |
| POST | /api/agent/v1/jobs | h_job_add |
| POST | /api/agent/v1/jobs/{jid}/cancel | h_job_cancel |
| POST | /api/agent/variant | h_agent_variant |
| POST | /api/audio/separate | h_audio_separate |
| POST | /api/civitai/resolve | h_civitai_resolve |
| POST | /api/clipboard | h_clipboard |
| POST | /api/cloud | h_cloud_set |
| POST | /api/cloud/test | h_cloud_test |
| POST | /api/engine/restart | h_engine_restart |
| POST | /api/ffmpeg/cancel | h_ffmpeg_cancel |
| POST | /api/ffmpeg/download | h_ffmpeg_download |
| POST | /api/ffmpeg/download/cancel | h_ffmpeg_download_cancel |
| POST | /api/ffmpeg/montage | h_ffmpeg_montage |
| POST | /api/ffmpeg/open-folder | h_ffmpeg_open_folder |
| POST | /api/ffmpeg/reset-folder | h_ffmpeg_reset_folder |
| POST | /api/ffmpeg/set-folder | h_ffmpeg_set_folder |
| POST | /api/generate/action | h_generate_action |
| POST | /api/generate/actor | h_generate_actor |
| POST | /api/generate/custom | h_generate_custom |
| POST | /api/generate/inpaint | h_generate_inpaint |
| POST | /api/generate/krea | h_generate_krea |
| POST | /api/generate/lipsync | h_generate_lipsync |
| POST | /api/generate/long | h_generate_long |
| POST | /api/generate/qwen | h_generate_qwen |
| POST | /api/generate/refs | h_generate_refs |
| POST | /api/generate/swap | h_generate_swap |
| POST | /api/generate/video | h_generate_video |
| POST | /api/grain | h_grain |
| POST | /api/hf/resolve | h_hf_resolve |
| POST | /api/interrupt | h_interrupt |
| POST | /api/library/delete | h_library_delete_many |
| POST | /api/long/project | h_long_project |
| POST | /api/long/stage | h_long_stage |
| POST | /api/loras/custom | h_loras_custom_set |
| POST | /api/loras/custom/mode | h_loras_custom_mode |
| POST | /api/loras/download | h_loras_download |
| POST | /api/loras/download/cancel | h_loras_download_cancel |
| POST | /api/loras/meta | h_loras_meta_save |
| POST | /api/loras/open-folder | h_loras_open_folder |
| POST | /api/loras/tokens | h_loras_tokens_save |
| POST | /api/mode/add | h_mode_add |
| POST | /api/mode/offered | h_mode_offered |
| POST | /api/models/download | h_models_download |
| POST | /api/models/download/cancel | h_models_download_cancel |
| POST | /api/models/open | h_models_open |
| POST | /api/models/pick | h_models_pick |
| POST | /api/models/variant | h_models_variant |
| POST | /api/montage | h_montage |
| POST | /api/open-driver-page | h_open_driver |
| POST | /api/preview/keep | h_preview_keep |
| POST | /api/queue/discard | h_queue_discard |
| POST | /api/queue/resume | h_queue_resume |
| POST | /api/rent/open | h_rent_open |
| POST | /api/report | h_report |
| POST | /api/results | h_results_set |
| POST | /api/results/backfill | h_results_backfill |
| POST | /api/results/clean | h_results_clean |
| POST | /api/search-web | h_search_web |
| POST | /api/settings/mirror | h_mirror |
| POST | /api/settings/watchdog | h_watchdog_set |
| POST | /api/setup/cancel | h_setup_cancel |
| POST | /api/setup/cloud | h_setup_cloud |
| POST | /api/setup/external | h_setup_external |
| POST | /api/setup/install | h_setup_install |
| POST | /api/setup/reset | h_setup_reset |
| POST | /api/speech/folder | h_speech_folder |
| POST | /api/speech/transcribe | h_transcribe |
| POST | /api/support | h_support |
| POST | /api/update/install | h_update_install |
| POST | /api/upscale | h_upscale |
| POST | /api/upscale_file | h_upscale_file |
| POST | /api/wf/add | h_wf_add |
| PUT | /api/agent/chats/{id} | h_chat_put |

### /ui*

| метод | путь | обработчик |
| --- | --- | --- |
| STATIC | /ui* | статические файлы |

## События в окно (broadcast/SSE): 22

- `delta`
- `disable`
- `done`
- `engine`
- `error`
- `external_job`
- `ffmpegdl`
- `image_url`
- `input`
- `interrupted`
- `keep_one`
- `local`
- `loradl`
- `mdl`
- `notice`
- `preview_anim`
- `scale_down`
- `setup`
- `skills`
- `stage`
- `status`
- `text`

## Состояние заданий: 31

| файл | строка | код |
| --- | --- | --- |
| server.py | 773 | self.jobs.pop(pid, None) |
| server.py | 778 | self.jobs.pop(pid, None) |
| server.py | 924 | self.jobs.pop(jid, None) |
| server.py | 1030 | job["state"] = "running" |
| server.py | 1043 | job["state"] = "error" |
| server.py | 1049 | self.jobs.pop(pid, None) |
| server.py | 1053 | self.jobs.pop(pid, None) |
| server.py | 1146 | self.jobs.pop(pid, None)   # there is nothing to wait for any more |
| server.py | 1181 | self.jobs.pop(pid, None)   # the result is registered: only now the job leaves the queue |
| server.py | 1894 | self.jobs[pid] = self.restore_job(saved, pid, state=live) |
| server.py | 1903 | self.jobs[pid] = job |
| server.py | 1908 | self.jobs.pop(pid, None) |
| server.py | 1972 | out.update(state=job.get("state") or "queued", stage=job.get("stage"), step=job.get("step"), |
| server.py | 2162 | self.jobs[pid] = {"prompt_id": pid, "state": "queued", "stages": {}, "stage": None, |
| server.py | 2188 | self.jobs.pop(pid, None) |
| server.py | 2215 | self.jobs.pop(pid, None) |
| server.py | 2264 | self.jobs[pid] = {"prompt_id": pid, "state": "queued", "stages": stages, "stage": None, "meta": meta, "last_output": last_output, "titles": titles or {}, |
| server.py | 3974 | self.jobs.pop(pid, None) |
| server.py | 3982 | elif job and job["state"] == "queued": |
| server.py | 3985 | self.jobs.pop(pid, None) |
| server.py | 4043 | self.jobs.pop(pid, None) |
| server.py | 4068 | if job["state"] == "running" and silent > 600: |
| server.py | 4079 | self.jobs.pop(pid, None) |
| server.py | 4090 | if job["state"] == "queued" and age > 240: |
| server.py | 4096 | self.jobs.pop(pid, None) |
| server.py | 4101 | elif job["state"] == "running" and silent > 900: |
| server.py | 4107 | self.jobs.pop(pid, None) |
| server.py | 4115 | job["state"] = "error" |
| server.py | 4118 | self.jobs.pop(pid, None) |
| agentapi.py | 647 | running = job.get("state") == "running" |
| agentapi.py | 658 | running = job.get("state") == "running" |

## Черновики и Библиотека: 24

| файл | строка | код |
| --- | --- | --- |
| server.py | 486 | self.pending = {}  # item id -> item; memory only, so a restart forgets them |
| server.py | 1176 | self.library.add(item)   # may raise: then the job stays and this is tried again |
| server.py | 1235 | return self.library.get(item_id) or self.pending.get(item_id) |
| server.py | 1240 | self.pending[item["id"]] = item |
| server.py | 1241 | while len(self.pending) > PREVIEW_LIMIT: |
| server.py | 1242 | self.pending.pop(next(iter(self.pending))) |
| server.py | 1280 | self.library.save() |
| server.py | 1976 | items = [self.public(x) for x in (*self.library.items, *self.pending.values()) if x.get("prompt_id") == pid] |
| server.py | 2198 | self.library.add(item) |
| server.py | 3723 | self.library.add(item) |
| server.py | 4131 | item = self.pending.get(iid) |
| server.py | 4133 | known = self.library.get(iid) |
| server.py | 4137 | if self.library.get(iid) is not None:   # the list was written, the draft was not dropped yet |
| server.py | 4138 | self.pending.pop(iid, None) |
| server.py | 4139 | return web.json_response({"item": self.public(self.library.get(iid)), "kept": True}) |
| server.py | 4142 | self.library.add(item)          # writes the whole list; raises if the file cannot be replaced |
| server.py | 4148 | self.pending.pop(iid, None) |
| server.py | 4152 | item = self.library.get(req.match_info["id"]) |
| server.py | 4158 | self.library.save() |
| server.py | 4162 | item = self.library.get(req.match_info["id"]) |
| server.py | 4181 | self.library.remove(item["id"]) |
| server.py | 4189 | items = [x for x in (self.library.get(i) for i in ids) if x] |
| server.py | 4227 | self.library.remove(i) |
| server.py | 4254 | item = self.library.get(req.match_info["id"]) |

## Короткая память об исходах: 5

| файл | строка | код |
| --- | --- | --- |
| server.py | 1185 | self.remember(payload) |
| server.py | 1199 | self.recent[pid] = {"state": kind, "at": datetime.now().isoformat(timespec="seconds"), |
| server.py | 1208 | self.history[job["prompt_id"]] = {"meta": job.get("meta") or {}, "created": job.get("t0")} |
| server.py | 1914 | self.recent[pid] = {"state": "error", "at": datetime.now().isoformat(timespec="seconds"), |
| server.py | 1986 | r = self.recent[pid] |

## Фоновые задачи: 48

| файл | строка | код |
| --- | --- | --- |
| server.py | 531 | self.hw = await asyncio.to_thread(engine.detect_hardware) |
| server.py | 536 | await asyncio.to_thread(self.llm.stop) |
| server.py | 607 | self.packs_task = asyncio.create_task(self.install_missing_packs()) |
| server.py | 610 | self.ws_task = asyncio.create_task(self.comfy_ws_loop()) |
| server.py | 611 | self.guard_task = asyncio.create_task(self.guard_loop()) |
| server.py | 612 | self.watchdog_task = asyncio.create_task(self.jobs_watchdog())  # jobs ComfyUI has lost, not a hung card |
| server.py | 660 | await asyncio.to_thread(self.process.stop) |
| server.py | 661 | await asyncio.to_thread(self.llm.stop) |
| server.py | 819 | self.hw = await asyncio.to_thread(engine.detect_hardware) |
| server.py | 865 | gpu = await asyncio.to_thread(self.monitor.gpu) |
| server.py | 900 | await asyncio.to_thread(self.process.restart) |
| server.py | 904 | self.ext_proc = await asyncio.to_thread(engine.restart_external, self.stats, self.ext_log, self.comfy) |
| server.py | 1270 | await asyncio.to_thread(shutil.copy2, src, dest) |
| server.py | 1324 | raw = await asyncio.to_thread(path.read_bytes) |
| server.py | 1468 | self.dl_task = asyncio.create_task(run()) |
| server.py | 1494 | self.hw = await asyncio.to_thread(engine.detect_hardware) |
| server.py | 1597 | data = await asyncio.to_thread(self.monitor.sample, where) |
| server.py | 1616 | hw = await asyncio.to_thread(engine.detect_hardware, install_dir) |
| server.py | 1713 | hw = await asyncio.to_thread(engine.detect_hardware, install_dir) |
| server.py | 1724 | self.installer.task = asyncio.create_task(self.run_install(install_dir, models_dir)) |
| server.py | 1737 | await asyncio.to_thread(self.process.stop) |
| server.py | 1788 | hw = await asyncio.to_thread(engine.detect_hardware, self.config.get("install_dir")) |
| server.py | 1799 | text = await asyncio.to_thread(report.build, { |
| server.py | 2062 | return web.json_response({"have": await asyncio.to_thread(clipboard_has_picture)}) |
| server.py | 2070 | if await asyncio.to_thread(clipboard_set_picture, data): |
| server.py | 2073 | picture = await asyncio.to_thread(clipboard_picture) |
| server.py | 2089 | await asyncio.to_thread(self.process.stop) |
| server.py | 2164 | asyncio.create_task(self.poll_cloud(rh, pid)) |
| server.py | 2250 | gpu = await asyncio.to_thread(self.monitor.gpu) |
| server.py | 2985 | dl = asyncio.create_task(civitai_client.download_file_stream(self.session, url, str(dest), token)) |
| server.py | 3004 | self.lora_dl_task = asyncio.create_task(work()) |
| server.py | 3212 | return web.json_response(await asyncio.to_thread(work)) |
| server.py | 3356 | return await asyncio.to_thread(p.read_bytes) |
| server.py | 3528 | res = await asyncio.to_thread(ffmpeg_mgr.download_and_extract_ffmpeg_sync, folder, prog, cancel) |
| server.py | 3540 | self.ffmpeg_dl_task = asyncio.create_task(work()) |
| server.py | 3568 | res = await asyncio.to_thread(ffmpeg_mgr.check_ffmpeg_update, st.get("version")) |
| server.py | 3684 | task = asyncio.create_task(self.run_ffmpeg_job(pid, dest, paths, cleanup, options, cancel, len(ids), preview)) |
| server.py | 3706 | await asyncio.to_thread(ffmpeg_mgr.run_studio_montage, st["ffmpeg_path"], st["ffprobe_path"], |
| server.py | 3708 | info = await asyncio.to_thread(ffmpeg_mgr.probe_media, st["ffprobe_path"], str(dest)) |
| server.py | 3989 | task = asyncio.create_task(self.interrupt_watch(pid)) |
| server.py | 4177 | gone = set(await asyncio.to_thread(send_to_recycle_bin, todo)) if todo else set() |
| server.py | 4326 | _, _, found = await asyncio.to_thread(ag.find_gguf, custom["dir"]) |
| server.py | 4390 | asyncio.create_task(run()) |
| server.py | 4408 | found = await asyncio.to_thread(speech.locate, self.llm.root, folder) |
| server.py | 4462 | self.hw = await asyncio.to_thread(engine.detect_hardware) |
| server.py | 4463 | model, proj, models = await asyncio.to_thread(ag.find_gguf, folder, body.get("model"), self.hw.get("vram_gb")) |
| server.py | 4635 | sid = await asyncio.to_thread(self.skills.save_upload, files) |
| agentapi.py | 317 | gpu = await asyncio.to_thread(app.monitor.gpu) |

## Запись на диск: 23

| файл | строка | код |
| --- | --- | --- |
| server.py | 230 | path.write_text(CLIP_HELPER, encoding="utf-8-sig") |
| server.py | 274 | path.write_bytes(data) |
| server.py | 447 | atomic_write_json(self.path, self.items, indent=1) |
| server.py | 1137 | (d / f"state_{lg['part']}{sfx}.latent").write_bytes(body) |
| server.py | 1138 | atomic_write_json(d / f"state_{lg['part']}{sfx}.json", |
| server.py | 1809 | path.write_text(text, encoding="utf-8") |
| server.py | 1851 | atomic_write_json(QUEUE_FILE, items) |
| server.py | 2019 | tmp.write_bytes(data) |
| server.py | 2187 | dest.write_bytes(await r.read()) |
| server.py | 2314 | atomic_write_json(self.wf_path(wid), {"name": name, "graph": graph}) |
| server.py | 3147 | (d / (key + ext)).write_bytes(raw) |
| server.py | 3158 | atomic_write_json(d / "project.json", body["project"]) |
| server.py | 3624 | p.write_bytes(files[k][1]) |
| server.py | 3665 | music.write_bytes(files["music"][1]) |
| server.py | 3677 | probe.write_bytes(b"") |
| server.py | 4617 | atomic_write_json(p, data) |
| agentapi.py | 186 | atomic_write_json(self._meta_path(asset_id), meta) |
| agentapi.py | 220 | atomic_write_json(path, data, indent=1) |
| engine.py | 62 | atomic_write_json(self.path, data, indent=1) |
| engine.py | 358 | (comfy_root / "ComfyUI" / "extra_model_paths.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8") |
| ffmpeg_mgr.py | 1128 | concat_txt.write_text("".join(_concat_line(clip) + "\n" for clip in clips), encoding="utf-8") |
| ffmpeg_mgr.py | 1530 | script_path.write_text(filter_complex, encoding="utf-8") |
| agent.py | 177 | target.write_bytes(data) |

