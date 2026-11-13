"""The queue and the job lifecycle as the window sees it: the saved queue, resuming after a
restart, the job details, stopping, the watchdog of stuck jobs and cloud jobs.

Part of the window's server: the methods below are mixed into `application.App` (see server.py).
Each one is an aiohttp handler or an assistant used only by them; the routes themselves are listed
in one place, `server.build_app`.
"""

from webcommon import (
    CLOUD_RETRY, DATA_DIR, STORE_ERRORS
)

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import web

import cloud
import jobmeta
import workflows as wf
from backends.base import BackendError, BackendUnavailable
from domain.errors import JobConflict
from domain.jobs import JobState, public_state
from storage.repositories import JobRepository


class JobRoutes:
    def load_resume_queue(self):
        """Jobs the database holds but this run has not started: what the window asks about at start.

        The queue never lived longer than a week (the JSON version dropped older records), so an older
        record is forgotten here as well instead of being offered forever.

        A cloud job is not in this list: the window closing never cancelled it on RunningHub, so it is
        watched again at start (`watch_cloud_jobs`) instead of being asked about — and it is never
        re-sent, which would render (and pay for) the same clip twice.
        """
        week_ago = datetime.now().timestamp() - 7 * 86400
        out = []
        self.resume_cloud = []
        self.resume_local = []
        for record in self.jobs_repo.active():
            if record.backend == "ffmpeg":
                self.resume_local.append(record)
                continue
            if record.created_at and record.created_at < week_ago:
                self.jobs_repo.delete(record.job_id)
                continue
            if record.backend == "cloud" and record.backend_job_id:
                self.resume_cloud.append(record)
                continue
            out.append(record)
        return out

    def persisted_job(self, job):
        """The record of a live job, with the fields the window and the engine still keep in the dict.

        `legacy` is the transitional part: the queue-record fields of the older version (stages, meta,
        titles, preview). Stage 4 folds them into the job service; until then they travel with the
        record, so a restart can resume a job exactly instead of guessing its parameters back.
        """
        record = job.get("record")
        if record is None:
            return None
        if record.graph is None:
            record.graph = job.get("graph")
        record.legacy = {**record.legacy, "stages": job.get("stages") or {}, "meta": job.get("meta") or {},
                         "titles": job.get("titles") or {}, "last_output": bool(job.get("last_output")),
                         "preview": bool(job.get("preview"))}
        return record

    def save_queue(self):
        """Persist the live jobs and keep the outcomes. Called on every change of the job list.

        Update known live records; never infer deletion from this process's UI cache. Submission,
        recovery and CPU jobs also live in the database without an entry in self.jobs. Terminal
        outcomes are pruned by age and count, independently of saved drafts.
        """
        try:
            with self.db.transaction():
                for job in list(self.jobs.values()):
                    record = self.persisted_job(job)
                    if record is None:
                        continue
                    self.jobs_repo.save(record)
                # Absence from the UI cache says nothing about a durable submission. In
                # particular submitting/uncertain jobs have not been attached to that cache.
                self.jobs_repo.prune_outcomes()
            self.queue_save_error = None
        except STORE_ERRORS as e:
            self.queue_save_error = str(e)
            self.note_error(f"Не удалось сохранить очередь заданий: {e}")

    async def h_queue_saved(self, _req):
        items = [{"mode": (record.legacy.get("meta") or {}).get("mode"),
                  "prompt": ((record.legacy.get("meta") or {}).get("prompt") or "")[:80]}
                 for record in self.pending_resume]
        return web.json_response({"count": len(items), "items": items})

    def job_from_record(self, record):
        """The job dict the window, the watch and the engine paths have always used, rebuilt from the
        record. `finalizing` is not restored: whoever resumes the job decides that."""
        legacy = record.legacy or {}
        pid = record.prompt_id or record.backend_job_id
        now = datetime.now().timestamp()
        return {"prompt_id": pid, "state": public_state(record.state), "stages": legacy.get("stages") or {},
                "stage": None, "meta": legacy.get("meta") or {}, "last_output": legacy.get("last_output", False),
                "titles": legacy.get("titles") or {}, "graph": record.graph, "scenes": None,
                "t0": record.created_at or now, "last_ev": now, "preview": legacy.get("preview", False),
                # a cloud job is watched by its task id; it is never sent to ComfyUI again
                "task": record.backend_job_id if record.backend == "cloud" else None,
                "record": record}

    def watch_cloud_jobs(self):
        """Watch the cloud jobs that were still running when the window closed.

        Closing the program never cancelled them on RunningHub: the task id is in the record, and the
        new run attaches to it instead of asking ComfyUI (which never had it) or sending it again
        (which would render — and pay for — the same clip twice). With the cloud switched off the
        records are kept for the next start, not lost.
        """
        records, self.resume_cloud = list(getattr(self, "resume_cloud", ())), []
        if not records:
            return
        rh = self.rh()
        if rh is None:
            self.resume_cloud = records
            self.note_error(f"Облачных заданий ждёт облако: {len(records)} (включите облако, чтобы следить за ними)")
            return
        for record in records:
            job = self.job_from_record(record)
            pid = job["prompt_id"]
            if not pid or pid in self.jobs:
                continue
            self.jobs[pid] = job
            self.try_set_job_state(job, JobState.QUEUED)   # the first status of the task moves it on
            self.tasks.spawn(f"cloud:{pid}", self.poll_cloud(rh, pid), replace=True)

    async def restore_local_results(self):
        """A completed CPU render needs registration, never a new ComfyUI submission."""
        for record in list(self.resume_local):
            item = record.legacy.get("local_item")
            if record.state is JobState.FINALIZING and item:
                try:
                    if not Path(item["file"]["path"]).is_file():
                        raise FileNotFoundError(item["file"]["path"])
                    await self.media.publish_local(self.job_from_record(record), item, notify=self.broadcast)
                except Exception as e:
                    self.note_error(f"Монтаж {record.prompt_id} ожидает регистрации: {e}")
                    continue
            else:
                record.fail("Монтаж был прерван при закрытии приложения", now=datetime.now().timestamp())
                self.jobs_repo.save(record)
            self.resume_local.remove(record)

    async def _history_done(self, pid):
        """What ComfyUI's history says about a job of the previous run: finished, failed or unknown."""
        try:
            _, hist = await self.comfy_json("GET", f"/history/{pid}", timeout=aiohttp.ClientTimeout(total=10))
        except Exception:
            return "unknown"
        entry = (hist or {}).get(pid)
        if not entry:
            return "unknown"
        if entry.get("status", {}).get("status_str") == "error":
            return "failed"
        return "finished" if entry.get("outputs") else "unknown"

    async def _resume_one(self, job, running, pending, backend_ok, out):
        """Resume one job of an earlier run; True when it may leave the waiting list, False to keep it."""
        meta = job.get("meta") or {}
        mode, prompt = meta.get("mode"), (meta.get("prompt") or "")[:80]
        project = str(meta.get("project") or "")
        pid = job.get("prompt_id")

        def note(state, prompt_id=pid, **extra):
            """One row of the answer: the window puts it back into the queue with its project."""
            out.append({"prompt_id": prompt_id, "mode": mode, "prompt": prompt,
                        "state": state, "project": project, **extra})
        record = job.get("record")
        if record is not None and record.state in (JobState.CREATED, JobState.SUBMITTING, JobState.UNCERTAIN):
            note("kept", reason="Исход отправки неизвестен: проверьте очередь движка перед повторным запуском")
            return False
        if record is not None and record.backend_id and record.backend_id != self.backend_identity():
            # the settings now point at another engine: this job belongs to the one that had it, and
            # sending it to a different server would generate a different clip behind the user's back
            self.note_error(f"Задание {pid} ждёт прежний движок ({record.backend_id}), а сейчас выбран другой")
            note("kept", reason="задание принадлежит другому движку")
            return False
        live = ("running" if pid in running else "queued" if pid in pending else None) if pid else None
        if live:  # ComfyUI still has it (or is running it): watch it instead of sending it twice
            if pid not in self.jobs:
                self.jobs[pid] = job
                self.set_job_state(job, JobState.RUNNING if live == "running" else JobState.QUEUED)
            note(live)
            return True
        if not backend_ok:
            return False  # an engine that does not answer has not lost anything: keep the record as it is
        if pid:
            done = await self._history_done(pid)
            if done == "finished":  # it finished while the window was closed: collect the result, do not render again
                self.jobs[pid] = job
                await self.finish_job(job)
                if not job.get("finalized"):
                    # the result could not be registered (e.g. the disk refused the Library write):
                    # the record stays in the waiting list, which is what the next start retries from
                    self.jobs.pop(pid, None)
                    note("queued")
                    return False
                note("done")
                return True
            if done == "failed":
                message = "ComfyUI закончил это задание с ошибкой, пока окно было закрыто"
                self.try_set_job_state(job, JobState.FAILED, error=message)
                if record is not None:
                    self.jobs_repo.note_message(pid, message)
                note("error")
                return True
        resp = await self.queue(job["graph"], job.get("stages") or {}, meta, job.get("last_output", False),
                                job.get("titles"), job.get("preview", False))
        body = json.loads(resp.text)
        if resp.status != 200 or not body.get("prompt_id"):
            raise RuntimeError(body.get("error") or f"ComfyUI отклонил задание ({resp.status})")
        note("queued", prompt_id=body["prompt_id"])
        return True

    async def h_queue_resume(self, _req):
        """Bring back the unfinished jobs of an earlier run, each one on its own.

        A job leaves the waiting list only after it was really resumed (re-attached to a live job,
        taken from the history, or submitted again), so one broken record or an engine that is not up
        yet cannot lose the rest. A ComfyUI that does not answer is not an empty queue and its jobs
        are never sent to it blindly.
        """
        items = list(self.pending_resume)
        if not items:
            return web.json_response({"jobs": [], "restored": 0, "kept": 0, "unreachable": False})
        running, pending, backend_ok = set(), set(), True
        try:
            _, q = await self.comfy_json("GET", "/queue")
            for key, bucket in (("queue_running", running), ("queue_pending", pending)):
                bucket.update(str(x[1]) for x in (q or {}).get(key, []) if len(x) > 1)
        except Exception as e:
            backend_ok = False
            self.note_error(f"Очередь прошлого запуска ждёт ComfyUI: он не отвечает ({type(e).__name__})")
        out, kept = [], 0
        for record in items:
            job = self.job_from_record(record)
            try:
                resumed = await self._resume_one(job, running, pending, backend_ok, out)
            except Exception as e:
                self.note_error(f"Не удалось восстановить задание из прошлой очереди: {e}")
                resumed = False
            if not resumed:
                kept += 1
                continue
            self.pending_resume = [r for r in self.pending_resume if r.job_id != record.job_id]
            if not record.state.terminal and not any(
                    j.get("record") is record for j in self.jobs.values()):
                # Explicit successful resubmission replaces this old queue entry. It is not
                # inferred from the absence of an entry in memory by save_queue.
                self.jobs_repo.delete(record.job_id)
            self.save_queue()   # after every job: a crash in the middle must not submit anything twice
        return web.json_response({
            "jobs": out, "restored": len(out), "kept": kept, "unreachable": not backend_ok,
            "error": None if backend_ok else "ComfyUI не отвечает: очередь сохранена и будет восстановлена позже"})

    async def h_queue_discard(self, _req):
        for record in list(self.pending_resume):
            self.jobs_repo.delete(record.job_id)
        self.pending_resume = []
        return web.json_response({"ok": True})

    async def h_job(self, req):
        """Everything about one job for the details window: what it is, what it was made from, how it ended."""
        pid = req.match_info["pid"]
        out = {"prompt_id": pid, "state": "unknown", "fps": wf.FPS}
        job = self.jobs.get(pid)
        if job:
            record = job.get("record")
            out.update(state=job.get("state") or "queued", stage=job.get("stage"), step=job.get("step"),
                       max=job.get("step_max"), created=job.get("t0"), meta=jobmeta.for_ui(job.get("meta")),
                       finalizing=bool(job.get("finalizing")),
                       phase=record.state.value if record is not None else None)
            return web.json_response(out)
        items = [self.public(x) for x in (*self.library.items, *self.pending.values()) if x.get("prompt_id") == pid]
        if items:   # finished: the Library record keeps the job's own meta (prompt, format, inputs)
            drop = {"file", "url", "local", "fav", "id", "kind", "ext", "prompt_id", "date", "missing"}
            out.update(state="done", meta=jobmeta.for_ui({k: v for k, v in items[0].items() if k not in drop}),
                       created=items[0].get("date"), items=items)
            return web.json_response(out)
        # the record of a job that has left the queue: it stays in the database as the outcome, so
        # the details window answers the same before and after a restart (no second memory of it)
        outcome = self.job_outcome(pid)
        if outcome is not None:
            out.update(state=outcome["state"], meta=jobmeta.for_ui(outcome["meta"]),
                       created=outcome["created"], phase=outcome["phase"], message=outcome["message"])
            return web.json_response(out)
        # a job of an earlier run waits for «восстановить»: it is queued, not an outcome
        waiting = next((record for record in self.pending_resume
                        if (record.prompt_id or record.backend_job_id) == pid), None)
        if waiting is not None:
            out.update(state="queued", meta=jobmeta.for_ui((waiting.legacy or {}).get("meta")),
                       created=waiting.created_at, phase=waiting.state.value)
            return web.json_response(out)
        raise web.HTTPNotFound()

    async def queue_cloud(self, rh, graph, stages, meta, preview=False, spec=None):
        meta = self.submission_meta(meta)
        if not self.config.get("rh_workflow_id"):
            return web.json_response({"error": "Укажите ID любого вашего workflow на RunningHub (раздел «Настройки» → «Облако»)"},
                                     status=400)
        if getattr(self, "rh_models", None) is None:
            try:
                self.rh_models = await rh.model_names(self.config.get("rh_workflow_id"))
            except cloud.CloudError:
                self.rh_models = set()
        cloud.remap_models(graph, self.rh_models)
        record = self.begin_submission(spec, graph, stages, dict(meta, cloud=True), preview,
                                       backend="cloud", backend_id="cloud:runninghub")
        try:
            task = await rh.create(graph, self.config.get("rh_workflow_id"), self.config.get("rh_instance", "default"))
        except cloud.CloudError as e:
            record.apply(JobState.UNCERTAIN, now=datetime.now().timestamp())
            self.jobs_repo.save(record)
            return web.json_response({"error": str(e)}, status=400)
        except BaseException:
            record.apply(JobState.UNCERTAIN, now=datetime.now().timestamp())
            self.jobs_repo.save(record)
            raise
        pid = "rh-" + task
        record.prompt_id, record.backend_job_id = pid, task
        record.apply(JobState.QUEUED, now=datetime.now().timestamp())
        self.jobs_repo.save(record)
        self.jobs[pid] = {"prompt_id": pid, "state": "queued", "stages": {}, "stage": None,
                          "meta": dict(meta, cloud=True), "task": task, "t0": datetime.now().timestamp(),
                          "preview": preview, "record": record}
        self.set_job_state(self.jobs[pid], JobState.QUEUED)
        self.tasks.spawn(f"cloud:{pid}", self.poll_cloud(rh, pid), replace=True)
        return web.json_response({"prompt_id": pid, "seed": meta["seed"]})

    async def poll_cloud(self, rh, pid):
        job = self.jobs.get(pid)
        last = None
        misses = 0   # consecutive "could not ask": a network blink is not a failed remote job
        try:
            while pid in self.jobs:
                try:
                    status = await rh.status(job["task"])
                except cloud.CloudError:
                    misses += 1
                    if misses < 5:
                        await asyncio.sleep(CLOUD_RETRY)
                        continue
                    raise
                misses = 0
                if status is None:   # not known there any more (expired, or another key): say why
                    await rh.outputs(job["task"])   # raises with the provider's own reason
                    raise cloud.CloudError("RunningHub не знает это задание")
                if status != last:
                    last = status
                    stage = {"QUEUED": "cloud_queued", "RUNNING": "cloud_run"}.get(status)
                    if stage:
                        await self.send_stage(job, stage)
                    target = {"QUEUED": JobState.QUEUED, "RUNNING": JobState.RUNNING}.get(status)
                    if target is not None:
                        self.try_set_job_state(job, target)
                if status == "SUCCESS":
                    outs = await rh.outputs(job["task"]) or []
                    video = next((o for o in outs if str(o.get("fileType", "")).lower() in ("mp4", "webm", "mov", "mkv")
                                  or str(o.get("fileUrl", "")).lower().split("?")[0].endswith((".mp4", ".webm", ".mov"))), None)
                    if not video:
                        raise cloud.CloudError("RunningHub не вернул видео")
                    self.try_set_job_state(job, JobState.FINALIZING)
                    self.save_queue()
                    dest = DATA_DIR / "cloud" / f'{pid}.mp4'
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    # the clip is streamed through a `.part` and published only when complete: a
                    # broken cloud download must not leave a finished-looking video on the disk
                    try:
                        if not dest.exists() or not dest.stat().st_size:
                            async with self.session.get(video["fileUrl"]) as r:
                                if r.status != 200:
                                    raise cloud.CloudError(f"RunningHub не отдал видео: HTTP {r.status}")
                                await self.media.files.save_stream(dest, r.content.iter_chunked(1 << 20),
                                                                   expected_bytes=r.content_length)
                        item = dict(job["meta"], prompt_id=pid,
                                date=datetime.now().isoformat(timespec="seconds"), fav=False,
                                file={"type": "local", "path": str(dest), "filename": dest.name})
                        if video.get("consumeCoins"):
                            item["sub"] = f'{item.get("sub", "")} · ☁ {video["consumeCoins"]} RH'.strip(" ·")
                        await self.media.publish_local(job, item, notify=self.broadcast)
                    except Exception as e:
                        # The remote render succeeded. Retry only collection/registration, never
                        # the paid generation, and keep the durable task id through a restart.
                        self.note_error(f"Облачный результат {pid} ожидает сохранения: {e}")
                        await asyncio.sleep(CLOUD_RETRY)
                        continue
                    self.try_set_job_state(job, JobState.SUCCEEDED)
                    self.jobs.pop(pid, None)
                    return
                if status == "FAILED":
                    await rh.outputs(job["task"])  # raises with the failure reason
                    raise cloud.CloudError("Задание на RunningHub завершилось ошибкой")
                if pid not in self.jobs:
                    return   # it was finished, cancelled or dropped while we were asking
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise   # the window is closing: watching stops, the remote job itself is not cancelled
        except Exception as e:  # noqa: BLE001 - the cloud path reports its own errors to the window
            if pid in self.jobs:
                job = self.jobs.get(pid)
                self.try_set_job_state(job, JobState.FAILED, error=str(e))
                self.jobs.pop(pid, None)
                await self.broadcast({"type": "error", "prompt_id": pid, "message": str(e)})

    async def h_interrupt(self, req):
        body = await req.json()
        pid = body.get("prompt_id")
        if pid:
            await self.cancel_job(pid)
        else:
            await self.stop_current()   # «остановить текущее» — отдельная явная операция, не побочный эффект
        return web.json_response({"ok": True})

    async def cancel_job(self, pid):
        """Stop one job: a cloud task, a waiting one, or the running one (ComfyUI has one running prompt at a time).

        An id this program does not know is not a reason to interrupt whatever the engine happens to be
        running: the window's «stop the current thing» is its own operation (`stop_current`).

        The outcome is data, not an HTTP response: the window shows a notice and the agent API words the
        same fact in its own dialect (`finished`/`unknown`/`canceled`/`canceling`/`unconfirmed`/`refused`).
        """
        job = self.jobs.get(pid) if pid else None
        if job and job.get("finalizing"):
            # the generation is already over and its result is being written: interrupting the engine
            # now would throw away a clip that exists on disk
            text = "Генерация уже закончилась: результат сохраняется."
            await self.broadcast({"type": "notice", "text": text})
            return {"state": "finished", "message": text}
        if not job:
            await self.broadcast({"type": "notice", "text": "Этого задания уже нет в очереди: останавливать нечего."})
            return {"state": "unknown", "message": "Этого задания уже нет в очереди"}
        record = job.get("record")
        if record is not None and not job.get("task"):
            try:
                accepted = await self.service.request_cancel(record)
            except JobConflict as e:
                await self.broadcast({"type": "notice", "text": e.message})
                return {"state": "refused", "message": e.message}
            except BackendUnavailable:
                text = "ComfyUI не отвечает: отмена записана, но не подтверждена. Задание останется в очереди."
                self.note_error(f"Отмена задания {pid} записана, но не подтверждена: ComfyUI не отвечает")
                await self.broadcast({"type": "notice", "text": text})
                return {"state": "unconfirmed", "message": text}
            except BackendError as e:
                text = f"Остановить не получилось: {e}"
                await self.broadcast({"type": "notice", "text": text})
                return {"state": "refused", "message": text}
            if record.state is JobState.CANCELED:   # a queued job is removed at once
                self.journal.event("cancel", prompt_id=pid, job_id=record.job_id, result="canceled")
                self.jobs.pop(pid, None)
                await self.broadcast({"type": "interrupted", "prompt_id": pid})
                return {"state": "canceled", "message": None}
            if accepted:
                # running: the engine accepted the request, the outcome comes from its event or the watch
                self.tasks.spawn(f"cancel:{pid}", self.interrupt_watch(pid), replace=True)
            self.journal.event("cancel", prompt_id=pid, job_id=record.job_id, result="canceling")
            return {"state": "canceling", "message": None}
        # cloud tasks and jobs of an older run have no record: their own paths stay as they were
        if job.get("task"):
            self.try_set_job_state(job, JobState.CANCELED)
            self.jobs.pop(pid, None)
            rh = self.rh()
            if rh:
                try:
                    await rh.cancel(job["task"])
                except cloud.CloudError:
                    pass
            await self.broadcast({"type": "interrupted", "prompt_id": pid})
            return {"state": "canceled", "message": None}
        if job["state"] == "queued":
            await self.comfy_json("POST", "/queue", json={"delete": [pid]})
            self.try_set_job_state(job, JobState.CANCELED)
            self.jobs.pop(pid, None)
            await self.broadcast({"type": "interrupted", "prompt_id": pid})
            return {"state": "canceled", "message": None}
        await self.comfy_json("POST", "/interrupt", json={"prompt_id": pid})
        self.tasks.spawn(f"cancel:{pid}", self.interrupt_watch(pid), replace=True)
        return {"state": "canceling", "message": None}

    async def stop_current(self):
        """The window's «Остановить»: stop whatever the engine is running right now, by explicit request."""
        await self.comfy_json("POST", "/interrupt", json={})

    async def interrupt_watch(self, pid, wait=15):
        """ComfyUI sometimes ignores «Cancel» (stuck on a heavy step). If the job is still there after `wait`
        seconds the engine is not answering us any more, so it is replaced: our own process, or the user's
        own ComfyUI started again from the command line it reports (only when that is possible)."""
        await asyncio.sleep(wait)
        if pid not in self.jobs:
            return
        if await self.stopped_after_all(pid):
            return
        text = "ComfyUI не отвечает на «Отмена». Перезапускаю движок, это займёт около минуты."
        self.note_error(text)
        await self.broadcast({"type": "notice", "text": text})
        ok, why = await self.restart_engine()
        if not ok:
            if why:
                text = ("ComfyUI не отвечает на «Отмена», и перезапустить его не получилось: " + why +
                        (" Закройте окно ComfyUI и запустите его снова." if self.watchdog_local() else ""))
                self.note_error(text)
                await self.broadcast({"type": "notice", "text": text})
            return
        # the restarted engine has an empty queue: nothing of what was running or waiting is there any more
        await self.drop_engine_jobs(kind="interrupted")

    async def stopped_after_all(self, pid):
        """The engine honoured the cancel a moment after we stopped watching.

        ComfyUI can take the interrupt slowly: by the time we are ready to replace the whole engine the
        prompt may already be gone from its queue. Restarting then would throw away a finished result and
        the other jobs behind it, so ask first — and take the result if the job finished while cancelling.
        """
        try:
            _, q = await self.comfy_json("GET", "/queue", timeout=aiohttp.ClientTimeout(total=5))
            alive = {str(x[1]) for key in ("queue_running", "queue_pending") for x in (q or {}).get(key, []) if len(x) > 1}
        except Exception:
            return False  # cannot ask at all: the engine really is unresponsive, replace it
        if pid in alive:
            return False
        job = self.jobs.get(pid)
        if job:
            try:
                _, hist = await self.comfy_json("GET", f"/history/{pid}", timeout=aiohttp.ClientTimeout(total=10))
            except Exception:
                hist = None
            entry = (hist or {}).get(pid) or {}
            if entry.get("outputs") and entry.get("status", {}).get("status_str") != "error":
                await self.finish_job(job)  # it finished just as we were cancelling: keep the clip
                return True
            self.jobs.pop(pid, None)
            await self.broadcast({"type": "interrupted", "prompt_id": pid})
        return True

    async def jobs_watchdog(self):
        """Kill jobs that ComfyUI has lost: e.g. it was restarted while the app waited for events that will
        never come («Ждёт, пока освободится ComfyUI» forever). A job sitting in ComfyUI's queue is legitimately
        waiting, so only a job ABSENT from the queue and from the history is stale."""
        while True:
            await asyncio.sleep(60)
            try:
                if self.rh() or not self.jobs:
                    continue
                _, q = await self.comfy_json("GET", "/queue", timeout=aiohttp.ClientTimeout(total=10))
                queued_ids = {str(item[1]) for item in (q or {}).get("queue_running", []) + (q or {}).get("queue_pending", [])}
            except Exception:
                continue   # ComfyUI unreachable or restarting: not the watchdog's case
            now = datetime.now().timestamp()  # t0 and last_ev are wall-clock seconds (the details window shows them)
            for pid, job in list(self.jobs.items()):
                if job.get("task"):   # cloud (RunningHub) jobs are watched by their own poller
                    continue
                if job.get("finalizing"):   # the result is being written: this is not a silent generation
                    continue
                age, silent = now - job.get("t0", now), now - job.get("last_ev", job.get("t0", now))
                if pid in queued_ids:
                    if job["state"] == "running" and silent > 600:
                        # ComfyUI still holds the job but has been silent for 10 minutes: a hung node.
                        # (Legit quiet phases like loading a huge model from disk are covered by the threshold.)
                        if self.watchdog_local():   # our own engine or ComfyUI on this computer: replace it
                            text = "Генерация зависла: ComfyUI молчит больше 10 минут. Перезапускаем движок, это займёт около минуты."
                            self.note_error(text)
                            await self.broadcast({"type": "notice", "text": text})
                            # the one restart path of the app: it holds the «restart in progress» flag, so this watch
                            # and «Сторож видеокарты» never restart the engine at the same time
                            ok, why = await self.restart_engine()
                            if ok:
                                self.jobs.pop(pid, None)
                                await self.broadcast({"type": "error", "prompt_id": pid,
                                                      "message": "Генерация зависла в ComfyUI и была снята при перезапуске движка. "
                                                                 "Отправьте её ещё раз."})
                                await self.drop_engine_jobs(None, "interrupted")  # everything else died with the engine
                            elif why:
                                self.note_error(f"Не удалось перезапустить движок после зависания: {why}")
                        else:
                            await self._stale_job(job, "Генерация зависла в вашем ComfyUI (больше 10 минут без ответа). "
                                                       "Закройте окно ComfyUI и запустите его заново, затем отправьте генерацию ещё раз.")
                    continue
                if job["state"] == "queued" and age > 240:
                    _, hist = await self.comfy_json("GET", f"/history/{pid}")
                    if (hist or {}).get(pid):   # it actually finished while nobody watched: collect the result
                        try:
                            await self.finish_job(job)
                        except Exception:
                            self.jobs.pop(pid, None)
                            await self.broadcast({"type": "interrupted", "prompt_id": pid})
                    else:
                        await self._stale_job(job, "ComfyUI это задание не получил (скорее всего, он перезапускался). "
                                                   "Отправьте генерацию ещё раз.")
                elif job["state"] == "running" and silent > 900:
                    _, hist = await self.comfy_json("GET", f"/history/{pid}")
                    if (hist or {}).get(pid):
                        try:
                            await self.finish_job(job)
                        except Exception:
                            self.jobs.pop(pid, None)
                            await self.broadcast({"type": "interrupted", "prompt_id": pid})
                    else:
                        await self._stale_job(job, "Связь с ComfyUI прервалась: задание потеряно. "
                                                   "Отправьте генерацию ещё раз.")

    async def _stale_job(self, job, message):
        pid = job["prompt_id"]
        self.try_set_job_state(job, JobState.FAILED, error=message)
        self.note_error("Задание зависло и снято: " + message)
        await self.broadcast({"type": "error", "prompt_id": pid, "message": message})
        self.jobs.pop(pid, None)
