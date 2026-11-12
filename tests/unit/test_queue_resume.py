"""Bringing back the unfinished jobs of an earlier run: one broken record must not lose the others.

    python tests/unit/test_queue_resume.py
"""

from __future__ import annotations

import json
import sqlite3
import time
import unittest
from unittest import mock

import support
from support import VIDEO_OUTPUT

import server
from domain.jobs import JobRecord, JobState


def empty_queue():
    return 200, {"queue_running": [], "queue_pending": []}


class QueueResumeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = support.fresh_app()
        self.events = support.capture_broadcast(self.app)

    def seed(self, *saved):
        """Leave in the database the records an earlier run (or the import) would have left."""
        for record in saved:
            self.app.jobs_repo.save(JobRecord(
                job_id="j-" + record["pid"], prompt_id=record["pid"], backend="comfy",
                backend_id=self.app.backend_identity(),    # the same engine the settings point at now
                backend_job_id=record["pid"], state=JobState.QUEUED, created_at=time.time(),
                updated_at=time.time(), graph=record["graph"],
                legacy={"stages": record.get("stages") or {}, "meta": record.get("meta") or {},
                        "titles": {}, "last_output": False, "preview": record.get("preview", False)}))
        self.app.pending_resume = self.app.load_resume_queue()

    def waiting(self):
        return [record.prompt_id for record in self.app.pending_resume]

    def only_get_queue(self):
        async def comfy(method, path, **kwargs):
            if path == "/queue":
                return empty_queue()
            raise AssertionError("неожиданный запрос к движку: " + path)
        self.app.comfy_json = comfy

    async def test_nothing_to_resume(self):
        body = json.loads((await self.app.h_queue_resume(None)).text)
        self.assertEqual(body, {"jobs": [], "restored": 0, "kept": 0, "unreachable": False})

    async def test_an_engine_that_does_not_answer_keeps_the_whole_queue(self):
        self.seed(support.saved("old1"), support.saved("old2"))

        async def down(*args, **kwargs):
            raise ConnectionError("движок не запущен")

        async def no_submit(*args, **kwargs):
            raise AssertionError("задание отправлено в движок, который не ответил")

        self.app.comfy_json = down
        self.app.queue = no_submit
        body = json.loads((await self.app.h_queue_resume(None)).text)
        self.assertTrue(body["unreachable"])
        self.assertEqual(body["jobs"], [])
        self.assertEqual(self.waiting(), ["old1", "old2"], "очередь потеряна из-за молчащего движка")
        self.assertEqual(self.app.jobs_repo.count(), 2)

    async def test_one_broken_record_does_not_lose_the_rest(self):
        self.seed(support.saved("old1", meta={"mode": "video", "prompt": "первое", "seed": 1}),
                  support.saved("old2", meta={"mode": "video", "prompt": "второе", "seed": 1}))
        self.only_get_queue()
        calls = []

        async def queue(graph, stages, meta, last_output=False, titles=None, preview=False):
            calls.append(meta.get("prompt"))
            if meta.get("prompt") == "первое":
                raise RuntimeError("ComfyUI отклонил граф")
            from aiohttp import web
            return web.json_response({"prompt_id": "new2"})

        self.app.queue = queue
        body = json.loads((await self.app.h_queue_resume(None)).text)
        self.assertEqual(len(calls), 2, "после отказа одного задания остальные не были отправлены")
        self.assertEqual([j["prompt_id"] for j in body["jobs"]], ["new2"])
        self.assertEqual(body["kept"], 1)
        self.assertEqual(self.waiting(), ["old1"], "неудачный элемент должен остаться на следующий раз")
        self.assertIsNotNone(self.app.jobs_repo.by_prompt("old1"))
        self.assertIsNone(self.app.jobs_repo.by_prompt("old2"), "отправленный элемент остался дублем в таблице")

    async def test_a_live_job_is_watched_not_sent_again(self):
        self.seed(support.saved("old1"))

        async def comfy(method, path, **kwargs):
            if path == "/queue":
                return 200, {"queue_running": [["7", "old1", {}, {}, {}]], "queue_pending": []}
            raise AssertionError(path)

        async def no_submit(*args, **kwargs):
            raise AssertionError("задание, которое движок ещё держит, отправлено повторно")

        self.app.comfy_json = comfy
        self.app.queue = no_submit
        body = json.loads((await self.app.h_queue_resume(None)).text)
        self.assertEqual(body["jobs"][0]["state"], "running")
        self.assertEqual(self.app.jobs["old1"]["state"], "running", "живое задание не взято под наблюдение")
        self.assertEqual(self.waiting(), [])
        self.assertEqual(self.app.jobs_repo.by_prompt("old1").state, JobState.RUNNING)

    async def test_a_finished_job_is_taken_from_the_history(self):
        self.seed(support.saved("old1"))

        async def comfy(method, path, **kwargs):
            if path == "/queue":
                return empty_queue()
            if path == "/history/old1":
                return 200, {"old1": {"outputs": VIDEO_OUTPUT, "status": {"status_str": "success"}}}
            raise AssertionError(path)

        async def no_submit(*args, **kwargs):
            raise AssertionError("готовый результат отправлен на повторную генерацию")

        self.app.comfy_json = comfy
        self.app.queue = no_submit
        body = json.loads((await self.app.h_queue_resume(None)).text)
        self.assertEqual(body["jobs"][0]["state"], "done")
        self.assertEqual(len(self.app.library.items), 1, "результат прошлого запуска не попал в Библиотеку")
        self.assertEqual(self.waiting(), [])
        self.assertIsNone(self.app.jobs_repo.by_prompt("old1"), "завершённое задание осталось в очереди")

    async def test_a_finished_job_whose_result_cannot_be_written_stays_for_the_next_start(self):
        self.seed(support.saved("old1"))

        async def comfy(method, path, **kwargs):
            if path == "/queue":
                return empty_queue()
            if path == "/history/old1":
                return 200, {"old1": {"outputs": VIDEO_OUTPUT, "status": {"status_str": "success"}}}
            raise AssertionError(path)

        self.app.comfy_json = comfy

        def full(item):
            raise sqlite3.OperationalError("disk I/O error")

        with mock.patch.object(self.app.library, "add", full):
            body = json.loads((await self.app.h_queue_resume(None)).text)
        self.assertEqual(body["kept"], 1)
        self.assertEqual(body["jobs"][0]["state"], "queued")
        self.assertEqual(self.waiting(), ["old1"], "результат не записан, а запись потеряна")
        self.assertEqual(self.app.library.items, [])
        self.assertNotIn("old1", self.app.jobs)

    async def test_a_failed_job_is_reported_not_rendered_again(self):
        self.seed(support.saved("old1"))

        async def comfy(method, path, **kwargs):
            if path == "/queue":
                return empty_queue()
            if path == "/history/old1":
                return 200, {"old1": {"outputs": {}, "status": {"status_str": "error"}}}
            raise AssertionError(path)

        async def no_submit(*args, **kwargs):
            raise AssertionError("задание, упавшее в движке, отправлено заново без спроса")

        self.app.comfy_json = comfy
        self.app.queue = no_submit
        body = json.loads((await self.app.h_queue_resume(None)).text)
        self.assertEqual(body["jobs"][0]["state"], "error")
        self.assertIn("old1", self.app.recent)
        self.assertEqual(self.waiting(), [])

    async def test_a_job_of_an_earlier_run_is_submitted_again_when_the_engine_forgot_it(self):
        self.seed(support.saved("old1"))
        self.only_get_queue()

        async def queue(graph, stages, meta, last_output=False, titles=None, preview=False):
            from aiohttp import web
            return web.json_response({"prompt_id": "new1", "seed": 1})

        self.app.queue = queue
        body = json.loads((await self.app.h_queue_resume(None)).text)
        self.assertEqual(body["jobs"][0]["prompt_id"], "new1")
        self.assertEqual(body["jobs"][0]["state"], "queued")
        self.assertEqual(self.waiting(), [])

    async def test_a_rejected_graph_stays_in_the_queue(self):
        self.seed(support.saved("old1"))
        self.only_get_queue()

        async def queue(graph, stages, meta, last_output=False, titles=None, preview=False):
            from aiohttp import web
            return web.json_response({"error": "нет модели"}, status=400)

        self.app.queue = queue
        body = json.loads((await self.app.h_queue_resume(None)).text)
        self.assertEqual(body["kept"], 1)
        self.assertEqual(self.waiting(), ["old1"])

    async def test_a_job_of_another_engine_is_not_sent_to_this_one(self):
        record = JobRecord(job_id="j-old", prompt_id="old1", backend="comfy",
                           backend_id="comfy:http://192.168.1.5:8188",   # a rented server, not this one
                           backend_job_id="old1", state=JobState.QUEUED, created_at=time.time(),
                           updated_at=time.time(), graph={"1": {}},
                           legacy={"stages": {}, "meta": {"mode": "video", "prompt": "x"}})
        self.app.jobs_repo.save(record)
        self.app.pending_resume = self.app.load_resume_queue()
        self.only_get_queue()

        async def no_submit(*args, **kwargs):
            raise AssertionError("задание отправлено не на тот движок")

        self.app.queue = no_submit
        body = json.loads((await self.app.h_queue_resume(None)).text)
        self.assertEqual(body["kept"], 1)
        self.assertEqual(self.waiting(), ["old1"], "задание должно ждать свой движок, а не уходить на другой")

    async def test_discarding_forgets_the_records(self):
        self.seed(support.saved("old1"), support.saved("old2"))
        await self.app.h_queue_discard(None)
        self.assertEqual(self.waiting(), [])
        self.assertEqual(self.app.jobs_repo.count(), 0)


if __name__ == "__main__":
    unittest.main()
