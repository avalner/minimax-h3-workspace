"""What the agent sees about a job: the public states stay, the internal phase is additive.

A job that is finalizing is not a 404 and not done; a job of an earlier run that waits for
«восстановить» is queued, not forgotten. These are the states the plan asks not to break.

    python tests/unit/test_agent_view.py
"""

from __future__ import annotations

import time
import types
import unittest

import support

import agentapi
from domain.jobs import JobRecord, JobState


class JobViewTest(unittest.TestCase):
    def setUp(self):
        app = types.SimpleNamespace()
        app.config = types.SimpleNamespace(get=lambda key, default=None: default)
        app.port = 0
        app.library = types.SimpleNamespace(items=[])
        app.pending_resume = []
        app.jobs = {}
        app.file_path = lambda item: None
        self.app = app
        self.api = agentapi.AgentAPI(app, support.new_dir("agentview"), 25.0)

    def job(self, pid="p1", **extra):
        job = {"prompt_id": pid, "meta": {"mode": "video", "prompt": "кадр", "seed": 1},
               "state": "running", "stage": "save", "t0": time.time() - 5}
        job.update(extra)
        return job

    def test_a_finalizing_job_is_running_and_names_its_phase(self):
        record = JobRecord(job_id="j1", prompt_id="p1", state=JobState.FINALIZING)
        self.app.jobs["p1"] = self.job("p1", finalizing=True, record=record)
        view = self.api.job_view("p1")
        self.assertEqual(view["state"], "running", "финализация не должна выглядеть как done или 404")
        self.assertEqual(view["phase"], "finalizing")
        self.assertTrue(view["finalizing"])

    def test_a_submitting_job_is_queued(self):
        record = JobRecord(job_id="j2", prompt_id="p2", state=JobState.SUBMITTING)
        self.app.jobs["p2"] = self.job("p2", state="queued", record=record)
        view = self.api.job_view("p2")
        self.assertEqual((view["state"], view["phase"]), ("queued", "submitting"))

    def test_a_job_of_an_earlier_run_is_queued_and_recovering(self):
        record = JobRecord(job_id="j3", prompt_id="p3", state=JobState.QUEUED,
                           legacy={"meta": {"mode": "video", "prompt": "из прошлого запуска"}})
        self.app.pending_resume = [record]
        view = self.api.job_view("p3")
        self.assertIsNotNone(view, "задание прошлого запуска пропало из ответа агента")
        self.assertEqual(view["state"], "queued")
        self.assertTrue(view["recovering"])
        self.assertEqual(view["progress"]["stage"], "recovering")
        self.assertEqual(view["prompt"], "из прошлого запуска")

    def test_an_unknown_job_is_none(self):
        self.assertIsNone(self.api.job_view("nope"))

    def test_a_finished_job_keeps_the_old_shape(self):
        self.app.job_outcome = lambda pid: ({  # the record of a job that ended (the app reads it)
            "state": "error", "at": "2026-11-12T10:00:00", "message": "нет модели",
            "meta": {}, "created": 0.0, "phase": "failed"} if pid == "p4" else None)
        view = self.api.job_view("p4")
        self.assertEqual(view["state"], "failed")
        self.assertEqual(view["error"], {"code": "JOB_FAILED", "message": "нет модели"})


if __name__ == "__main__":
    unittest.main()
