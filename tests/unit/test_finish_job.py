"""finish_job: the result is registered once, and the job stays visible until it is.

    python tests/unit/test_finish_job.py
"""

from __future__ import annotations

import asyncio
import sqlite3
import unittest
from unittest import mock

import support
from support import VIDEO_OUTPUT

import server
from domain.jobs import JobState


class FinishJobTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = support.fresh_app()
        self.events = support.capture_broadcast(self.app)

        async def outputs(pid):
            return VIDEO_OUTPUT

        self.app.job_outputs = outputs

    async def test_the_result_is_registered_once(self):
        job = support.job("p1")
        self.app.jobs["p1"] = job
        await self.app.finish_job(job)
        self.assertEqual(len(self.app.library.items), 1)
        self.assertEqual(list(self.app.jobs), [])
        self.assertEqual(sum(1 for e in self.events if e["type"] == "done"), 1)

    async def test_two_events_for_one_job_give_one_item(self):
        job = support.job("p1")
        self.app.jobs["p1"] = job
        await asyncio.gather(self.app.finish_job(job), self.app.finish_job(job))
        self.assertEqual(len(self.app.library.items), 1)
        self.assertEqual(sum(1 for e in self.events if e["type"] == "done"), 1)

    async def test_the_job_stays_visible_while_the_result_is_written(self):
        release = asyncio.Event()

        async def slow(pid):
            await release.wait()
            return VIDEO_OUTPUT

        self.app.job_outputs = slow
        job = support.job("p1", record=support.job_record("p1"))
        self.app.jobs["p1"] = job
        task = asyncio.create_task(self.app.finish_job(job))
        await support.until(lambda: self.app.jobs.get("p1", {}).get("finalizing"))
        self.assertIn("p1", self.app.jobs, "задание исчезло из очереди до регистрации результата")
        saved = self.app.jobs_repo.by_prompt("p1")
        self.assertIsNotNone(saved, "рестарт в этот момент не нашёл бы задание")
        self.assertIs(saved.state, JobState.FINALIZING, "рестарт в этот момент повторил бы генерацию")
        release.set()
        await task
        self.assertNotIn("p1", self.app.jobs)
        saved = self.app.jobs_repo.by_prompt("p1")
        self.assertIsNotNone(saved, "исход задания не сохранился")
        self.assertIs(saved.state, JobState.SUCCEEDED)
        self.assertEqual(self.app.jobs_repo.active(), [], "завершённое задание осталось в очереди")
        self.assertEqual(len(self.app.library.items), 1)

    async def test_a_failed_registration_keeps_the_job_for_a_retry(self):
        def full(*args, **kwargs):
            raise sqlite3.OperationalError("disk I/O error")

        job = support.job("p1", record=support.job_record("p1"))
        self.app.jobs["p1"] = job
        with mock.patch.object(self.app.library.repo, "add", full):
            await self.app.finish_job(job)   # does not raise: the clip is not lost because a write failed
        self.assertIn("p1", self.app.jobs, "задание пропало вместе с несозданной записью")
        self.assertFalse(self.app.jobs["p1"]["finalizing"], "повторная попытка была бы заблокирована")
        self.assertTrue(any("не сохранился" in text for text in self.app.recent_errors))
        self.assertIsNotNone(self.app.jobs_repo.by_prompt("p1"), "повторять будет не с чего")
        await self.app.finish_job(job)   # the engine still has the result: no new generation
        self.assertEqual(len(self.app.library.items), 1)
        self.assertNotIn("p1", self.app.jobs)

    async def test_no_files_is_an_error_and_the_job_leaves(self):
        async def empty(pid):
            return {}

        self.app.job_outputs = empty
        job = support.job("p1")
        self.app.jobs["p1"] = job
        await self.app.finish_job(job)
        self.assertEqual(self.app.library.items, [])
        self.assertNotIn("p1", self.app.jobs)
        errors = [e for e in self.events if e["type"] == "error"]
        self.assertEqual(errors[0]["prompt_id"], "p1")

    async def test_cancel_does_not_interrupt_a_job_that_is_finalizing(self):
        job = support.job("p1", finalizing=True)
        self.app.jobs["p1"] = job

        async def no_comfy(*args, **kwargs):
            raise AssertionError("движок прерван, хотя результат уже есть")

        self.app.comfy_json = no_comfy
        await self.app.cancel_job("p1")
        self.assertIn("p1", self.app.jobs)
        self.assertTrue(any(e["type"] == "notice" for e in self.events))

    async def test_a_replaced_engine_does_not_drop_a_job_whose_result_is_being_written(self):
        self.app.jobs["p1"] = support.job("p1", finalizing=True)
        self.app.jobs["p2"] = support.job("p2", state="running")
        await self.app.drop_engine_jobs("движок перезапущен")
        self.assertIn("p1", self.app.jobs, "задание, результат которого уже готов, объявлено потерянным")
        self.assertNotIn("p2", self.app.jobs)


if __name__ == "__main__":
    unittest.main()
