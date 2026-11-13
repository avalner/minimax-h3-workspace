"""`/api/job/{id}` gives the window explicit parameters for a job, whatever year the record is from.

The details window and «повторить» build the form from this answer: if the model is only inside the
caption, the answer says the value is a guess instead of pretending it is known.

    python tests/unit/test_job_details.py
"""

from __future__ import annotations

import json
import time
import unittest

import support

import server


class _Match:
    def __init__(self, pid):
        self.match_info = {"pid": pid}


class JobDetailsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = support.fresh_app()

    async def details(self, pid):
        return json.loads((await self.app.h_job(_Match(pid))).text)

    async def test_a_live_job_with_parameters_answers_them_directly(self):
        job = support.job("p1")
        job["meta"].update(model="fused", acceleration="normal", sub="Текст → видео · Fused Turbo")
        self.app.jobs["p1"] = job
        body = await self.details("p1")
        self.assertEqual((body["meta"]["model"], body["meta"]["acceleration"]), ("fused", "normal"))
        self.assertEqual(body["meta"]["params_source"], "record")

    async def test_an_old_live_job_is_read_from_its_caption(self):
        job = support.job("p2")
        job["meta"].update(sub="Изображение → видео · быстрый черновик")
        job["meta"].pop("model", None)
        self.app.jobs["p2"] = job
        body = await self.details("p2")
        self.assertEqual((body["meta"]["model"], body["meta"]["acceleration"]), ("fast", "sla"))
        self.assertEqual(body["meta"]["params_source"], "legacy")

    async def test_a_finished_job_of_an_old_library_record_is_normalised(self):
        self.app.library.add({"id": "old1", "prompt_id": "p-old", "kind": "video", "mode": "video",
                              "date": "2026-11-11T10:00:00", "fav": False,
                              "sub": "Кадр → кадр · Fused Turbo",
                              "file": {"filename": "clip.mp4", "subfolder": "", "type": "output"}})
        body = await self.details("p-old")
        self.assertEqual(body["state"], "done")
        self.assertEqual((body["meta"]["model"], body["meta"]["params_source"]), ("fused", "legacy"))

    async def test_a_job_of_an_earlier_run_is_normalised_too(self):
        from domain.jobs import JobRecord, JobState
        record = JobRecord(job_id="j-wait", prompt_id="p-wait", state=JobState.QUEUED,
                           created_at=time.time(), updated_at=time.time(),
                           legacy={"meta": {"mode": "video", "sub": "Текст → видео · Fused Turbo"}})
        self.app.jobs_repo.save(record)
        self.app.pending_resume = [record]
        body = await self.details("p-wait")
        self.assertEqual(body["state"], "queued")
        self.assertEqual((body["meta"]["model"], body["meta"]["params_source"]), ("fused", "legacy"))

    async def test_a_record_with_nothing_known_is_not_presented_as_fact(self):
        job = support.job("p3")
        job["meta"] = {"mode": "video", "prompt": "кадр", "seed": 1}
        self.app.jobs["p3"] = job
        body = await self.details("p3")
        self.assertEqual(body["meta"]["params_source"], "unknown")


if __name__ == "__main__":
    unittest.main()
