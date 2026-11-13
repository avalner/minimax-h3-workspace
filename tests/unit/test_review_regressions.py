"""Cross-layer regressions found during the refactoring review; no real backend/user data."""
import asyncio
import json
import sqlite3
import threading
import unittest
from pathlib import Path
from unittest import mock

import support
import server
import routes.jobs
import routes.montage
from agentapi import AgentError
from backends.base import BackendUnavailable, SubmitResult
from domain.errors import SchemaVersionError
from domain.jobs import JobSpec, JobState
from domain.media import MediaKind, MediaRecord, MediaState
from storage.database import Database, SCHEMA_VERSION
from storage.migrations import migrate


def spec():
    return JobSpec.video(prompt="test", aspect="16:9", quality="720p", seconds=5,
                         seed=42, width=1280, height=720)


class Backend:
    name = "comfy"

    def __init__(self):
        self.calls = 0

    async def submit(self, request):
        self.calls += 1
        await asyncio.sleep(0)
        return SubmitResult(backend_job_id=f"p{self.calls}", graph={"1": {"class_type": "Test"}}, stages={})


class ReviewTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = support.fresh_app()
        self.events = support.capture_broadcast(self.app)

    def reopen(self):
        self.app.db.close()
        self.app = support.new_app()
        return self.app

    async def test_inflight_and_uncertain_survive_queue_changes_and_restart(self):
        entered, release = asyncio.Event(), asyncio.Event()

        class LostReply:
            name = "comfy"

            async def submit(self, request):
                entered.set()
                await release.wait()
                raise BackendUnavailable("reply lost")

        self.app.service.backend = LostReply()
        task = asyncio.create_task(self.app.service.submit_idempotent(spec(), backend_id="comfy:test", key="k"))
        await entered.wait()
        job_id = self.app.idempotency.get("k")["job_id"]
        self.app.jobs["other"] = support.job("other", record=support.job_record("other"))
        self.assertEqual(self.app.jobs_repo.get(job_id).state, JobState.SUBMITTING)
        release.set()
        with self.assertRaises(BackendUnavailable):
            await task
        self.app.save_queue()
        app = self.reopen()
        self.assertEqual(app.jobs_repo.get(job_id).state, JobState.UNCERTAIN)
        record, duplicate = await app.service.submit_idempotent(spec(), backend_id="comfy:test", key="k")
        self.assertTrue(duplicate)
        self.assertEqual(record.job_id, job_id)
        app.comfy_json = mock.AsyncMock(return_value=(200, {"queue_pending": [], "queue_running": []}))
        with mock.patch.object(app, "queue", side_effect=AssertionError("must not resubmit")):
            await app.h_queue_resume(None)
        self.assertIsNotNone(app.jobs_repo.get(job_id))

    async def test_all_graph_modes_write_before_post(self):
        async def post(method, path, **kwargs):
            active = self.app.jobs_repo.active()
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0].state, JobState.SUBMITTING)
            self.assertEqual(active[0].legacy["meta"]["mode"], "qwen")
            raise ConnectionError("lost response")

        self.app.prepare_engine = mock.AsyncMock()
        self.app.comfy_json = post
        with self.assertRaises(ConnectionError):
            await self.app.queue({"1": {"class_type": "Test"}}, {}, {"mode": "qwen", "seed": 1})
        self.app.save_queue()
        self.assertEqual(self.reopen().jobs_repo.active()[0].state, JobState.UNCERTAIN)

    async def test_drafts_survive_restart_above_old_limit_and_keep_files(self):
        engine = support.new_dir("engine")
        self.app.output_dir = engine
        (engine / "clip.mp4").write_bytes(b"complete clip")
        self.app.media.outputs_of = mock.AsyncMock(return_value=support.VIDEO_OUTPUT)
        job = support.job(preview=True, record=support.job_record())
        self.app.jobs["p1"] = job
        await self.app.finish_job(job)
        draft = next(iter(self.app.pending.values()))
        self.assertTrue(Path(draft["local"]).exists())
        for n in range(45):
            self.app.hold_preview({**draft, "id": f"extra{n}", "prompt_id": f"old{n}"})
        (engine / "clip.mp4").unlink()
        app = self.reopen()
        self.assertEqual(len(app.pending), 46)
        self.assertEqual(app.library.items, [])
        self.assertEqual(len(app.ui_snapshot()["results"]), 46)
        self.assertFalse(app.public(app.pending[draft["id"]])["missing"])
        await app.h_preview_keep(support.Req({"id": draft["id"]}))
        app = self.reopen()
        self.assertEqual(len(app.library.items), 1)
        self.assertNotIn(draft["id"], app.pending)
        self.assertFalse(app.public(app.library.items[0])["missing"])

    async def test_keep_persists_archive_path_and_legacy_path_fallback(self):
        engine, copies = support.new_dir("engine"), support.new_dir("copies")
        self.app.output_dir = engine
        self.app.config.update(keep_copies=True, results_dir=str(copies))
        (engine / "clip.mp4").write_bytes(b"complete clip")
        item = {"id": "draft1", "date": "2026-11-13", "file": {
            "filename": "clip.mp4", "type": "output", "subfolder": ""}}
        self.app.hold_preview(item)
        await self.app.h_preview_keep(support.Req({"id": "draft1"}))
        app = self.reopen()
        kept = app.library.get("draft1")
        self.assertTrue(Path(kept["local"]).exists())
        (engine / "clip.mp4").unlink()
        self.assertFalse(app.public(kept)["missing"])
        # Repair access to already-created copies from the buggy release, too.
        kept.pop("local")
        self.assertTrue(app.file_path(kept).exists())

    async def test_future_schema_blocks_app_start(self):
        self.app.db.execute("INSERT INTO schema_migrations(name,version,applied_at) VALUES('future',?,0)",
                            (SCHEMA_VERSION + 1,))
        self.app.db.close()
        with self.assertRaises(SchemaVersionError):
            support.new_app()

    async def test_old_accidentally_published_draft_is_migrated(self):
        item = {"id": "old-draft", "file": {"type": "output", "filename": "x.png"}}
        self.app.library.repo.add(item)
        self.app.media_repo.save(MediaRecord(media_id="old-draft", kind=MediaKind.IMAGE, state=MediaState.DRAFT))
        self.app.db.execute("DELETE FROM schema_migrations WHERE name='0002-drafts'")
        self.app.db.execute("DROP TABLE drafts")
        app = self.reopen()
        self.assertEqual(app.library.items, [])
        self.assertTrue(app.pending["old-draft"]["preview"])

    async def test_optional_seed_and_uploaded_names_do_not_break_retries(self):
        self.app.config.data.update(modes=["video"], engine="external")
        self.app.need_models = mock.AsyncMock()
        self.app.prepare_engine = mock.AsyncMock()
        backend = Backend()
        self.app.service.backend = backend
        for body in ({"prompt": "test", "idempotency_key": "text"},
                     {"prompt": "image", "seed": 42, "inputs": {"first_frame": {"asset": "asset1"}},
                      "idempotency_key": "image"}):
            async def asset(ref, field):
                return {"asset": "asset1"} if ref else None
            with mock.patch.object(self.app.agents, "_ref_to_asset", asset), \
                 mock.patch.object(self.app.agents.assets, "file", return_value=Path("fake.png")), \
                 mock.patch.object(self.app, "put_input", mock.AsyncMock(side_effect=["random1.png", "random2.png"])) as upload:
                first = json.loads((await self.app.agents._submit(body)).text)
                second = json.loads((await self.app.agents._submit(body)).text)
                self.assertEqual(first["job"], second["job"])
                self.assertEqual(first["seed"], second["seed"])
                self.assertEqual(first["inputs"], second["inputs"])
                self.assertTrue(second["deduplicated"])
                self.assertLessEqual(upload.await_count, 1)
        self.assertEqual(backend.calls, 2)
        with self.assertRaises(AgentError) as conflict:
            await self.app.agents._submit({"prompt": "changed", "idempotency_key": "text"})
        self.assertEqual(conflict.exception.code, "IDEMPOTENCY_CONFLICT")

    async def test_outcome_write_failure_is_reported_without_nameerror(self):
        job = support.job(record=support.job_record())
        with mock.patch.object(self.app.jobs_repo, "save", side_effect=OSError("disk error")):
            self.app.set_job_state(job, JobState.FAILED)
        self.assertIn("disk error", self.app.queue_save_error)

    async def test_api_retry_after_restart_preserves_generated_seed(self):
        self.app.config.data.update(modes=["video"], engine="external")
        self.app.need_models = mock.AsyncMock()
        self.app.prepare_engine = mock.AsyncMock()
        backend = Backend()
        self.app.service.backend = backend
        body = {"prompt": "test", "idempotency_key": "restart"}
        first = json.loads((await self.app.agents._submit(body)).text)
        app = self.reopen()
        app.need_models = mock.AsyncMock(side_effect=AssertionError("retry must not prepare another job"))
        second = json.loads((await app.agents._submit(body)).text)
        self.assertEqual(first["job"], second["job"])
        self.assertEqual(first["seed"], second["seed"])
        self.assertTrue(second["deduplicated"])

    async def test_simultaneous_api_retries_send_one_job_with_one_seed(self):
        self.app.need_models = mock.AsyncMock()
        self.app.prepare_engine = mock.AsyncMock()
        backend = Backend()
        self.app.service.backend = backend
        body = {"prompt": "test", "idempotency_key": "concurrent"}
        replies = await asyncio.gather(self.app.agents._submit(body), self.app.agents._submit(body))
        one, two = [json.loads(reply.text) for reply in replies]
        self.assertEqual(one["job"], two["job"])
        self.assertEqual(one["seed"], two["seed"])
        self.assertEqual(backend.calls, 1)

    async def test_failed_media_commit_does_not_promote_a_draft(self):
        item = {"id": "draft-failure", "file": {"filename": "image.png", "type": "output"}}
        self.app.hold_preview(item)
        with mock.patch.object(self.app.media_repo, "save", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(Exception):
                await self.app.h_preview_keep(support.Req({"id": item["id"]}))
        app = self.reopen()
        self.assertIn(item["id"], app.pending)
        self.assertEqual(app.library.items, [])

    async def test_concurrent_keep_creates_one_library_item(self):
        from services.media import Copy
        item = {"id": "double-keep", "file": {"filename": "x.png", "type": "output"}}
        self.app.hold_preview(item)

        async def archive(*args, **kwargs):
            await asyncio.sleep(0)
            return Copy()

        with mock.patch.object(self.app.media, "archive", archive):
            await asyncio.gather(self.app.h_preview_keep(support.Req({"id": item["id"]})),
                                 self.app.h_preview_keep(support.Req({"id": item["id"]})))
        self.assertEqual(len(self.app.library.items), 1)
        self.assertEqual(self.app.pending, {})
        self.assertEqual(len(self.reopen().library.items), 1)

    async def test_ffmpeg_registration_failure_recovers_a_draft_without_rendering(self):
        dest = support.new_dir("ffmpeg") / "result.mp4"
        record = self.app.begin_submission(None, None, {}, {"mode": "montage", "seed": 0}, True,
                                           backend="ffmpeg", backend_id="ffmpeg:local")
        record.prompt_id = record.backend_job_id = "ff-test"
        record.apply(JobState.QUEUED)
        self.app.jobs_repo.save(record)

        async def render(*args, **kwargs):
            dest.write_bytes(b"complete montage")

        self.app.stop_aware_thread = render
        self.app.ffmpeg_status = lambda: {"ffmpeg_path": "unused", "ffprobe_path": "unused"}
        with mock.patch.object(routes.montage.ffmpeg_mgr, "probe_media", return_value={
                "video": {"width": 1280, "height": 720}, "duration": 5}), \
             mock.patch.object(self.app.drafts_repo, "save", side_effect=sqlite3.OperationalError("disk full")):
            await self.app.run_ffmpeg_job("ff-test", dest, [], [], {}, threading.Event(), 1, True)
        self.assertTrue(dest.exists())
        self.assertEqual(self.app.jobs_repo.get(record.job_id).state, JobState.FINALIZING)
        app = self.reopen()
        self.assertEqual(app.pending_resume, [], "FFmpeg must never be resubmitted to ComfyUI")
        await app.restore_local_results()
        self.assertEqual(len(app.pending), 1)
        self.assertEqual(app.library.items, [])
        self.assertEqual(app.jobs_repo.get(record.job_id).state, JobState.SUCCEEDED)
        self.assertEqual(app.resume_local, [])

    async def test_cloud_result_failure_is_recoverable_without_new_generation(self):
        record = support.job_record("rh-cloud")
        record.backend, record.backend_job_id = "cloud", "cloud"
        job = support.job("rh-cloud", record=record, task="cloud")
        self.app.jobs["rh-cloud"] = job

        class RH:
            async def status(self, task): return "SUCCESS"
            async def outputs(self, task): return [{"fileType": "mp4", "fileUrl": "fake"}]

        class Response:
            status, content_length = 200, 4
            def __init__(self): self.content = self
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def iter_chunked(self, n): yield b"clip"

        class Session:
            def get(self, *args, **kwargs): return Response()

        self.app.session = Session()
        failed = asyncio.Event()
        real_add = self.app.library.repo.add

        def fail(item):
            failed.set()
            raise sqlite3.OperationalError("disk full")

        with mock.patch.object(self.app.library.repo, "add", fail), \
             mock.patch.object(routes.jobs, "CLOUD_RETRY", 0.01):
            task = asyncio.create_task(self.app.poll_cloud(RH(), "rh-cloud"))
            await asyncio.wait_for(failed.wait(), 2)
            self.assertEqual(self.app.jobs_repo.get(record.job_id).state, JobState.FINALIZING)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        app = self.reopen()
        restored = app.resume_cloud[0]
        app.jobs["rh-cloud"] = app.job_from_record(restored)
        app.session = mock.Mock(get=mock.Mock(side_effect=AssertionError("file already downloaded")))
        await app.poll_cloud(RH(), "rh-cloud")
        self.assertEqual(app.jobs_repo.get(record.job_id).state, JobState.SUCCEEDED)
        self.assertEqual(len(app.library.items), 1)
        self.assertNotIn("rh-cloud", app.jobs)


if __name__ == "__main__":
    unittest.main()
