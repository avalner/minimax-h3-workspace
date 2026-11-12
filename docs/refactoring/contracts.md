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

## Состояние заданий: 47

| файл | строка | код |
| --- | --- | --- |
| server.py | 913 | self.jobs.pop(pid, None) |
| server.py | 918 | self.jobs.pop(pid, None) |
| server.py | 1038 | self.try_set_job_state(self.jobs[jid], JobState.CANCELED if kind == "interrupted" else JobState.FAILED, |
| server.py | 1041 | self.jobs.pop(jid, None) |
| server.py | 1164 | self.jobs.pop(pid, None) |
| server.py | 1169 | self.jobs.pop(pid, None) |
| server.py | 1239 | self.jobs[pid] = job |
| server.py | 1264 | job["state"] = public_state(target) |
| server.py | 1267 | changed = record.apply(target, now=datetime.now().timestamp(), error=error) |
| server.py | 1268 | job["state"] = public_state(record.state) |
| server.py | 1328 | self.jobs.pop(pid, None)   # there is nothing to wait for any more |
| server.py | 1334 | self.jobs.pop(pid, None)   # the result is registered: only now the job leaves the queue |
| server.py | 2109 | self.jobs[pid] = job |
| server.py | 2142 | self.jobs[pid] = job |
| server.py | 2151 | self.jobs[pid] = job |
| server.py | 2156 | self.jobs.pop(pid, None) |
| server.py | 2223 | out.update(state=job.get("state") or "queued", stage=job.get("stage"), step=job.get("step"), |
| server.py | 2414 | self.jobs[pid] = {"prompt_id": pid, "state": "queued", "stages": {}, "stage": None, |
| server.py | 2417 | self.set_job_state(self.jobs[pid], JobState.QUEUED) |
| server.py | 2463 | self.jobs.pop(pid, None) |
| server.py | 2496 | self.jobs.pop(pid, None) |
| server.py | 4340 | self.jobs.pop(pid, None) |
| server.py | 4350 | self.jobs.pop(pid, None) |
| server.py | 4358 | elif job["state"] == "queued": |
| server.py | 4362 | self.jobs.pop(pid, None) |
| server.py | 4420 | self.jobs.pop(pid, None) |
| server.py | 4445 | if job["state"] == "running" and silent > 600: |
| server.py | 4456 | self.jobs.pop(pid, None) |
| server.py | 4467 | if job["state"] == "queued" and age > 240: |
| server.py | 4473 | self.jobs.pop(pid, None) |
| server.py | 4478 | elif job["state"] == "running" and silent > 900: |
| server.py | 4484 | self.jobs.pop(pid, None) |
| server.py | 4495 | self.jobs.pop(pid, None) |
| agentapi.py | 651 | running = job.get("state") == "running" |
| agentapi.py | 662 | running = job.get("state") == "running" |
| services/jobs.py | 72 | record.apply(JobState.SUBMITTING, now=self._clock()) |
| services/jobs.py | 77 | record.fail(str(e), now=self._clock()) |
| services/jobs.py | 81 | record.apply(JobState.UNCERTAIN, now=self._clock()) |
| services/jobs.py | 86 | record.apply(JobState.UNCERTAIN, now=self._clock()) |
| services/jobs.py | 93 | record.apply(JobState.QUEUED, now=self._clock()) |
| services/jobs.py | 162 | record.apply(target, now=self._clock()) |
| services/jobs.py | 173 | record.apply(JobState.FINALIZING, now=self._clock()) |
| services/jobs.py | 180 | record.succeed(now=self._clock()) |
| services/jobs.py | 187 | record.fail(message, now=self._clock()) |
| services/jobs.py | 195 | record.cancel_confirmed(now=self._clock()) |
| services/jobs.py | 210 | record.cancel_confirmed(now=self._clock())   # a queued job is removed at once |
| services/media.py | 410 | record.succeed(now=self._clock()) |

## Черновики и Библиотека: 26

| файл | строка | код |
| --- | --- | --- |
| server.py | 528 | self.pending = {}  # item id -> item; memory only, so a restart forgets them |
| server.py | 1412 | return self.library.get(item_id) or self.pending.get(item_id) |
| server.py | 1417 | self.pending[item["id"]] = item |
| server.py | 1418 | while len(self.pending) > PREVIEW_LIMIT: |
| server.py | 1419 | self.pending.pop(next(iter(self.pending))) |
| server.py | 2228 | items = [self.public(x) for x in (*self.library.items, *self.pending.values()) if x.get("prompt_id") == pid] |
| server.py | 2473 | self.library.add(item) |
| server.py | 4062 | self.library.add(item) |
| server.py | 4508 | item = self.pending.get(iid) |
| server.py | 4510 | known = self.library.get(iid) |
| server.py | 4514 | if self.library.get(iid) is not None:   # the list was written, the draft was not dropped yet |
| server.py | 4515 | self.pending.pop(iid, None) |
| server.py | 4516 | return web.json_response({"item": self.public(self.library.get(iid)), "kept": True}) |
| server.py | 4524 | self.pending.pop(iid, None) |
| server.py | 4528 | item = self.library.get(req.match_info["id"]) |
| server.py | 4534 | self.library.save(item) |
| server.py | 4538 | item = self.library.get(req.match_info["id"]) |
| server.py | 4557 | self.library.remove(item["id"]) |
| server.py | 4565 | items = [x for x in (self.library.get(i) for i in ids) if x] |
| server.py | 4603 | self.library.remove(i) |
| server.py | 4630 | item = self.library.get(req.match_info["id"]) |
| services/media.py | 399 | self.library.repo.add(item)   # the row and the file are both there, or neither is shown |
| services/media.py | 524 | existing = self.library.get(item["id"]) |
| services/media.py | 527 | self.library.add(item)          # the row is written first: it may raise, the draft is kept |
| services/media.py | 538 | self.library.save(item)     # one changed row: the path of the copy |
| services/media.py | 556 | if library or self.library.get(item["id"]) is not None: |

## Короткая память об исходах: 5

| файл | строка | код |
| --- | --- | --- |
| server.py | 1362 | self.remember(payload) |
| server.py | 1376 | self.recent[pid] = {"state": kind, "at": datetime.now().isoformat(timespec="seconds"), |
| server.py | 1385 | self.history[job["prompt_id"]] = {"meta": job.get("meta") or {}, "created": job.get("t0")} |
| server.py | 2162 | self.recent[pid] = {"state": "error", "at": datetime.now().isoformat(timespec="seconds"), |
| server.py | 2238 | r = self.recent[pid] |

## Фоновые задачи: 49

| файл | строка | код |
| --- | --- | --- |
| server.py | 609 | self.hw = await asyncio.to_thread(engine.detect_hardware) |
| server.py | 614 | await asyncio.to_thread(self.llm.stop) |
| server.py | 689 | self.packs_task = self.tasks.spawn("install-nodes", self.install_missing_packs()) |
| server.py | 692 | self.ws_task = self.tasks.spawn("comfy-ws", self.comfy_ws_loop()) |
| server.py | 693 | self.guard_task = self.tasks.spawn("gpu-watch", self.guard_loop()) |
| server.py | 694 | self.watchdog_task = self.tasks.spawn("lost-watch", self.jobs_watchdog())  # jobs ComfyUI has lost, not a hung card |
| server.py | 780 | await asyncio.to_thread(self.llm.stop) |
| server.py | 794 | worker = asyncio.ensure_future(asyncio.to_thread(func, *args)) |
| server.py | 959 | self.hw = await asyncio.to_thread(engine.detect_hardware) |
| server.py | 1005 | gpu = await asyncio.to_thread(self.monitor.gpu) |
| server.py | 1481 | raw = await asyncio.to_thread(path.read_bytes) |
| server.py | 1636 | self.dl_task = self.tasks.spawn("models", run()) |
| server.py | 1662 | self.hw = await asyncio.to_thread(engine.detect_hardware) |
| server.py | 1765 | data = await asyncio.to_thread(self.monitor.sample, where) |
| server.py | 1784 | hw = await asyncio.to_thread(engine.detect_hardware, install_dir) |
| server.py | 1884 | hw = await asyncio.to_thread(engine.detect_hardware, install_dir) |
| server.py | 1895 | self.installer.task = self.tasks.spawn("install", self.run_install(install_dir, models_dir)) |
| server.py | 1958 | hw = await asyncio.to_thread(engine.detect_hardware, self.config.get("install_dir")) |
| server.py | 1969 | text = await asyncio.to_thread(report.build, { |
| server.py | 2111 | self.tasks.spawn(f"cloud:{pid}", self.poll_cloud(rh, pid), replace=True) |
| server.py | 2316 | return web.json_response({"have": await asyncio.to_thread(clipboard_has_picture)}) |
| server.py | 2324 | if await asyncio.to_thread(clipboard_set_picture, data): |
| server.py | 2327 | picture = await asyncio.to_thread(clipboard_picture) |
| server.py | 2418 | self.tasks.spawn(f"cloud:{pid}", self.poll_cloud(rh, pid), replace=True) |
| server.py | 2529 | gpu = await asyncio.to_thread(self.monitor.gpu) |
| server.py | 3316 | dl = asyncio.create_task(civitai_client.download_file_stream(self.session, url, str(dest), token)) |
| server.py | 3341 | self.lora_dl_task = self.tasks.spawn("lora", work()) |
| server.py | 3549 | return web.json_response(await asyncio.to_thread(work)) |
| server.py | 3693 | return await asyncio.to_thread(p.read_bytes) |
| server.py | 3878 | self.ffmpeg_dl_task = self.tasks.spawn("ffmpeg-dl", work()) |
| server.py | 3906 | res = await asyncio.to_thread(ffmpeg_mgr.check_ffmpeg_update, st.get("version")) |
| server.py | 4022 | task = self.tasks.spawn(f"ffmpeg:{pid}", |
| server.py | 4047 | info = await asyncio.to_thread(ffmpeg_mgr.probe_media, st["ffprobe_path"], str(dest)) |
| server.py | 4344 | self.tasks.spawn(f"cancel:{pid}", self.interrupt_watch(pid), replace=True) |
| server.py | 4366 | self.tasks.spawn(f"cancel:{pid}", self.interrupt_watch(pid), replace=True) |
| server.py | 4553 | gone = set(await asyncio.to_thread(send_to_recycle_bin, todo)) if todo else set() |
| server.py | 4702 | _, _, found = await asyncio.to_thread(ag.find_gguf, custom["dir"]) |
| server.py | 4766 | self.tasks.spawn("agent-install", run()) |
| server.py | 4784 | found = await asyncio.to_thread(speech.locate, self.llm.root, folder) |
| server.py | 4838 | self.hw = await asyncio.to_thread(engine.detect_hardware) |
| server.py | 4839 | model, proj, models = await asyncio.to_thread(ag.find_gguf, folder, body.get("model"), self.hw.get("vram_gb")) |
| server.py | 5011 | sid = await asyncio.to_thread(self.skills.save_upload, files) |
| agentapi.py | 319 | gpu = await asyncio.to_thread(app.monitor.gpu) |
| services/engines.py | 89 | await asyncio.to_thread(process.stop)   # a worker thread: terminate + wait 15 s + taskkill |
| services/engines.py | 109 | await asyncio.to_thread(self.process.restart) |
| services/engines.py | 113 | self.external = await asyncio.to_thread( |
| services/media.py | 205 | await asyncio.to_thread(_fsync, part) |
| services/media.py | 207 | await asyncio.to_thread(_replace, part, dest) |
| services/media.py | 225 | worker = asyncio.ensure_future(asyncio.to_thread( |

## Запись на диск: 24

| файл | строка | код |
| --- | --- | --- |
| server.py | 247 | path.write_text(CLIP_HELPER, encoding="utf-8-sig") |
| server.py | 291 | path.write_bytes(data) |
| server.py | 1356 | atomic_write_json(d / f"state_{lg['part']}{sfx}.json", |
| server.py | 1980 | path.write_text(text, encoding="utf-8") |
| server.py | 2273 | tmp.write_bytes(data) |
| server.py | 2618 | atomic_write_json(self.wf_path(wid), {"name": name, "graph": graph}) |
| server.py | 3484 | (d / (key + ext)).write_bytes(raw) |
| server.py | 3495 | atomic_write_json(d / "project.json", body["project"]) |
| server.py | 3962 | p.write_bytes(files[k][1]) |
| server.py | 4003 | music.write_bytes(files["music"][1]) |
| server.py | 4015 | probe.write_bytes(b"") |
| server.py | 4993 | atomic_write_json(p, data) |
| agentapi.py | 189 | atomic_write_json(self._meta_path(asset_id), meta) |
| agentapi.py | 222 | atomic_write_json(path, data, indent=1) |
| engine.py | 62 | atomic_write_json(self.path, data, indent=1) |
| engine.py | 358 | (comfy_root / "ComfyUI" / "extra_model_paths.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8") |
| ffmpeg_mgr.py | 1128 | concat_txt.write_text("".join(_concat_line(clip) + "\n" for clip in clips), encoding="utf-8") |
| ffmpeg_mgr.py | 1530 | script_path.write_text(filter_complex, encoding="utf-8") |
| agent.py | 177 | target.write_bytes(data) |
| storage/atomic.py | 69 | def atomic_write_bytes(path, data, *, fsync=True, retries=5, retry_delay=0.02): |
| storage/atomic.py | 93 | def atomic_write_text(path, text, *, encoding="utf-8", fsync=True, retries=5, retry_delay=0.02): |
| storage/atomic.py | 95 | return atomic_write_bytes(path, text.encode(encoding), fsync=fsync, retries=retries, retry_delay=retry_delay) |
| storage/atomic.py | 98 | def atomic_write_json(path, data, *, indent=None, ensure_ascii=False, sort_keys=False, fsync=True): |
| storage/atomic.py | 101 | return atomic_write_text(path, text, fsync=fsync) |

