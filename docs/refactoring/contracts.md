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

## Состояние заданий: 34

| файл | строка | код |
| --- | --- | --- |
| server.py | 776 | self.jobs.pop(pid, None) |
| server.py | 781 | self.jobs.pop(pid, None) |
| server.py | 926 | self.try_set_job_state(self.jobs[jid], JobState.CANCELED if kind == "interrupted" else JobState.FAILED, |
| server.py | 929 | self.jobs.pop(jid, None) |
| server.py | 1054 | self.jobs.pop(pid, None) |
| server.py | 1059 | self.jobs.pop(pid, None) |
| server.py | 1123 | job["state"] = public_state(target) |
| server.py | 1127 | job["state"] = public_state(record.state) |
| server.py | 1205 | self.jobs.pop(pid, None)   # there is nothing to wait for any more |
| server.py | 1251 | self.jobs.pop(pid, None)   # the result is registered: only now the job leaves the queue |
| server.py | 1968 | self.jobs[pid] = self.restore_job(saved, pid, state=live) |
| server.py | 1977 | self.jobs[pid] = job |
| server.py | 1982 | self.jobs.pop(pid, None) |
| server.py | 2047 | out.update(state=job.get("state") or "queued", stage=job.get("stage"), step=job.get("step"), |
| server.py | 2238 | self.jobs[pid] = {"prompt_id": pid, "state": "queued", "stages": {}, "stage": None, |
| server.py | 2241 | self.set_job_state(self.jobs[pid], JobState.QUEUED) |
| server.py | 2270 | self.jobs.pop(pid, None) |
| server.py | 2299 | self.jobs.pop(pid, None) |
| server.py | 2309 | self.jobs.pop(pid, None) |
| server.py | 2358 | self.jobs[pid] = {"prompt_id": pid, "state": "queued", "stages": stages, "stage": None, "meta": meta, "last_output": last_output, "titles": titles or {}, |
| server.py | 2361 | self.set_job_state(self.jobs[pid], JobState.QUEUED)   # created → submitting → queued, in the record |
| server.py | 4093 | self.jobs.pop(pid, None) |
| server.py | 4101 | elif job and job["state"] == "queued": |
| server.py | 4105 | self.jobs.pop(pid, None) |
| server.py | 4163 | self.jobs.pop(pid, None) |
| server.py | 4188 | if job["state"] == "running" and silent > 600: |
| server.py | 4199 | self.jobs.pop(pid, None) |
| server.py | 4210 | if job["state"] == "queued" and age > 240: |
| server.py | 4216 | self.jobs.pop(pid, None) |
| server.py | 4221 | elif job["state"] == "running" and silent > 900: |
| server.py | 4227 | self.jobs.pop(pid, None) |
| server.py | 4238 | self.jobs.pop(pid, None) |
| agentapi.py | 660 | running = job.get("state") == "running" |
| agentapi.py | 671 | running = job.get("state") == "running" |

## Черновики и Библиотека: 24

| файл | строка | код |
| --- | --- | --- |
| server.py | 489 | self.pending = {}  # item id -> item; memory only, so a restart forgets them |
| server.py | 1239 | self.library.add(item)   # may raise: then the job stays and this is tried again |
| server.py | 1305 | return self.library.get(item_id) or self.pending.get(item_id) |
| server.py | 1310 | self.pending[item["id"]] = item |
| server.py | 1311 | while len(self.pending) > PREVIEW_LIMIT: |
| server.py | 1312 | self.pending.pop(next(iter(self.pending))) |
| server.py | 1350 | self.library.save() |
| server.py | 2052 | items = [self.public(x) for x in (*self.library.items, *self.pending.values()) if x.get("prompt_id") == pid] |
| server.py | 2280 | self.library.add(item) |
| server.py | 3836 | self.library.add(item) |
| server.py | 4251 | item = self.pending.get(iid) |
| server.py | 4253 | known = self.library.get(iid) |
| server.py | 4257 | if self.library.get(iid) is not None:   # the list was written, the draft was not dropped yet |
| server.py | 4258 | self.pending.pop(iid, None) |
| server.py | 4259 | return web.json_response({"item": self.public(self.library.get(iid)), "kept": True}) |
| server.py | 4262 | self.library.add(item)          # writes the whole list; raises if the file cannot be replaced |
| server.py | 4268 | self.pending.pop(iid, None) |
| server.py | 4272 | item = self.library.get(req.match_info["id"]) |
| server.py | 4278 | self.library.save() |
| server.py | 4282 | item = self.library.get(req.match_info["id"]) |
| server.py | 4301 | self.library.remove(item["id"]) |
| server.py | 4309 | items = [x for x in (self.library.get(i) for i in ids) if x] |
| server.py | 4347 | self.library.remove(i) |
| server.py | 4374 | item = self.library.get(req.match_info["id"]) |

## Короткая память об исходах: 5

| файл | строка | код |
| --- | --- | --- |
| server.py | 1255 | self.remember(payload) |
| server.py | 1269 | self.recent[pid] = {"state": kind, "at": datetime.now().isoformat(timespec="seconds"), |
| server.py | 1278 | self.history[job["prompt_id"]] = {"meta": job.get("meta") or {}, "created": job.get("t0")} |
| server.py | 1988 | self.recent[pid] = {"state": "error", "at": datetime.now().isoformat(timespec="seconds"), |
| server.py | 2062 | r = self.recent[pid] |

## Фоновые задачи: 48

| файл | строка | код |
| --- | --- | --- |
| server.py | 534 | self.hw = await asyncio.to_thread(engine.detect_hardware) |
| server.py | 539 | await asyncio.to_thread(self.llm.stop) |
| server.py | 610 | self.packs_task = asyncio.create_task(self.install_missing_packs()) |
| server.py | 613 | self.ws_task = asyncio.create_task(self.comfy_ws_loop()) |
| server.py | 614 | self.guard_task = asyncio.create_task(self.guard_loop()) |
| server.py | 615 | self.watchdog_task = asyncio.create_task(self.jobs_watchdog())  # jobs ComfyUI has lost, not a hung card |
| server.py | 663 | await asyncio.to_thread(self.process.stop) |
| server.py | 664 | await asyncio.to_thread(self.llm.stop) |
| server.py | 822 | self.hw = await asyncio.to_thread(engine.detect_hardware) |
| server.py | 868 | gpu = await asyncio.to_thread(self.monitor.gpu) |
| server.py | 903 | await asyncio.to_thread(self.process.restart) |
| server.py | 907 | self.ext_proc = await asyncio.to_thread(engine.restart_external, self.stats, self.ext_log, self.comfy) |
| server.py | 1340 | await asyncio.to_thread(shutil.copy2, src, dest) |
| server.py | 1394 | raw = await asyncio.to_thread(path.read_bytes) |
| server.py | 1538 | self.dl_task = asyncio.create_task(run()) |
| server.py | 1564 | self.hw = await asyncio.to_thread(engine.detect_hardware) |
| server.py | 1667 | data = await asyncio.to_thread(self.monitor.sample, where) |
| server.py | 1686 | hw = await asyncio.to_thread(engine.detect_hardware, install_dir) |
| server.py | 1783 | hw = await asyncio.to_thread(engine.detect_hardware, install_dir) |
| server.py | 1794 | self.installer.task = asyncio.create_task(self.run_install(install_dir, models_dir)) |
| server.py | 1807 | await asyncio.to_thread(self.process.stop) |
| server.py | 1858 | hw = await asyncio.to_thread(engine.detect_hardware, self.config.get("install_dir")) |
| server.py | 1869 | text = await asyncio.to_thread(report.build, { |
| server.py | 2138 | return web.json_response({"have": await asyncio.to_thread(clipboard_has_picture)}) |
| server.py | 2146 | if await asyncio.to_thread(clipboard_set_picture, data): |
| server.py | 2149 | picture = await asyncio.to_thread(clipboard_picture) |
| server.py | 2165 | await asyncio.to_thread(self.process.stop) |
| server.py | 2242 | asyncio.create_task(self.poll_cloud(rh, pid)) |
| server.py | 2344 | gpu = await asyncio.to_thread(self.monitor.gpu) |
| server.py | 3098 | dl = asyncio.create_task(civitai_client.download_file_stream(self.session, url, str(dest), token)) |
| server.py | 3117 | self.lora_dl_task = asyncio.create_task(work()) |
| server.py | 3325 | return web.json_response(await asyncio.to_thread(work)) |
| server.py | 3469 | return await asyncio.to_thread(p.read_bytes) |
| server.py | 3641 | res = await asyncio.to_thread(ffmpeg_mgr.download_and_extract_ffmpeg_sync, folder, prog, cancel) |
| server.py | 3653 | self.ffmpeg_dl_task = asyncio.create_task(work()) |
| server.py | 3681 | res = await asyncio.to_thread(ffmpeg_mgr.check_ffmpeg_update, st.get("version")) |
| server.py | 3797 | task = asyncio.create_task(self.run_ffmpeg_job(pid, dest, paths, cleanup, options, cancel, len(ids), preview)) |
| server.py | 3819 | await asyncio.to_thread(ffmpeg_mgr.run_studio_montage, st["ffmpeg_path"], st["ffprobe_path"], |
| server.py | 3821 | info = await asyncio.to_thread(ffmpeg_mgr.probe_media, st["ffprobe_path"], str(dest)) |
| server.py | 4109 | task = asyncio.create_task(self.interrupt_watch(pid)) |
| server.py | 4297 | gone = set(await asyncio.to_thread(send_to_recycle_bin, todo)) if todo else set() |
| server.py | 4446 | _, _, found = await asyncio.to_thread(ag.find_gguf, custom["dir"]) |
| server.py | 4510 | asyncio.create_task(run()) |
| server.py | 4528 | found = await asyncio.to_thread(speech.locate, self.llm.root, folder) |
| server.py | 4582 | self.hw = await asyncio.to_thread(engine.detect_hardware) |
| server.py | 4583 | model, proj, models = await asyncio.to_thread(ag.find_gguf, folder, body.get("model"), self.hw.get("vram_gb")) |
| server.py | 4755 | sid = await asyncio.to_thread(self.skills.save_upload, files) |
| agentapi.py | 319 | gpu = await asyncio.to_thread(app.monitor.gpu) |

## Запись на диск: 23

| файл | строка | код |
| --- | --- | --- |
| server.py | 234 | path.write_text(CLIP_HELPER, encoding="utf-8-sig") |
| server.py | 278 | path.write_bytes(data) |
| server.py | 450 | atomic_write_json(self.path, self.items, indent=1) |
| server.py | 1195 | (d / f"state_{lg['part']}{sfx}.latent").write_bytes(body) |
| server.py | 1196 | atomic_write_json(d / f"state_{lg['part']}{sfx}.json", |
| server.py | 1879 | path.write_text(text, encoding="utf-8") |
| server.py | 1921 | atomic_write_json(QUEUE_FILE, items) |
| server.py | 2095 | tmp.write_bytes(data) |
| server.py | 2269 | dest.write_bytes(await r.read()) |
| server.py | 2410 | atomic_write_json(self.wf_path(wid), {"name": name, "graph": graph}) |
| server.py | 3260 | (d / (key + ext)).write_bytes(raw) |
| server.py | 3271 | atomic_write_json(d / "project.json", body["project"]) |
| server.py | 3737 | p.write_bytes(files[k][1]) |
| server.py | 3778 | music.write_bytes(files["music"][1]) |
| server.py | 3790 | probe.write_bytes(b"") |
| server.py | 4737 | atomic_write_json(p, data) |
| agentapi.py | 188 | atomic_write_json(self._meta_path(asset_id), meta) |
| agentapi.py | 222 | atomic_write_json(path, data, indent=1) |
| engine.py | 62 | atomic_write_json(self.path, data, indent=1) |
| engine.py | 358 | (comfy_root / "ComfyUI" / "extra_model_paths.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8") |
| ffmpeg_mgr.py | 1128 | concat_txt.write_text("".join(_concat_line(clip) + "\n" for clip in clips), encoding="utf-8") |
| ffmpeg_mgr.py | 1530 | script_path.write_text(filter_complex, encoding="utf-8") |
| agent.py | 177 | target.write_bytes(data) |

