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

### /ui

| метод | путь | обработчик |
| --- | --- | --- |
| GET | /ui/{path} | h_ui |

## События в окно (broadcast/SSE): 23

- `delta`
- `disable`
- `done`
- `engine`
- `error`
- `external_job`
- `ffmpegdl`
- `hello`
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
| application.py | 457 | self.jobs.pop(pid, None) |
| application.py | 461 | self.jobs.pop(pid, None) |
| application.py | 576 | self.try_set_job_state(self.jobs[jid], JobState.CANCELED if kind == "interrupted" else JobState.FAILED, |
| application.py | 578 | self.jobs.pop(jid, None) |
| application.py | 674 | self.jobs.pop(pid, None) |
| application.py | 678 | self.jobs.pop(pid, None) |
| application.py | 746 | self.jobs[pid] = job |
| application.py | 771 | job["state"] = public_state(target) |
| application.py | 775 | changed = record.apply(target, now=datetime.now().timestamp(), error=error) |
| application.py | 776 | job["state"] = public_state(record.state) |
| application.py | 866 | self.jobs.pop(pid, None)   # there is nothing to wait for any more |
| application.py | 872 | self.jobs.pop(pid, None)   # the result is registered: only now the job leaves the queue |
| application.py | 948 | jobs.append({"prompt_id": pid, "state": job.get("state") or "queued", |
| agentapi.py | 673 | running = job.get("state") == "running" |
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
| routes/jobs.py | 145 | self.jobs[pid] = job |
| routes/jobs.py | 178 | self.jobs[pid] = job |
| routes/jobs.py | 187 | self.jobs[pid] = job |
| routes/jobs.py | 192 | self.jobs.pop(pid, None) |
| routes/jobs.py | 261 | out.update(state=job.get("state") or "queued", stage=job.get("stage"), step=job.get("step"), |
| routes/jobs.py | 303 | self.jobs[pid] = {"prompt_id": pid, "state": "queued", "stages": {}, "stage": None, |
| routes/jobs.py | 306 | self.set_job_state(self.jobs[pid], JobState.QUEUED) |
| routes/jobs.py | 352 | self.jobs.pop(pid, None) |
| routes/jobs.py | 384 | self.jobs.pop(pid, None) |
| routes/jobs.py | 433 | self.jobs.pop(pid, None) |
| routes/jobs.py | 444 | self.jobs.pop(pid, None) |
| routes/jobs.py | 453 | if job["state"] == "queued": |
| routes/jobs.py | 456 | self.jobs.pop(pid, None) |
| routes/jobs.py | 514 | self.jobs.pop(pid, None) |
| routes/jobs.py | 539 | if job["state"] == "running" and silent > 600: |
| routes/jobs.py | 550 | self.jobs.pop(pid, None) |
| routes/jobs.py | 561 | if job["state"] == "queued" and age > 240: |
| routes/jobs.py | 567 | self.jobs.pop(pid, None) |
| routes/jobs.py | 572 | elif job["state"] == "running" and silent > 900: |
| routes/jobs.py | 578 | self.jobs.pop(pid, None) |
| routes/jobs.py | 589 | self.jobs.pop(pid, None) |

## Черновики и Библиотека: 27

| файл | строка | код |
| --- | --- | --- |
| application.py | 94 | self.pending = {}  # item id -> item; memory only, so a restart forgets them |
| application.py | 960 | for item in (*self.library.items, *self.pending.values()): |
| application.py | 988 | return self.library.get(item_id) or self.pending.get(item_id) |
| application.py | 993 | self.pending[item["id"]] = item |
| application.py | 994 | while len(self.pending) > PREVIEW_LIMIT: |
| application.py | 995 | self.pending.pop(next(iter(self.pending))) |
| services/media.py | 399 | self.library.repo.add(item)   # the row and the file are both there, or neither is shown |
| services/media.py | 524 | existing = self.library.get(item["id"]) |
| services/media.py | 527 | self.library.add(item)          # the row is written first: it may raise, the draft is kept |
| services/media.py | 538 | self.library.save(item)     # one changed row: the path of the copy |
| services/media.py | 556 | if library or self.library.get(item["id"]) is not None: |
| routes/jobs.py | 266 | items = [self.public(x) for x in (*self.library.items, *self.pending.values()) if x.get("prompt_id") == pid] |
| routes/jobs.py | 362 | self.library.add(item) |
| routes/library.py | 138 | item = self.pending.get(iid) |
| routes/library.py | 140 | known = self.library.get(iid) |
| routes/library.py | 144 | if self.library.get(iid) is not None:   # the list was written, the draft was not dropped yet |
| routes/library.py | 145 | self.pending.pop(iid, None) |
| routes/library.py | 146 | return web.json_response({"item": self.public(self.library.get(iid)), "kept": True}) |
| routes/library.py | 154 | self.pending.pop(iid, None) |
| routes/library.py | 158 | item = self.library.get(req.match_info["id"]) |
| routes/library.py | 164 | self.library.save(item) |
| routes/library.py | 168 | item = self.library.get(req.match_info["id"]) |
| routes/library.py | 187 | self.library.remove(item["id"]) |
| routes/library.py | 195 | items = [x for x in (self.library.get(i) for i in ids) if x] |
| routes/library.py | 233 | self.library.remove(i) |
| routes/library.py | 260 | item = self.library.get(req.match_info["id"]) |
| routes/montage.py | 388 | self.library.add(item) |

## Короткая память об исходах: 0

| файл | строка | код |
| --- | --- | --- |

## Фоновые задачи: 50

| файл | строка | код |
| --- | --- | --- |
| application.py | 181 | self.hw = await asyncio.to_thread(engine.detect_hardware) |
| application.py | 186 | await asyncio.to_thread(self.llm.stop) |
| application.py | 253 | self.packs_task = self.tasks.spawn("install-nodes", self.install_missing_packs()) |
| application.py | 256 | self.ws_task = self.tasks.spawn("comfy-ws", self.comfy_ws_loop()) |
| application.py | 257 | self.guard_task = self.tasks.spawn("gpu-watch", self.guard_loop()) |
| application.py | 258 | self.watchdog_task = self.tasks.spawn("lost-watch", self.jobs_watchdog())  # jobs ComfyUI has lost, not a hung card |
| application.py | 323 | await asyncio.to_thread(self.llm.stop) |
| application.py | 339 | worker = asyncio.ensure_future(asyncio.to_thread(func, *args)) |
| application.py | 502 | self.hw = await asyncio.to_thread(engine.detect_hardware) |
| application.py | 541 | gpu = await asyncio.to_thread(self.monitor.gpu) |
| application.py | 1055 | raw = await asyncio.to_thread(path.read_bytes) |
| application.py | 1109 | gpu = await asyncio.to_thread(self.monitor.gpu) |
| agentapi.py | 320 | gpu = await asyncio.to_thread(app.monitor.gpu) |
| services/engines.py | 89 | await asyncio.to_thread(process.stop)   # a worker thread: terminate + wait 15 s + taskkill |
| services/engines.py | 109 | await asyncio.to_thread(self.process.restart) |
| services/engines.py | 113 | self.external = await asyncio.to_thread( |
| services/media.py | 205 | await asyncio.to_thread(_fsync, part) |
| services/media.py | 207 | await asyncio.to_thread(_replace, part, dest) |
| services/media.py | 225 | worker = asyncio.ensure_future(asyncio.to_thread( |
| runtime/events.py | 87 | sub.task = self._tasks.spawn(f"events:{self._count}", self._send(sub)) |
| routes/agent.py | 48 | _, _, found = await asyncio.to_thread(ag.find_gguf, custom["dir"]) |
| routes/agent.py | 112 | self.tasks.spawn("agent-install", run()) |
| routes/agent.py | 130 | found = await asyncio.to_thread(speech.locate, self.llm.root, folder) |
| routes/agent.py | 184 | self.hw = await asyncio.to_thread(engine.detect_hardware) |
| routes/agent.py | 185 | model, proj, models = await asyncio.to_thread(ag.find_gguf, folder, body.get("model"), self.hw.get("vram_gb")) |
| routes/agent.py | 355 | sid = await asyncio.to_thread(self.skills.save_upload, files) |
| routes/generate.py | 733 | return web.json_response(await asyncio.to_thread(work)) |
| routes/jobs.py | 147 | self.tasks.spawn(f"cloud:{pid}", self.poll_cloud(rh, pid), replace=True) |
| routes/jobs.py | 307 | self.tasks.spawn(f"cloud:{pid}", self.poll_cloud(rh, pid), replace=True) |
| routes/jobs.py | 438 | self.tasks.spawn(f"cancel:{pid}", self.interrupt_watch(pid), replace=True) |
| routes/jobs.py | 460 | self.tasks.spawn(f"cancel:{pid}", self.interrupt_watch(pid), replace=True) |
| routes/library.py | 57 | return await asyncio.to_thread(p.read_bytes) |
| routes/library.py | 183 | gone = set(await asyncio.to_thread(send_to_recycle_bin, todo)) if todo else set() |
| routes/lora.py | 240 | dl = asyncio.create_task(civitai_client.download_file_stream(self.session, url, str(dest), token)) |
| routes/lora.py | 265 | self.lora_dl_task = self.tasks.spawn("lora", work()) |
| routes/models.py | 141 | self.dl_task = self.tasks.spawn("models", run()) |
| routes/models.py | 167 | self.hw = await asyncio.to_thread(engine.detect_hardware) |
| routes/models.py | 258 | hw = await asyncio.to_thread(engine.detect_hardware, install_dir) |
| routes/models.py | 358 | hw = await asyncio.to_thread(engine.detect_hardware, install_dir) |
| routes/models.py | 369 | self.installer.task = self.tasks.spawn("install", self.run_install(install_dir, models_dir)) |
| routes/montage.py | 204 | self.ffmpeg_dl_task = self.tasks.spawn("ffmpeg-dl", work()) |
| routes/montage.py | 232 | res = await asyncio.to_thread(ffmpeg_mgr.check_ffmpeg_update, st.get("version")) |
| routes/montage.py | 348 | task = self.tasks.spawn(f"ffmpeg:{pid}", |
| routes/montage.py | 373 | info = await asyncio.to_thread(ffmpeg_mgr.probe_media, st["ffprobe_path"], str(dest)) |
| routes/status.py | 130 | data = await asyncio.to_thread(self.monitor.sample, where) |
| routes/status.py | 158 | hw = await asyncio.to_thread(engine.detect_hardware, self.config.get("install_dir")) |
| routes/status.py | 169 | text = await asyncio.to_thread(report.build, { |
| routes/status.py | 240 | return web.json_response({"have": await asyncio.to_thread(clipboard_has_picture)}) |
| routes/status.py | 248 | if await asyncio.to_thread(clipboard_set_picture, data): |
| routes/status.py | 251 | picture = await asyncio.to_thread(clipboard_picture) |

## Запись на диск: 24

| файл | строка | код |
| --- | --- | --- |
| application.py | 894 | atomic_write_json(d / f"state_{lg['part']}{sfx}.json", |
| webcommon.py | 210 | path.write_text(CLIP_HELPER, encoding="utf-8-sig") |
| webcommon.py | 254 | path.write_bytes(data) |
| agentapi.py | 190 | atomic_write_json(self._meta_path(asset_id), meta) |
| agentapi.py | 223 | atomic_write_json(path, data, indent=1) |
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
| routes/agent.py | 337 | atomic_write_json(p, data) |
| routes/generate.py | 69 | atomic_write_json(self.wf_path(wid), {"name": name, "graph": graph}) |
| routes/generate.py | 668 | (d / (key + ext)).write_bytes(raw) |
| routes/generate.py | 679 | atomic_write_json(d / "project.json", body["project"]) |
| routes/library.py | 49 | tmp.write_bytes(data) |
| routes/montage.py | 288 | p.write_bytes(files[k][1]) |
| routes/montage.py | 329 | music.write_bytes(files["music"][1]) |
| routes/montage.py | 341 | probe.write_bytes(b"") |
| routes/status.py | 181 | path.write_text(text, encoding="utf-8") |

