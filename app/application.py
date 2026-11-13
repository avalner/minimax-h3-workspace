"""The application object: lifecycle, the engine, jobs and their records.

The HTTP handlers live in `routes/` and are mixed in; `server.build_app` lists the routes.
Everything here works without aiohttp routes and without the window.
"""

from webcommon import (
    BIN_PREVIEW, BIN_PREVIEW_META, CLIENT_ID, DATA_DIR, ENGINE_STOP_TIMEOUT, JobStore, LONG_DIR,
    Library, ROOT, SHUTDOWN_BUDGET, STATE_DB, STORE_ERRORS, _ssl_context, comfy_error_text
)

import asyncio
import contextlib
import ctypes
import json
import random
import re
import struct
import sys
import threading
import time
import urllib.parse
import uuid
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import web

import agent as ag
import agentapi
import catalog
import cloud
import datadir
import engine
import journal
import monitor
import watchdog
import workflows as wf
from backends.base import BackendUnavailable
from backends.comfy import ComfyBackend
from domain.errors import InvalidTransition, SchemaVersionError
from domain.jobs import JobRecord, JobState, MAX_SECONDS, new_job_id, public_state
from domain.projects import DEFAULT_PROJECT_ID, clean_name
from runtime.events import EventHub
from runtime.tasks import TaskSupervisor
from services.engines import EngineManager
from services.jobs import JobService
from services.media import EmptyResult, MediaService
from storage.atomic import atomic_write_json
from storage.database import Database
from storage.migrations import migrate
from storage.repositories import (
    DraftRepository, IdempotencyRepository, JobRepository, MediaRepository, ProjectRepository,
    ResultRepository, StageRepository
)

from routes.status import StatusRoutes
from routes.models import ModelRoutes
from routes.jobs import JobRoutes
from routes.library import LibraryRoutes
from routes.lora import LoraRoutes
from routes.agent import AgentRoutes
from routes.generate import GenerateRoutes
from routes.montage import MontageRoutes
from routes.projects import ProjectRoutes
from routes.stages import StageRoutes


