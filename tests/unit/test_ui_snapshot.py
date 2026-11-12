"""What a window receives first after (re)connecting: the queue, the outcomes and their results.

    python tests/unit/test_ui_snapshot.py
"""

from __future__ import annotations

import time
import unittest

import support

from domain.jobs import JobRecord, JobState


class SnapshotTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = support.fresh_app()

    def finished_item(self, media_id="m1", pid="p-done", **extra):
        item = {"id": media_id, "prompt_id": pid, "kind": "video", "mode": "video",
                "date": "2026-11-12T10:00:00", "fav": False,
                "file": {"filename": "clip.mp4", "subfolder": "", "type": "output"}}
        item.update(extra)
        self.app.library.add(item)
        return item

    async def test_a_reconnect_gets_the_live_queue_the_outcome_and_its_result(self):
        self.app.jobs["p-run"] = support.job("p-run", state="running", prompt_id="p-run",
                                             record=support.job_record("p-run"))
        self.app.jobs["p-run"]["stage"] = "sample"
        self.app.jobs["p-run"]["step"], self.app.jobs["p-run"]["step_max"] = 3, 8
        self.app.recent["p-done"] = {"state": "done", "at": "2026-11-12T10:00:00", "message": ""}
        item = self.finished_item()
        snapshot = self.app.ui_snapshot()
        self.assertEqual(snapshot["type"], "hello")
        self.assertEqual([j["prompt_id"] for j in snapshot["jobs"]], ["p-run"])
        self.assertEqual((snapshot["jobs"][0]["state"], snapshot["jobs"][0]["stage"]), ("running", "sample"))
        self.assertEqual(snapshot["recent"][0]["prompt_id"], "p-done")
        self.assertEqual([r["id"] for r in snapshot["results"]], [item["id"]])
        self.assertNotIn("file", snapshot["results"][0], "снимок не должен отдавать внутренние ссылки на файл")

    async def test_a_draft_of_a_finished_picture_is_in_the_snapshot(self):
        self.app.recent["p-draft"] = {"state": "done", "at": "2026-11-12T10:00:00", "message": ""}
        self.app.hold_preview({"id": "d1", "prompt_id": "p-draft", "kind": "image", "mode": "krea",
                               "date": "2026-11-12T10:00:00", "preview": True,
                               "file": {"filename": "pic.png", "subfolder": "", "type": "output"}})
        snapshot = self.app.ui_snapshot()
        self.assertEqual([r["id"] for r in snapshot["results"]], ["d1"])
        self.assertTrue(snapshot["results"][0]["preview"])

    async def test_an_old_result_is_not_pushed_again(self):
        self.finished_item(media_id="old", pid="p-old")   # not in `recent`: the window does not need it
        snapshot = self.app.ui_snapshot()
        self.assertEqual(snapshot["results"], [])

    async def test_the_engine_state_travels_with_the_snapshot(self):
        self.app.comfy_online = True
        self.assertTrue(self.app.ui_snapshot()["engine"])

    async def test_a_finalizing_job_is_shown_as_running_with_its_mark(self):
        record = JobRecord(job_id="j1", prompt_id="p-fin", state=JobState.FINALIZING,
                           created_at=time.time(), updated_at=time.time())
        job = support.job("p-fin")
        job.update(finalizing=True, record=record)
        self.app.jobs["p-fin"] = job
        entry = self.app.ui_snapshot()["jobs"][0]
        self.assertEqual(entry["state"], "running")
        self.assertTrue(entry["finalizing"])


if __name__ == "__main__":
    unittest.main()
