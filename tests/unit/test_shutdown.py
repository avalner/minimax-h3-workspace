"""The shutdown: loops first, state before closing, the engine last, and nothing hidden.

    python tests/unit/test_shutdown.py
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
import unittest
from unittest import mock

import support

import cloud
import application
import routes.jobs
import server
from domain.jobs import JobRecord, JobState
from storage.database import Database


async def stop_for_good(task):
    """Cancel a task that swallows cancellation until it really ends."""
    for _ in range(200):
        if task.done():
            break
        task.cancel()
        await asyncio.sleep(0.01)
    with contextlib.suppress(asyncio.CancelledError):
        await task


class FakeSession:
    def __init__(self):
        self.closed = False
        self.close_calls = 0

    async def close(self):
        self.close_calls += 1
        self.closed = True


class FakeProcess:
    def __init__(self):
        self.stops = 0

    def stop(self):
        self.stops += 1

    def alive(self):
        return True

    def log_tail(self, n=15):
        return ""


class ShutdownTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_loops_are_stopped_before_the_session_is_closed(self):
        app = support.fresh_app()
        app.session = FakeSession()
        closed_when_cancelled = []

        async def loop():
            try:
                while True:
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                closed_when_cancelled.append(app.session.closed)
                raise

        app.tasks.spawn("loop", loop())
        await asyncio.sleep(0)
        await app.stop(None)
        self.assertEqual(closed_when_cancelled, [False], "задача ещё работала с закрытой сессией")
        self.assertTrue(app.session.closed)
        self.assertTrue(app.tasks.closing, "после остановки новые задачи всё ещё стартуют")
        self.assertEqual(app.last_shutdown["unfinished"], [])

    async def test_the_recovery_state_is_written_before_the_database_is_closed(self):
        app = support.fresh_app()
        app.session = FakeSession()
        app.jobs["p1"] = support.job("p1", record=support.job_record("p1"))
        await app.stop(None)
        database = Database(server.STATE_DB)   # the app closed its own connection: read it fresh
        try:
            row = database.one("SELECT state FROM jobs WHERE prompt_id='p1'")
        finally:
            database.close()
        self.assertIsNotNone(row, "очередь не сохранена перед закрытием базы")
        self.assertEqual(row["state"], "queued")

    async def test_a_second_stop_does_nothing(self):
        app = support.fresh_app()
        app.session = FakeSession()
        await app.stop(None)
        await app.stop(None)
        self.assertEqual(app.session.close_calls, 1)

    async def test_an_operation_that_does_not_fit_the_budget_is_reported(self):
        app = support.fresh_app()
        app.session = FakeSession()

        async def stubborn():
            swallowed = 0
            while True:
                try:
                    await asyncio.sleep(5)
                except asyncio.CancelledError:
                    swallowed += 1
                    if swallowed >= 3:
                        raise

        task = app.tasks.spawn("stubborn", stubborn())
        await asyncio.sleep(0)
        with mock.patch.object(application, "SHUTDOWN_BUDGET", 0.2):
            await app.stop(None)
        self.assertEqual(app.last_shutdown["unfinished"], ["stubborn"])
        self.assertTrue(any("stubborn" in text for text in app.recent_errors),
                        "незавершённая операция не попала в диагностику")
        await stop_for_good(task)

    async def test_the_engine_the_app_started_is_stopped_at_exit(self):
        app = support.fresh_app()
        app.session = FakeSession()
        process = FakeProcess()
        app.engines.process = process
        await app.stop(None)
        self.assertEqual(process.stops, 1, "наш ComfyUI остался работать после закрытия")

    async def test_the_users_own_engine_is_not_stopped(self):
        app = support.fresh_app()
        app.session = FakeSession()
        external = FakeProcess()
        app.engines.external = external   # started again by us, but it is the user's ComfyUI
        await app.stop(None)
        self.assertEqual(external.stops, 0)

    async def test_a_worker_thread_stops_before_the_caller_is_told(self):
        app = support.fresh_app()
        cancel = threading.Event()
        started = threading.Event()
        order = []

        def worker():
            started.set()
            cancel.wait(5)   # the worker answers the event by itself, as FFmpeg does
            order.append("worker stopped")

        task = asyncio.create_task(app.stop_aware_thread(worker, cancel=cancel))
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        order.append("caller told")
        self.assertEqual(order, ["worker stopped", "caller told"],
                         "вызывающий узнал об отмене раньше, чем встал поток: файлы уберут под ним")


class CloudResumeTest(unittest.IsolatedAsyncioTestCase):
    def app_with_cloud_record(self, state=JobState.RUNNING):
        app = support.fresh_app()
        record = JobRecord(job_id="j-cloud", prompt_id="rh-t1", backend="cloud",
                           backend_id="cloud:runninghub", backend_job_id="t1", state=state,
                           created_at=time.time(), updated_at=time.time(),
                           legacy={"meta": {"mode": "video", "prompt": "облако"}})
        app.jobs_repo.save(record)
        app.pending_resume = app.load_resume_queue()
        return app

    async def test_a_cloud_job_is_watched_after_a_restart_and_never_sent_to_comfy(self):
        app = self.app_with_cloud_record()
        self.assertEqual(app.pending_resume, [], "облачное задание предложено восстановить заново")
        self.assertEqual([r.job_id for r in app.resume_cloud], ["j-cloud"])
        app.config.data.update(rh_enabled=True, rh_key="ключ")
        seen = []

        async def fake_poll(rh, pid):
            seen.append(pid)

        app.poll_cloud = fake_poll
        app.watch_cloud_jobs()
        self.assertIn("rh-t1", app.jobs)
        self.assertEqual(app.jobs["rh-t1"]["task"], "t1", "задание потеряло id удалённой задачи")
        self.assertEqual(app.jobs["rh-t1"]["state"], "running", "состояние задания сброшено")
        await asyncio.sleep(0)
        self.assertEqual(seen, ["rh-t1"], "наблюдение за облачным заданием не началось")

    async def test_switching_the_cloud_off_keeps_the_job_instead_of_losing_it(self):
        app = self.app_with_cloud_record()
        app.config.data.update(rh_enabled=False)
        app.watch_cloud_jobs()
        self.assertEqual([r.job_id for r in app.resume_cloud], ["j-cloud"])
        self.assertEqual(app.jobs, {})
        self.assertTrue(any("облак" in text for text in app.recent_errors))

    async def test_a_network_blink_does_not_make_a_remote_job_failed(self):
        app = support.fresh_app()
        app.jobs["rh-t"] = {"prompt_id": "rh-t", "state": "queued", "stages": {}, "meta": {},
                            "task": "t", "record": None}
        calls = []

        class FlakyCloud:
            async def status(self, task):
                calls.append(task)
                if len(calls) <= 2:
                    raise cloud.CloudError("RunningHub недоступен: нет сети")
                app.jobs.pop("rh-t", None)   # the watch ends: the task left the queue
                return "RUNNING"

        with mock.patch.object(routes.jobs, "CLOUD_RETRY", 0):
            await app.poll_cloud(FlakyCloud(), "rh-t")
        self.assertEqual(len(calls), 3, "обрыв связи не повторил вопрос")
        self.assertEqual(app.recent_errors, [], "обрыв сети записан как ошибка задания")

    async def test_a_remote_job_that_never_answers_is_reported(self):
        app = support.fresh_app()
        events = support.capture_broadcast(app)
        app.jobs["rh-t"] = {"prompt_id": "rh-t", "state": "queued", "stages": {}, "meta": {},
                            "task": "t", "record": None}

        class DeadCloud:
            async def status(self, task):
                raise cloud.CloudError("RunningHub недоступен: нет сети")

        with mock.patch.object(routes.jobs, "CLOUD_RETRY", 0):
            await app.poll_cloud(DeadCloud(), "rh-t")
        self.assertNotIn("rh-t", app.jobs, "задание навсегда осталось в очереди")
        self.assertEqual([e["type"] for e in events if e["type"] == "error"], ["error"])


if __name__ == "__main__":
    unittest.main()