class App(StatusRoutes, ModelRoutes, JobRoutes, LibraryRoutes, LoraRoutes, AgentRoutes, GenerateRoutes,
          MontageRoutes, ProjectRoutes, StageRoutes):
    def __init__(self, comfy_override=None, port=None):
        self.config = engine.Config(DATA_DIR / "config.json")
        # a share or a synchronised folder is a poor home for the queue, the library and (stage 3) the
        # database: the window says so once instead of letting the user find out the hard way
        self.data_dir_warning = datadir.check_location(DATA_DIR)
        self.comfy_override = comfy_override.rstrip("/") if comfy_override else None
        self.port = port  # the port this window actually listens on: agents find it through agent.json
        self.agents = agentapi.AgentAPI(self, DATA_DIR, MAX_SECONDS)
        self.session = None
        # the state of this folder: the database is opened once, and the older JSON files are imported
        # into it on the first start of this version (see storage.migrations for what and how)
        self.db = Database(STATE_DB)
        self.migration = None
        self.migration_error = None
        try:
            self.migration = migrate(self.db, DATA_DIR)
        except SchemaVersionError:
            self.db.close()
            raise
        except Exception as e:  # noqa: BLE001 - a broken database must not hide the window behind a traceback
            self.migration_error = f"{type(e).__name__}: {e}"
        self.jobs_repo = JobRepository(self.db)
        self.media_repo = MediaRepository(self.db)
        self.results_repo = ResultRepository(self.db)
        self.projects_repo = ProjectRepository(self.db)
        self.stages_repo = StageRepository(self.db)
        self.queue_save_error = None   # the last failure to persist the queue, shown in the window instead of a silent loss
        self.pending_resume = self.load_resume_queue()  # unfinished jobs of an earlier run (asked about at start)
        self.jobs = JobStore(self.save_queue)  # prompt_id -> job dict
        self.library = Library(self.db)
        # finished pictures of the picture sections that the user has not added to the Library yet:
        # they are served and saved to disk like Library items, but stay out of the Library list
        self.drafts_repo = DraftRepository(self.db)
        self.pending = {item["id"]: item for item in self.drafts_repo.load()}
        self.output_dir = None
        self.comfy_online = False
        self.installer = None
        self.setup_log = []  # last setup events, replayed to a reopened window
        self.model_names = {}  # role -> name as ComfyUI lists it
        self.missing = []  # catalog entries ComfyUI does not see
        self.found = set()  # roles whose file this ComfyUI does see
        self.needed = []  # roles the chosen modes need (plus the optional extras of «Модели»)
        self.skills = ag.Skills(ROOT / "skills", DATA_DIR / "skills", self.config)
        self.llm = ag.LlamaServer(self.llm_root(), DATA_DIR / "llama.log")
        self.agent_installer = None
        self.hw = None
        self.rh_models = None  # model paths found in the user's RunningHub workflow
        self.monitor = monitor.Monitor()
        self.recent_errors = []  # last problems, for the report to the author
        # what happened to each job: ids, states, durations and the kind of an error — never prompts,
        # tokens or configuration. The report to the author includes its tail.
        self.journal = journal.Journal(DATA_DIR / "logs" / "journal.jsonl")
        # every background coroutine of the app: one place that keeps the reference, sees the error
        # and can stop it (a `create_task` in a handler is fire and forget)
        self.tasks = TaskSupervisor(note_error=self.note_error)
        # the open windows: events are queued per window, so one slow window cannot hold the work,
        # and the fact of an outcome is written down whether or not any window is open
        self.events = EventHub(tasks=self.tasks, remember=self.remember, note_error=self.note_error)
        self._stopping = False   # set first in the shutdown: no new work, no new tasks
        self.last_shutdown = None  # what the shutdown could not finish in its budget, for the report
        self.guard = watchdog.GpuWatchdog()  # the video card watch: slow render or hung engine
        self.guard_task = None
        self.watchdog_task = None  # the lost-job watch: a job ComfyUI no longer knows about
        self.guard_gpu = None    # the last reading the watch took
        self.stats = None        # last /system_stats: it carries the command line of the user's own ComfyUI
        self.ext_log = DATA_DIR / "comfyui-external.log"
        # who owns ComfyUI: the process, the one restart that may run, and the VRAM handshake with
        # the agent's model. Two restarts at once never start two engines.
        self.engines = EngineManager(
            url=lambda: self.comfy, wait_ready=self.wait_comfy, log_path=self.ext_log,
            external_restart=engine.restart_external, stats=lambda: self.stats,
            stop_llm=self.stop_llm, release_vram=self.release_vram, busy=lambda: bool(self.jobs),
            note_error=self.note_error, clock=time.time)
        self.ffmpeg_dl = None    # the running portable-FFmpeg download: {"cancel": Event, "pct": 0, "text": "", "done": False}
        self.ffmpeg_jobs = {}    # prompt_id -> {"cancel": Event, "task": Task, "dest": Path}
        self.lora_dl = None      # the running LoRA download from Civitai / Hugging Face
        # one submission path for the jobs that have a spec: the service owns the durable submit, the
        # idempotency keys and the state rules; the backend talks to the engine (built last: it reads
        # the engine address, which may point at a process the app manages)
        self.idempotency = IdempotencyRepository(self.db)
        self.service = JobService(self.jobs_repo, self.build_backend(), keys=self.idempotency)
        # the results of the jobs: one finalization for every path that collects them, streaming
        # copies into the results folder, and the drafts that wait for «Добавить в библиотеку»
        self.media = MediaService(
            db=self.db, jobs=self.jobs_repo, media=self.media_repo, results=self.results_repo,
            library=self.library, source=self.service.backend, outputs_of=self.outputs_for_media,
            output_dir=lambda: self.output_dir, results_dir=self.results_dir,
            keep_copies=self.keep_copies, present=self.public, hold_draft=self.note_preview_committed,
            note_error=self.note_error, on_outputs=self.keep_scene_state,
            drafts=self.drafts_repo, draft_dir=lambda: DATA_DIR / "drafts",
            extra_folders=lambda: [DATA_DIR / "cloud", DATA_DIR / "cache"])

    # ---------- projects ----------

    def projects(self):
        """The workspaces to choose from. The default one is created by the migration, so the
        window always has a project to show, even for a user who never made one."""
        return self.projects_repo.all()

    def active_project(self):
        """The project new results are stamped with: the chosen one, else the default.

        A project deleted behind the window's back must not stop generation: an unknown id in the
        settings falls back to the default instead of failing a submit.
        """
        project_id = str(self.config.get("active_project") or "")
        if project_id and self.projects_repo.exists(project_id):
            return project_id
        return DEFAULT_PROJECT_ID

    def set_active_project(self, project_id):
        project_id = str(project_id or "").strip()
        if not project_id or not self.projects_repo.exists(project_id):
            raise KeyError(project_id)
        self.config.update(active_project=project_id)
        return project_id

    def create_project(self, name):
        """A new workspace, selected at once: a project is created to work in it."""
        name = clean_name(name)
        if not name:
            raise ValueError("Название проекта не может быть пустым")
        project = self.projects_repo.create(name, now=time.time())
        self.config.update(active_project=project["id"])
        return project

    def submission_meta(self, meta):
        """A job's meta carries the project it was submitted in — the active one unless the caller
        named another (the agent API may). Stamped at submission, not at completion: a clip that
        finishes after the user switched projects still belongs to the one it was asked in."""
        meta = dict(meta or {})
        meta.setdefault("project", self.active_project())
        return meta

    async def outputs_for_media(self, pid):
        """The engine's outputs, asked through the App every time: the media service must not keep a
        stale copy of `job_outputs` (the engine may be replaced, and the watch polls it)."""
        return await self.job_outputs(pid)

    @contextlib.asynccontextmanager
    async def view_stream(self, path, params):
        """One output file of the engine as a stream: status, declared size and chunks, no whole read.

        The media service owns the destination and the `.part`; this only lets the backend ask for
        the file without knowing how it is stored.
        """
        async with self.session.get(self.comfy + path, params=params) as r:
            yield r.status, r.content_length, r.content.iter_chunked(1 << 20)

    def llm_root(self):
        if self.config.get("llm_dir"):
            return Path(self.config.get("llm_dir"))
        if self.config.get("engine") == "managed" and self.config.get("install_dir"):
            return Path(self.config.get("install_dir")) / "llm"
        return DATA_DIR.parent / "llm"

    async def llm_variant(self):
        if not self.llm.alive():
            self.llm.root = self.llm_root()  # follows the install folder chosen in setup
            self.llm.custom = self.config.get("llm_custom")
        if self.config.get("llm_variant") in ag.LLM_VARIANTS:
            return self.config.get("llm_variant")
        if self.hw is None:
            self.hw = await asyncio.to_thread(engine.detect_hardware)
        return ag.pick_variant(self.hw)

    async def stop_llm(self):
        if self.llm.alive():
            await asyncio.to_thread(self.llm.stop)

    @property
    def comfy(self):
        if self.comfy_override:
            return self.comfy_override
        if self.engines.process:
            return self.engines.process.url
        return self.config.get("comfy_url", "http://127.0.0.1:8188").rstrip("/")

    def comfy_root(self):
        d = self.config.get("install_dir")
        return Path(d) / catalog.COMFY_DIRNAME if d else None

    def choice(self):
        return {**catalog.DEFAULT_CHOICE, **(self.config.get("choice") or {}), **(self.config.get("choice_override") or {})}

    def modes(self):
        modes = list(self.config.get("modes") or catalog.MODE_ROLES)
        if "actor" not in modes and ("refs" in modes or "lipsync" in modes):
            modes.append("actor")  # same models, so set-ups made before this mode existed get it too
        if "swap" not in modes and "refs" in modes:
            modes.append("swap")   # character-swap preset: References models + a 0.16 GB LoRA, fetched by "Докачать"
        return modes + [m for m in getattr(self, "auto_modes", ()) if m not in modes]

    def apply_mirror(self):
        engine.HF_MIRROR = "hf-mirror.com" if self.config.get("hf_mirror") else ""

    def awake_loop(self):
        """While generations are queued or running (and for ten minutes after the last one), Windows is not allowed to go to sleep:
        a long video made scene after scene overnight must not be stopped by the power plan. The screen may still switch off."""
        import ctypes
        import time
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        last_busy = 0.0
        while True:
            if self.jobs:
                last_busy = time.time()
            keep = time.time() - last_busy < 600
            try:
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if keep else 0))
            except Exception:
                return
            time.sleep(15)

    async def start(self, _app):
        if sys.platform == "win32":
            import threading
            threading.Thread(target=self.awake_loop, daemon=True, name="keep-awake").start()
        self.apply_mirror()
        trace = aiohttp.TraceConfig()

        async def _comfy_key(_s, _c, params):
            # key of a rented server: sent only to the ComfyUI address, never to other sites
            key = self.config.get("comfy_key")
            if key and params.url.host and params.url.host == urllib.parse.urlparse(self.comfy).hostname:
                params.headers["X-API-Key"] = key
                params.headers["Authorization"] = f"Bearer {key}"
        trace.on_request_start.append(_comfy_key)
        self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_ssl_context()), trace_configs=[trace])
        asyncio.get_running_loop().run_in_executor(None, self.prune_input_cache)  # the details window copies files it shows
        try:   # `.part` files of a closed run: never a file an active writer owns, never a user's file
            self.media.cleanup()
        except Exception as e:  # noqa: BLE001 - an untidy folder must not stop the program
            self.note_error(f"Уборка недописанных файлов не удалась: {e}")
        if self.config.get("engine") == "managed" and self.config.get("setup_done") and not self.comfy_override:
            if self.missing_node_packs():
                self.packs_task = self.tasks.spawn("install-nodes", self.install_missing_packs())
            else:
                self.start_managed()
        self.ws_task = self.tasks.spawn("comfy-ws", self.comfy_ws_loop())
        self.guard_task = self.tasks.spawn("gpu-watch", self.guard_loop())
        self.watchdog_task = self.tasks.spawn("lost-watch", self.jobs_watchdog())  # jobs ComfyUI has lost, not a hung card
        self.watch_cloud_jobs()   # a remote job survives the window: watch it, never send it again
        self.tasks.spawn("restore-local-results", self.restore_local_results())
        self.agents.publish()

    def prune_input_cache(self, days=30):
        """Drop the copies of job files the details window made: they are only a cache, ComfyUI keeps the originals."""
        folder = DATA_DIR / "cache" / "input"
        if not folder.is_dir():
            return
        edge = datetime.now().timestamp() - days * 86400
        for f in folder.glob("*"):
            try:
                if f.is_file() and f.stat().st_mtime < edge:
                    f.unlink()
            except OSError:
                pass

    async def stop(self, _app):
        """The one shutdown, in the order that keeps the recovery state safe.

        1. stop accepting new work and new background tasks;
        2. tell the installers and downloads to stop (their own cancel flags);
        3. cancel the loops (the engine web socket, the watches, the cloud polls) and wait for them;
        4. wait for the media writers, so a `.part` is never deleted under a live writer;
        5. write the recovery state — before anything is closed, so a budget cut cannot lose the
           queue: a job that was being finalized is already `finalizing` in the database, and the
           next start takes it from the engine's history instead of rendering it again;
        6. close the HTTP session, then the database;
        7. stop the processes this program started (the managed ComfyUI), last: it is the slow part
           and it owns no state that a cut would lose.

        A remote cloud job is *not* cancelled here: it keeps running on RunningHub, and the next start
        watches it again (`watch_cloud_jobs`).
        """
        if self._stopping:
            return
        self._stopping = True
        started = time.monotonic()

        def left(reserve=0.0):
            return max(0.5, SHUTDOWN_BUDGET - (time.monotonic() - started) - reserve)

        self.agents.unpublish()
        self.tasks.close()   # from this moment a handler gets None instead of a task on a closed session
        await self.events.close()   # the windows are told nothing more: their connections are closed
        for installer in (self.installer, getattr(self, "dl_installer", None), self.agent_installer):
            if installer is not None:
                installer.cancelled = True
        if self.ffmpeg_dl:
            self.ffmpeg_dl["cancel"].set()
        for job in self.ffmpeg_jobs.values():
            job["cancel"].set()
        unfinished = await self.tasks.shutdown(timeout=left(4.0))
        writers = await self.media.shutdown(timeout=left())
        self.save_queue()   # the recovery state, while the database is still open
        if self.session is not None and not self.session.closed:
            await self.session.close()
        try:
            self.db.close()
        except Exception:  # noqa: BLE001 - a database that is already closed is not a shutdown failure
            pass
        try:
            await asyncio.wait_for(self.engines.stop(), timeout=ENGINE_STOP_TIMEOUT)
        except asyncio.TimeoutError:
            unfinished.append("engine-stop")   # the adopted process dies with the program, no state is lost
        await asyncio.to_thread(self.llm.stop)
        self.last_shutdown = {"seconds": round(time.monotonic() - started, 2), "unfinished": unfinished,
                              "writers": bool(writers)}
        self.journal.event("shutdown", seconds=self.last_shutdown["seconds"],
                           unfinished=len(unfinished), writers=bool(writers))
        if unfinished:
            self.note_error("При закрытии не успели: " + ", ".join(unfinished)
                            + ". Состояние сохранено, следующий запуск продолжит.")

    async def stop_aware_thread(self, func, *args, cancel=None):
        """Run a worker that answers a cancel event; a cancelled caller waits for the worker to stop.

        `asyncio.to_thread` cannot be interrupted: cancelling the task leaves FFmpeg writing while
        the cleanup removes its output. So the event is set and the worker is awaited — it checks the
        event and exits itself — before the caller is told that the work was cancelled.
        """
        worker = asyncio.ensure_future(asyncio.to_thread(func, *args))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            if cancel is not None:
                cancel.set()
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(asyncio.shield(worker), timeout=15.0)
            raise

    def start_managed(self):
        root = self.comfy_root()
        self.engines.start_managed(root)
        self.output_dir = root / "ComfyUI" / "output"

    async def wait_comfy(self, timeout=300):
        """Wait until ComfyUI answers; raise with its log if the process died."""
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            process = self.engines.process
            external = self.engines.external
            died = (process is not None and not process.alive()) or (
                process is None and external is not None and external.poll() is not None)
            if died:
                tail = self.engines.log_tail(20)
                hint = ("\n\nПохоже, проблема в драйвере видеокарты. Обновите драйвер NVIDIA (nvidia.com/drivers), "
                        "перезагрузите компьютер и нажмите «Продолжить».") if re.search(r"cuda|nvidia|driver", tail, re.I) else ""
                raise RuntimeError("ComfyUI не запустился:\n" + tail + hint)
            try:
                await self.system_stats()
                return
            except Exception:
                await asyncio.sleep(2)
        raise RuntimeError("ComfyUI долго не отвечает:\n" + self.engines.log_tail(20))

    async def resolve_models(self):
        """Match the chosen model files against what ComfyUI sees (any subfolder)."""
        visible = {}
        for cat in {v["category"] for variants in catalog.VARIANTS.values() for v in variants.values()}:
            try:
                _, lst = await self.comfy_json("GET", f"/models/{cat}")
            except Exception:
                lst = []
            visible[cat] = lst or []
        self.visible = {cat: {n.replace("/", "\\").split("\\")[-1] for n in lst} for cat, lst in visible.items()}
        names, found, entries = {}, set(), {}
        for role, variant in self.choice().items():
            entry = entries[role] = catalog.model_entry(role, variant)
            picked = (self.config.get("model_pick") or {}).get(role)  # a file the user chose by hand
            def seen(file):
                ok = {file, *catalog.FILE_ALIASES.get(file, ())}  # same file under another name (e.g. upscaler from Hugging Face)
                return next((n for n in visible[entry["category"]] if n.replace("/", "\\").split("\\")[-1] in ok), None)
            others = {v["file"] for r, vs in catalog.VARIANTS.items() if r != role for v in vs.values()}
            if picked and picked.replace("/", "\\").split("\\")[-1] in others:  # the file of ANOTHER model of the app: never feed it to this role
                picked = None
            hit = picked if picked in visible[entry["category"]] else seen(entry["file"])
            if not hit and role not in (self.config.get("choice_override") or {}):  # another variant the user already has (v1.3 instead of v2); never for a version picked by hand
                hit = next(filter(None, (seen(v["file"]) for v in catalog.VARIANTS[role].values())), None)
            names[role] = hit or entry["name"]
            if hit:
                found.add(role)
        # modes added after a set-up was made: with your own ComfyUI they switch on by themselves when the files are there
        self.auto_modes = {m for m in ("krea", "kreareal", "qwen", "flux", "upscale", "long") if self.config.get("engine") == "external"
                           and all(r in found for r in catalog.MODE_ROLES[m])}
        needed = catalog.roles_for(self.modes())
        needed += [r for r in catalog.EXTRA_ROLES if r not in needed]  # optional models: listed, downloaded on demand
        missing = [entries[r] for r in needed if r in entries and r not in found]
        self.model_names, self.missing, self.found, self.needed = names, missing, found, needed
        return names, missing

    async def comfy_json(self, method, path, **kw):
        async with self.session.request(method, self.comfy + path, **kw) as r:
            body = await r.text()
            data = json.loads(body) if body else None
            return r.status, data

    async def system_stats(self):
        try:
            status, data = await self.comfy_json("GET", "/system_stats", timeout=aiohttp.ClientTimeout(total=8))
            if status != 200 or not isinstance(data, dict) or "system" not in data:
                raise ValueError("no stats")
        except Exception:
            # some builds hide or slow down /system_stats; a queue answer still means ComfyUI is alive
            status, _ = await self.comfy_json("GET", "/queue", timeout=aiohttp.ClientTimeout(total=8))
            if status != 200:
                raise
            data = {"system": {}, "devices": []}
        argv = data["system"].get("argv", [])
        if "--output-directory" in argv:
            self.output_dir = Path(argv[argv.index("--output-directory") + 1])
        self.stats = data  # the command line in it is how the user's own ComfyUI can be started again
        return data

    async def reconcile_jobs(self):
        """After a reconnect: a job marked running may have finished while the app was not listening.
        A short websocket hiccup used to leave such a job stuck at its last percentage forever, with the
        finished clip never reaching the Library, so on every reconnect we ask ComfyUI what really happened."""
        if not self.jobs:
            return
        try:
            _, q = await self.comfy_json("GET", "/queue", timeout=aiohttp.ClientTimeout(total=10))
            alive = {str(x[1]) for key in ("queue_running", "queue_pending") for x in (q or {}).get(key, []) if len(x) > 1}
        except Exception:
            return
        now = datetime.now().timestamp()
        for pid, job in list(self.jobs.items()):
            if job.get("task") or pid in alive:  # a cloud task, or ComfyUI still has it
                continue
            if now - (job.get("t0") or 0) < 20:  # just submitted: it may not be in /queue yet
                continue
            try:
                _, hist = await self.comfy_json("GET", f"/history/{pid}")
            except Exception:
                continue
            entry = (hist or {}).get(pid)
            try:
                if not entry:
                    self.jobs.pop(pid, None)
                    await self.broadcast({"type": "error", "prompt_id": pid, "message": (
                        "Задание пропало из очереди ComfyUI: связь с ним прерывалась. Запустите ещё раз.")})
                elif entry.get("status", {}).get("status_str") == "error":
                    self.jobs.pop(pid, None)
                    await self.broadcast({"type": "error", "prompt_id": pid,
                                          "message": "ComfyUI закончил задание с ошибкой (связь с ним прерывалась)"})
                else:
                    await self.finish_job(job)  # it is done: take the result into the Library as usual
            except Exception as e:
                self.note_error(f"Не удалось восстановить задание после обрыва связи: {e}")

    async def comfy_ws_loop(self):
        while True:
            if not self.config.get("setup_done") and not self.comfy_override:
                await asyncio.sleep(2)
                continue
            ws_url = self.comfy.replace("http", "ws", 1) + f"/ws?clientId={CLIENT_ID}"
            try:
                async with self.session.ws_connect(ws_url, max_msg_size=0, heartbeat=20) as ws:
                    self.comfy_online = True
                    await self.resolve_models()
                    await self.reconcile_jobs()
                    await self.broadcast({"type": "engine", "online": True})
                    await self.warn_weak_gpu()
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self.on_comfy_event(json.loads(msg.data))
                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            await self.on_comfy_binary(msg.data)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            if self.comfy_online:
                self.comfy_online = False
                await self.broadcast({"type": "engine", "online": False})
            await asyncio.sleep(2)

    async def warn_weak_gpu(self):
        """The 'max' profile (full ~32 GB Singularity) is meant for 24 GB cards. Say so once per run on a weaker card."""
        if getattr(self, "_weak_warned", False) or self.config.get("choice", {}).get("lipsync_unet") != "full":
            return
        try:
            if self.hw is None:
                self.hw = await asyncio.to_thread(engine.detect_hardware)
            vram = self.hw.get("vram_gb") or 0
        except Exception:
            return
        if 0 < vram < 24:
            self._weak_warned = True
            text = (f"Выбрана самая тяжёлая модель (профиль «Максимум»), а у видеокарты {vram:g} ГБ памяти: "
                    "генерация может зависать на первом шаге. Откройте «Настройки», выберите для Lipsync/Референсов "
                    "облегчённую версию (pruned) и скачайте её.")
            self.note_error(text)
            await self.broadcast({"type": "notice", "text": text})

    def watchdog_on(self):
        return self.config.get("gpu_watchdog") is not False

    def watchdog_local(self):
        """A rented or otherwise remote ComfyUI cannot be started again from here, and its card is not ours."""
        return self.engines.local()

    def running_jobs(self):
        return [j for j in self.jobs.values() if j.get("state") == "running" and not j.get("task")]

    async def guard_loop(self):
        while True:
            await asyncio.sleep(watchdog.PERIOD)
            try:
                await self.guard_tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # the watch must never take the app down with it
                self.note_error(f"Сторож видеокарты: {type(e).__name__}: {e}")

    async def guard_tick(self):
        """One look at the card. Cheap when nothing runs: nvidia-smi is asked only while a job is going."""
        if self.engines.restarting:  # the engine is being replaced right now: there is nothing to judge yet
            return
        if not self.watchdog_on() or not self.watchdog_local() or not self.running_jobs():
            self.guard.paused()
            return
        gpu = await asyncio.to_thread(self.monitor.gpu)
        self.guard_gpu = gpu
        verdict = self.guard.observe(gpu, busy=True, online=self.comfy_online)
        if verdict:
            await self.on_stuck(verdict)

    async def on_stuck(self, verdict):
        """The engine looks hung: say why, restart it, and tell the user what that costs."""
        line = watchdog.describe(verdict)
        self.journal.event("watchdog", verdict=str(getattr(verdict, "kind", verdict))[:40],
                           text=line)
        self.guard_log(line)
        self.note_error("Сторож видеокарты: " + line)
        await self.broadcast({"type": "notice", "text": watchdog.verdict_text(verdict)})
        ok, why = await self.restart_engine()
        if ok:
            await self.drop_engine_jobs("Задание прервано: движок завис, ComfyUI перезапущен")
            self.guard.reset()
            return
        text = (f"Перезапустить ComfyUI не получилось: {why}. Закройте окно ComfyUI и запустите его снова. "
                "Если это повторяется, перезагрузите компьютер и обновите драйвер NVIDIA.")
        self.note_error(text)
        await self.broadcast({"type": "notice", "text": text})

    async def restart_engine(self):
        """Start ComfyUI again. The engine manager owns the work and its one-restart-at-a-time rule:
        callers that arrive while it runs wait for it and receive the same answer, so a watch, a
        cancel timeout and the button never start two engines."""
        return await self.engines.restart()

    async def drop_engine_jobs(self, message=None, kind="error"):
        """The engine was replaced, so its queue is gone: nothing of what it had will finish now."""
        for jid in [j for j, job in self.jobs.items() if not job.get("task") and not job.get("finalizing")]:
            # a job that is being finalized has already finished in the engine: only its result is
            # being written, so a replaced engine is not a reason to declare it lost
            self.try_set_job_state(self.jobs[jid], JobState.CANCELED if kind == "interrupted" else JobState.FAILED,
                                   error=message)
            self.jobs.pop(jid, None)
            await self.broadcast({"type": kind, "prompt_id": jid, **({"message": message} if message else {})})

    def guard_log(self, line):
        """One line about the card, for the report and for the developer. The window never reads it."""
        try:
            path = DATA_DIR / "watchdog.log"
            if path.exists() and path.stat().st_size > 400_000:
                path.replace(path.with_suffix(".old"))
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {line}\n")
        except OSError:
            pass

    def guard_log_tail(self, n=12):
        try:
            return "\n".join((DATA_DIR / "watchdog.log").read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
        except OSError:
            return ""

    def watch_state(self):
        """What the sidebar shows about the watch: nothing while all is well."""
        if not self.watchdog_on() or not self.watchdog_local():
            return {"state": "off", "text": ""}
        last = self.engines.last
        if last and last.get("at") and time.time() - last["at"] < 600:
            if last.get("ok"):
                when = time.strftime("%H:%M", time.localtime(last["at"]))
                return {"state": "warn", "text": f"перезапущен в {when}"}
            return {"state": "bad", "text": "перезапуск не удался"}
        if not self.running_jobs():
            return {"state": "ok", "text": ""}
        stall = self.guard.stall()
        if stall < 120:
            return {"state": "ok", "text": ""}
        g = self.guard_gpu or {}
        card = ""
        if g.get("util") is not None:
            card = f" · {round(g['util'])} %"
            if g.get("temp") is not None:
                card += f" · {round(g['temp'])} °C"
        return {"state": "warn", "text": f"нет шагов {int(stall // 60)} мин{card}"}

    async def on_comfy_binary(self, data):
        if len(data) < 8:
            return
        kind = struct.unpack(">I", data[:4])[0]
        if kind == BIN_PREVIEW:
            image = data[8:]
        elif kind == BIN_PREVIEW_META:
            meta_len = struct.unpack(">I", data[4:8])[0]
            meta = json.loads(data[8:8 + meta_len])
            if meta.get("prompt_id") not in self.jobs:
                return
            image = data[8 + meta_len:]
        else:
            return
        if not any(j["state"] == "running" for j in self.jobs.values()):
            return
        self.guard.note_progress()  # a live preview is the strongest sign of a working render
        self.events.preview(image)  # queued, dropped if a window cannot keep up: a frame is not state

    async def on_comfy_event(self, msg):
        t, d = msg.get("type"), msg.get("data") or {}
        if t == "kj_preview_override":   # animated live preview of KJNodes (no prompt id in it: it belongs to the running job)
            run = next((j for j in self.jobs.values() if j["state"] == "running"), None)
            if run and d.get("image"):
                await self.broadcast({"type": "preview_anim", "prompt_id": run["prompt_id"], "image": d["image"],
                                      "mime": d.get("mime") or "image/jpeg", "step": d.get("step"), "total": d.get("total")})
            return
        job = self.jobs.get(d.get("prompt_id"))
        if not job:
            return
        pid = job["prompt_id"]
        self.guard.note_progress()  # any event of our job means ComfyUI is alive and working on it
        if t in ("execution_start", "executing", "progress"):
            job["last_ev"] = datetime.now().timestamp()  # the lost-job watch measures silence in wall-clock seconds
        if t == "execution_start":
            self.try_set_job_state(job, JobState.RUNNING)
            await self.send_stage(job, "load")
        elif t == "executing" and d.get("node"):
            stage = job["stages"].get(d["node"])
            if stage:
                await self.send_stage(job, stage, node=d["node"])
        elif t == "progress":
            stage = job["stages"].get(str(d.get("node")), job.get("stage"))
            if stage in ("sample", "sample2"):
                await self.send_stage(job, stage, d.get("value"), d.get("max"), node=str(d.get("node")))
        elif t == "execution_success":
            await self.finish_job(job)
        elif t == "execution_error":
            self.try_set_job_state(job, JobState.FAILED, error=str(d.get("exception_message", "")).strip())
            self.note_error(f'Ошибка генерации, узел {d.get("node_type", "")}: {d.get("exception_message", "").strip()}')
            hint = self.model_hint((job.get("meta") or {}).get("mode", ""), d.get("exception_message", ""))
            await self.broadcast({"type": "error", "prompt_id": pid,
                                  "message": f'{d.get("node_type", "")}: {d.get("exception_message", "").strip()}' + hint})
            self.jobs.pop(pid, None)
        elif t == "execution_interrupted":
            self.try_set_job_state(job, JobState.CANCELED)
            await self.broadcast({"type": "interrupted", "prompt_id": pid})
            self.jobs.pop(pid, None)

    async def send_stage(self, job, stage, step=None, total=None, node=None):
        job["stage"] = stage
        job["step"], job["step_max"] = step, total  # agents read the same numbers: "step 3 of 8"
        msg = {"type": "stage", "prompt_id": job["prompt_id"], "stage": stage, "step": step, "max": total}
        sc = job.get("scenes")
        if sc and node is not None and str(node) in sc.get("map", {}):
            job["scene_now"] = sc["map"][str(node)] + 1
        if sc and job.get("scene_now"):
            msg.update(scene=job["scene_now"], scenes=sc["n"])
        await self.broadcast(msg)

    async def job_outputs(self, pid, asks=4, delay=0.6):
        """What a finished job produced.

        ComfyUI announces «execution_success» before the history entry is written, so a single question
        right after the event can honestly answer "nothing yet" while the video is already on disk. That
        is how a finished render used to end up as «ComfyUI не вернул ни видео, ни изображение»: ask
        again for a few seconds before believing it.
        """
        for n in range(asks):
            try:
                _, hist = await self.comfy_json("GET", f"/history/{pid}", timeout=aiohttp.ClientTimeout(total=10))
            except Exception:
                hist = None
            outputs = ((hist or {}).get(pid) or {}).get("outputs") or {}
            if any(isinstance(values, list) and values for out in outputs.values() if isinstance(out, dict)
                   for values in out.values()):  # outputs is node id -> {"images": [...], "animated": [...]}
                return outputs
            if n < asks - 1:
                await asyncio.sleep(delay * (n + 1))
        return {}

    def backend_identity(self):
        """Which engine a job belongs to. The settings may change later; a job must not follow them."""
        if self.rh():
            return "cloud:runninghub"
        return "comfy:" + self.comfy

    def build_backend(self):
        """The engine behind the job service: small callables, not the whole App."""
        return ComfyBackend(client=self.comfy_client, url=self.comfy, client_id=CLIENT_ID,
                            models=lambda: self.model_names, loras=lambda mode: self.custom_loras(mode),
                            view=self.view_stream)

    async def comfy_client(self, method, path, **kwargs):
        """The backend's HTTP: an engine that does not answer is not a job that failed."""
        try:
            return await self.comfy_json(method, path, **kwargs)
        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError, OSError) as e:
            raise BackendUnavailable(f"ComfyUI не отвечает: {type(e).__name__}") from e

    def attach_job(self, record, *, meta, last_output=False, titles=None, preview=False,
                   graph=None, stages=None, scenes=None):
        """Put a submitted job into the live queue and the database.

        The record is the truth; this dict is what the window, the engine events and the agent view
        have always used. One place builds it, whether the job came through the service or not.
        """
        meta = self.submission_meta(meta)
        pid = record.prompt_id or record.backend_job_id
        if not pid:
            raise InvalidTransition("У задания нет идентификатора: показывать в очереди нечего")
        job = {"prompt_id": pid, "state": public_state(record.state),
               "stages": stages if stages is not None else (record.legacy or {}).get("stages") or {},
               "stage": None, "meta": meta, "last_output": last_output, "titles": titles or {},
               "graph": graph if graph is not None else record.graph, "scenes": scenes,
               "t0": record.created_at, "preview": preview, "record": record}
        self.jobs[pid] = job
        return job

    def new_record(self, spec, prompt_id, backend_job_id=None):
        """The typed record of a new job: our id, the engine's id, the public id and the request.

        The record is born already queued: it exists only once the engine has accepted the job and
        given it an id. Before that there is nothing to track — the durable "submitting" moment of
        stage 4 is what will change that.
        """
        now = datetime.now().timestamp()
        return JobRecord(job_id=new_job_id(), spec=spec, backend="cloud" if self.rh() else "comfy",
                         backend_id=self.backend_identity(), backend_job_id=backend_job_id or prompt_id,
                         prompt_id=prompt_id, state=JobState.QUEUED, created_at=now, updated_at=now)

    def set_job_state(self, job, target, *, error=None):
        """The one place a job changes state.

        The rules live in `domain.jobs`; here the legacy fields the window and the agent API read are
        kept in step with the record: `state` in the words they already know, and the `finalizing`
        mark. A job without a record (an older run, or a test double) keeps the old behaviour.
        """
        target = JobState(target)
        record = job.get("record")
        if record is None:
            job["state"] = public_state(target)
            job["finalizing"] = target is JobState.FINALIZING
            return True
        was_state, was_at = record.state, record.updated_at
        changed = record.apply(target, now=datetime.now().timestamp(), error=error)
        job["state"] = public_state(record.state)
        job["finalizing"] = record.state is JobState.FINALIZING
        if changed:
            self.journal.event("transition", job_id=record.job_id, prompt_id=record.prompt_id,
                               backend_job_id=record.backend_job_id, backend=record.backend,
                               mode=record.spec.mode if record.spec else None,
                               was=was_state.value, now=record.state.value,
                               ms=int((record.updated_at - (was_at or record.updated_at)) * 1000),
                               error=error)
        if changed and record.state.terminal:
            # the outcome is a fact the moment it happens: write it down before any window hears about
            # it, so the details window and the snapshot never depend on the order of events. A live
            # job is written with the fields the window keeps (meta, stages); a job that is not in the
            # queue any more keeps the record it already has.
            if job.get("prompt_id") in self.jobs:
                self.save_queue()
            else:
                try:
                    self.jobs_repo.save(record)
                except STORE_ERRORS as e:
                    self.queue_save_error = str(e)
                    self.note_error(f"Не удалось сохранить исход задания: {e}")
        return changed

    def try_set_job_state(self, job, target, *, error=None):
        """Like set_job_state, but a late or repeated engine event is ignored instead of raising.

        The websocket loop must not be dropped because ComfyUI sent the same event twice or sent one
        for a job that has already ended.
        """
        try:
            return self.set_job_state(job, target, error=error)
        except InvalidTransition:
            return False

    async def finish_job(self, job):
        """Register the result of a job that ComfyUI has finished.

        Four different places ask for this — the websocket event, the reconnect reconciliation, the
        cancel race and the lost-job watch — and any of them can arrive twice or at the same moment.
        So the registration happens once per job, and the job leaves the queue only *after* its result
        is registered: between the generation and the library record the job stays visible, and a
        restart in that window can pick the result up instead of rendering it again.

        A failure here is not fatal: the job stays in the queue marked for retry, the error is shown,
        and the next event (or the watch) tries again — the clip on disk is not generated twice.
        """
        if job.get("finalizing") or job.get("finalized"):
            return   # a second event for the same job: the first one is registering its result
        pid = job["prompt_id"]
        started = time.monotonic()
        self.set_job_state(job, JobState.FINALIZING)   # also sets the `finalizing` mark the watch reads
        job["finalize_tries"] = job.get("finalize_tries", 0) + 1
        self.save_queue()   # a crash in the middle of finalization must be known to the next start
        try:
            await self._finish_job(job)
        except Exception as e:  # noqa: BLE001 - a lost library record is better than a lost clip
            job["finalizing"] = False
            record = job.get("record")
            if record is not None:
                record.finalize_attempts += 1   # stays FINALIZING: the generation itself did happen
            self.note_error(f"Результат задания {pid} сгенерирован, но не сохранился: {type(e).__name__}: {e}")
            self.journal.event("finalize_failed", prompt_id=pid,
                               job_id=record.job_id if record is not None else None,
                               error=journal.error_type(e), ms=int((time.monotonic() - started) * 1000))
            await self.broadcast({"type": "notice", "text": (
                "Результат готов, но записать его в Библиотеку не удалось: " + str(e) +
                ". Задание осталось в очереди — можно повторить.")})
            self.save_queue()
            return
        record = job.get("record")
        self.journal.event("finalized", prompt_id=pid,
                           job_id=record.job_id if record is not None else None,
                           backend_job_id=record.backend_job_id if record is not None else None,
                           results=len(record.results) if record is not None else None,
                           ms=int((time.monotonic() - started) * 1000))

    async def _finish_job(self, job):
        """One call to the media service: find the outputs, make the copies, record, notify.

        The order inside the service is the recovery rule (see `services.media`): a broken transfer
        leaves a `.part`, never a half-written clip under the real name, and a repeated attempt finds
        the copy and the Library record of the previous one instead of making a second pair.
        """
        pid = job["prompt_id"]
        try:
            await self.media.finalize(job, notify=self.broadcast)
        except EmptyResult:
            self.note_error(f"ComfyUI закончил работу, но в истории нет файлов (папка {self.output_dir or 'output'})")
            self.set_job_state(job, JobState.FAILED, error="ComfyUI не вернул ни видео, ни изображение")
            self.jobs.pop(pid, None)   # there is nothing to wait for any more
            await self.broadcast({"type": "error", "prompt_id": pid,
                                  "message": "ComfyUI не вернул ни видео, ни изображение: проверьте папку output"})
            return
        job["finalized"] = True
        self.set_job_state(job, JobState.SUCCEEDED)
        self.jobs.pop(pid, None)   # the result is registered: only now the job leaves the queue

    async def keep_scene_state(self, job, refs):
        """A scene of the long video: keep the tail of its latent for the next scene, not for a size test.

        The latent is streamed through a `.part` like every other result file: it is small, but a
        half-written state would make the next scene load a broken one.
        """
        lg = (job.get("meta") or {}).get("long")
        if not lg or lg.get("test"):
            return
        lat = next((ref for _, ref in refs if str(ref["filename"]).lower().endswith(".latent")), None)
        videos = [ref for _, ref in refs if str(ref["filename"]).lower().endswith((".mp4", ".webm", ".mkv", ".mov"))]
        if not lat or not videos:
            return
        try:
            d = LONG_DIR / lg["run"]
            d.mkdir(parents=True, exist_ok=True)
            sfx = "_d" if lg.get("draft") else ""
            async with self.service.backend.open_output(lat) as stream:
                await self.media.files.save_stream(d / f"state_{lg['part']}{sfx}.latent", stream.chunks,
                                                   expected_bytes=stream.expected_bytes)
            atomic_write_json(d / f"state_{lg['part']}{sfx}.json",
                              {"res": (job.get("meta") or {}).get("res", "")})
        except Exception as e:
            self.note_error(f"Не удалось сохранить состояние сцены: {e}")

    async def broadcast(self, payload):
        """One event for the open windows. The fact (if it is one) is recorded first, delivery never waits.

        The hub queues the event per window: a slow or frozen window cannot delay the finalization,
        the next job or another window. It reconnects and syncs from the snapshot instead.
        """
        await self.events.publish(payload)

    def recent_outcomes(self, limit=100):
        """The latest outcomes in the window's words (oldest first): prompt_id, state, at, message.

        The record is the owner of the outcome, so the same list is served to a reconnecting window
        and to an agent, and it contains what happened in earlier runs of the program as well.
        """
        words = {"succeeded": "done", "failed": "error", "canceled": "interrupted"}
        return [{"prompt_id": record.prompt_id or record.backend_job_id,
                 "state": words[record.state.value],
                 "at": datetime.fromtimestamp(record.updated_at).isoformat(timespec="seconds"),
                 "message": (record.legacy or {}).get("message") or ""}
                for record in reversed(self.jobs_repo.outcomes(limit=limit))]

    def job_outcome(self, pid):
        """How a job that has left the queue ended: the record in the database is the one owner.

        Returns the shape the window and the agent have always used (`state` in their words, `at`,
        `message`, plus `meta`/`created` for the details window), or None when the job is unknown.
        A succeeded job is normally found through the Library record first; this is the fallback for
        failed and stopped ones, which have no Library item.
        """
        record = self.jobs_repo.by_prompt(pid) if pid else None
        if record is None or record.state.value not in JobRepository.FINAL:
            return None
        return {"state": {"succeeded": "done", "failed": "error", "canceled": "interrupted"}[record.state.value],
                "at": datetime.fromtimestamp(record.updated_at).isoformat(timespec="seconds"),
                "message": (record.legacy or {}).get("message") or "",
                "meta": (record.legacy or {}).get("meta") or {},
                "created": record.created_at, "phase": record.state.value}

    def remember(self, payload):
        """Write how a job ended into its record, before the event is delivered.

        The record is the one owner of what happened: the row of a finished job stays in `jobs` (the
        queue is `active()`), so the details window and a reconnected window read the same facts, also
        after a restart. Called by the event hub, so the outcome exists even when no window is open.
        """
        kind = {"done": "succeeded", "error": "failed", "interrupted": "canceled"}.get(payload.get("type", ""))
        pid = payload.get("prompt_id")
        if not kind or not pid:
            return
        self.journal.event("outcome", prompt_id=pid, state=kind, message=payload.get("message") or "")
        record = self.jobs_repo.by_prompt(pid)
        if record is not None and record.can(JobState(kind)):
            record.apply(JobState(kind), now=time.time(), error=payload.get("message"))
            self.jobs_repo.save(record)
        self.jobs_repo.note_message(pid, payload.get("message") or "")

    def ui_snapshot(self):
        """What a window may have missed: the live queue, the latest outcomes and their results.

        Sent as the first message of a connection (`hello`). The events are not a journal and are not
        meant to be one: after a reconnect the window syncs from this snapshot and treats the events
        that follow as updates, so a repeated or a missed event cannot leave it with a wrong state.
        """
        jobs = []
        for pid, job in list(self.jobs.items()):
            meta = job.get("meta") or {}
            jobs.append({"prompt_id": pid, "state": job.get("state") or "queued",
                         "stage": job.get("stage"), "step": job.get("step"), "max": job.get("step_max"),
                         "mode": meta.get("mode"), "finalizing": bool(job.get("finalizing")),
                         "prompt": str(meta.get("sub") or meta.get("prompt") or "")[:140],
                         "agent": (meta.get("origin") or {}).get("kind") == "agent"})
        recent = self.recent_outcomes(limit=100)
        wanted = {rec["prompt_id"] for rec in recent}
        results = []
        for item in self.library.items:
            if item.get("prompt_id") in wanted:
                results.append(self.public(item))
                if len(results) >= 60:
                    break
        # Drafts have independent retention: even when their old job outcome has been pruned,
        # they must be available in the section after every restart.
        results.extend(self.public(item) for item in self.pending.values())
        return {"type": "hello", "engine": self.comfy_online, "jobs": jobs, "recent": recent, "results": results}

    def public(self, item):
        p = self.file_path(item)
        # a video deleted from disk stays in the list until the user removes it, but is marked
        return {k: v for k, v in item.items() if k != "file"} | {"url": f"/api/file/{item['id']}",
                                                                 "missing": bool(p) and not p.exists()}

    def file_path(self, item):
        if item["file"].get("type") == "local":
            return Path(item["file"]["path"])
        orig = None
        if self.output_dir and item["file"].get("type") == "output":
            orig = self.output_dir / item["file"].get("subfolder", "") / item["file"]["filename"]
        if orig and orig.exists():
            return orig
        local = item.get("local")   # the program's own copy: it stays when ComfyUI's output folder is cleaned
        if local and Path(local).exists():
            return Path(local)
        media = self.media_repo.get(item["id"])
        if media is not None and media.path and Path(media.path).exists():
            return Path(media.path)
        return orig

    def find_item(self, item_id):
        """A Library record, or a finished picture that is still waiting for «Добавить в библиотеку»."""
        return self.library.get(item_id) or self.pending.get(item_id)

    def hold_preview(self, item):
        """A picture is not added to the Library by itself: it waits until the user presses «Добавить в библиотеку»."""
        item["preview"] = True
        item.setdefault("project", self.active_project())
        self.drafts_repo.save(item)
        self.note_preview_committed(item)

    def note_preview_committed(self, item):
        """Update the UI cache after the draft transaction; do not write the same row again."""
        item["preview"] = True
        self.pending[item["id"]] = item

    def default_results_dir(self):
        """Videos\\Minimax H3\\output in the user's folder: it does not depend on where the program lies and survives updates."""
        return Path.home() / "Videos" / "Minimax H3" / "output"

    def results_dir(self):
        d = self.config.get("results_dir")
        return Path(d) if d else self.default_results_dir()

    def keep_copies(self):
        return self.config.get("keep_copies") is not False

    async def archive_item(self, item):
        """Copy the finished file of a library item into the program's own results folder.

        The copy itself belongs to the media service: a `.part` next to the destination, a size
        check, an atomic rename, and a failure that is reported as `archive_failed` instead of
        putting the generation in doubt.
        """
        return await self.media.archive_item(item)

    async def upload_state(self, path, name):
        """Put a saved scene state (.latent) into ComfyUI's input folder, where its LoadLatent node looks for it."""
        data = aiohttp.FormData()
        data.add_field("image", path.read_bytes(), filename=name, content_type="application/octet-stream")
        data.add_field("type", "input")
        data.add_field("overwrite", "true")
        status, res = await self.comfy_json("POST", "/upload/image", data=data)
        if status != 200:
            raise web.HTTPBadGateway(text="Не удалось передать состояние сцены в ComfyUI")
        return res["name"]

    async def upload_input(self, field, rh=None):
        """Put an image/audio/video file into ComfyUI's input/h3studio folder (or upload it to RunningHub)."""
        data = aiohttp.FormData()
        name = f"h3s_{uuid.uuid4().hex[:8]}{Path(field.filename or 'img.png').suffix or '.png'}"
        content_type = field.headers.get("Content-Type", "application/octet-stream")
        raw = await field.read()
        if getattr(self, "_capture", None) is not None:
            self._capture[field.name] = (Path(field.filename or "x.png").suffix.lower() or ".png", raw)
        if rh:
            try:
                return await rh.upload(name, raw, content_type)
            except cloud.CloudError as e:
                raise web.HTTPBadGateway(text=str(e))
        data.add_field("image", raw, filename=name, content_type=content_type)
        data.add_field("subfolder", "h3studio")
        data.add_field("type", "input")
        status, res = await self.comfy_json("POST", "/upload/image", data=data)
        if status != 200:
            raise web.HTTPBadGateway(text="Не удалось загрузить файл в ComfyUI")
        return f'{res["subfolder"]}/{res["name"]}' if res.get("subfolder") else res["name"]

    async def put_input(self, path, name=None):
        """A file from disk into ComfyUI's input/h3studio (a picture an agent brought, a ready clip)."""
        path = Path(path)
        data = aiohttp.FormData()
        nm = name or f"h3s_{uuid.uuid4().hex[:8]}{path.suffix.lower() or '.png'}"
        try:
            raw = await asyncio.to_thread(path.read_bytes)
        except OSError as e:
            raise web.HTTPBadRequest(text=f"Не удалось прочитать файл: {e}")
        data.add_field("image", raw, filename=nm, content_type="application/octet-stream")
        data.add_field("subfolder", "h3studio")
        data.add_field("type", "input")
        status, res = await self.comfy_json("POST", "/upload/image", data=data)
        if status != 200:
            raise web.HTTPBadGateway(text="Не удалось передать файл в ComfyUI")
        return f'{res["subfolder"]}/{res["name"]}' if res.get("subfolder") else res["name"]

    def storage_state(self):
        """What the database is: where it lives, which schema it has, what the import did."""
        migration = self.migration.to_dict() if self.migration is not None else None
        return {"path": str(STATE_DB), "schema_version": self.db.schema_version(),
                "migration": migration, "error": self.migration_error,
                "waiting": len(self.pending_resume)}

    def rh(self):
        """A RunningHub client when cloud generation is switched on, else None."""
        if self.config.get("rh_enabled") and self.config.get("rh_key"):
            return cloud.RunningHub(self.session, self.config.get("rh_key"), self.config.get("rh_host", "ai"))
        return None

    async def read_form(self, req, file_fields, need_prompt=True):
        form, files = {}, {}
        rh = self.rh()
        async for field in await req.multipart():
            if field.name in file_fields and field.filename:
                files[field.name] = await self.upload_input(field, rh)
            else:
                form[field.name] = await field.text()
        prompt = form.get("prompt", "").strip()
        aspect, quality = form.get("aspect", "16:9"), form.get("quality", "720p")
        if not prompt and need_prompt:
            raise web.HTTPBadRequest(text="Напишите промпт")
        if aspect not in wf.ASPECTS or quality not in wf.QUALITY:
            raise web.HTTPBadRequest(text="Неизвестный формат или качество")
        seed = int(form["seed"]) if form.get("seed", "").isdigit() else random.randrange(2 ** 50)
        return form, files, prompt, aspect, quality, seed

    async def prepare_engine(self, mode_key):
        """Before a job goes to the engine: the agent's model out of VRAM, the previous job's models freed.

        The rule (when to ask, when to keep the models for the next picture) lives in the engine
        manager; this is the app side of that handshake.
        """
        return await self.engines.prepare(mode_key)

    async def release_vram(self):
        """The card side of `prepare_engine`: ask ComfyUI to unload, then say what is still held."""
        try:
            await self.comfy_json("POST", "/free", json={"unload_models": True, "free_memory": True})
            await asyncio.sleep(1.5)
            gpu = await asyncio.to_thread(self.monitor.gpu)
            used = (gpu or {}).get("vram_used_gb")
            if used is not None and used > 2.5:  # right after /free ComfyUI itself holds well under this
                await self.broadcast({"type": "notice", "text": (
                    f"Перед заданием занято {used:g} ГБ видеопамяти. Скорее всего, её держат другие программы "
                    "(браузер, Adobe, игры). Закройте их, иначе генерация может идти медленнее.")})
        except Exception:
            pass

    async def queue(self, graph, stages, meta, last_output=False, titles=None, preview=False, spec=None):
        """The submission of the modes that still build their graph in the handler.

        Video goes through the job service (see `start_video_job`); the rest of the modes keep this
        path until stage 4 brings each of them over.
        """
        scenes = stages.pop("__scenes__", None)   # which sampler is which scene (long video), for the progress text
        rh = self.rh()
        if rh:
            return await self.queue_cloud(rh, graph, stages, meta, preview, spec=spec)
        await self.prepare_engine((meta or {}).get("mode", ""))
        record = self.begin_submission(spec, graph, stages, meta, preview, last_output, titles)
        try:
            status, res = await self.comfy_json("POST", "/prompt", json={
                "prompt": graph, "client_id": CLIENT_ID,
                "extra_data": {"preview_method": "auto"}})
        except BaseException:
            record.apply(JobState.UNCERTAIN, now=time.time())
            self.jobs_repo.save(record)
            raise
        if status != 200:
            record.fail(comfy_error_text(res), now=time.time())
            self.jobs_repo.save(record)
            return web.json_response({"error": comfy_error_text(res)}, status=400)
        pid = res["prompt_id"]
        record.prompt_id = record.backend_job_id = pid
        record.apply(JobState.QUEUED, now=time.time())
        self.jobs_repo.save(record)
        self.attach_job(record, meta=meta, last_output=last_output, titles=titles,
                        preview=preview, graph=graph, stages=stages, scenes=scenes)
        return web.json_response({"prompt_id": pid, "seed": meta["seed"], "queue_number": res.get("number")})

    def begin_submission(self, spec, graph, stages, meta, preview=False, last_output=False, titles=None,
                         backend=None, backend_id=None):
        """Write intent before any backend POST, including modes without a typed video spec."""
        meta = self.submission_meta(meta)
        now = time.time()
        job_id = new_job_id()
        record = JobRecord(job_id=job_id, prompt_id="pending-" + job_id, spec=spec,
                           backend=backend or ("cloud" if self.rh() else "comfy"),
                           backend_id=backend_id or self.backend_identity(), created_at=now, updated_at=now,
                           graph=graph, legacy={"stages": stages, "meta": meta, "preview": preview,
                                                "last_output": last_output, "titles": titles or {}})
        record.apply(JobState.SUBMITTING, now=now)
        self.jobs_repo.save(record)
        return record

    async def start_video_job(self, spec, *, meta, last_output=False, titles=None, preview=False, key=None,
                              fingerprint=None):
        """The one submission path of a video job: window and agent, with or without an idempotency key.

        Returns (record, deduplicated). The engine's answer is turned into a service error, never into
        an HTTP response, so both entrances map it their own way.
        """
        await self.prepare_engine(spec.mode)
        started = time.monotonic()
        record, deduplicated = await self.service.submit_idempotent(
            spec, backend_id=self.backend_identity(), key=key, fingerprint=fingerprint)
        if not deduplicated:
            self.attach_job(record, meta=meta, last_output=last_output, titles=titles, preview=preview)
        self.journal.event("submitted", job_id=record.job_id, prompt_id=record.prompt_id,
                           backend_job_id=record.backend_job_id, mode=spec.mode,
                           deduplicated=deduplicated, keyed=bool(key),
                           ms=int((time.monotonic() - started) * 1000))
        return record, deduplicated

    async def need_models(self, mode):
        if self.rh():  # models live on RunningHub
            if not self.model_names:
                self.model_names = {r: catalog.VARIANTS[r][v]["name"] for r, v in self.choice().items()}
            return
        if mode not in self.modes():
            raise web.HTTPBadRequest(text="Этот режим не установлен. Добавьте его в разделе «Настройки».")
        if not self.model_names:
            await self.resolve_models()
        lost = [m["file"] for m in self.missing if m["role"] in catalog.MODE_ROLES[mode]]
        if lost:
            raise web.HTTPBadRequest(text="ComfyUI не видит модели: " + ", ".join(lost)
                                     + ". Откройте раздел «Настройки».")
