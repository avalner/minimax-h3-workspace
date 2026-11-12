"""The rules of a job's life, checked without a webserver, a ComfyUI or a video card.

    python tests/unit/test_job_states.py
"""

from __future__ import annotations

import json
import unittest

import support  # noqa: F401  (puts app/ on sys.path)

from domain.errors import InvalidTransition, JobConflict
from domain.jobs import (TRANSITIONS, JobRecord, JobSpec, JobState, ResultPolicy, public_state)


def spec():
    return JobSpec.video(prompt="кадр", aspect="16:9", quality="720p", seconds=5, seed=7,
                         width=1280, height=720)


class FlowTest(unittest.TestCase):
    def test_a_job_walks_the_whole_path(self):
        record = JobRecord(job_id="j1", spec=spec(), created_at=100.0, updated_at=100.0)
        for target in (JobState.SUBMITTING, JobState.QUEUED, JobState.RUNNING, JobState.FINALIZING,
                       JobState.SUCCEEDED):
            self.assertTrue(record.apply(target, now=101.0))
            self.assertEqual(record.state, target)
        self.assertEqual(record.version, 6, "версия записи должна меняться на каждом переходе")
        self.assertEqual(record.updated_at, 101.0)

    def test_a_final_outcome_is_never_rewritten_by_a_late_event(self):
        record = JobRecord(job_id="j2", state=JobState.FAILED)
        for target in (JobState.QUEUED, JobState.RUNNING, JobState.FINALIZING, JobState.SUCCEEDED):
            self.assertFalse(record.can(target))
            with self.assertRaises(InvalidTransition):
                record.apply(target)
        self.assertEqual(record.state, JobState.FAILED)

    def test_success_does_not_turn_into_failure(self):
        record = JobRecord(job_id="j3", state=JobState.SUCCEEDED)
        with self.assertRaises(InvalidTransition):
            record.fail("поздняя ошибка")
        self.assertEqual(record.state, JobState.SUCCEEDED)
        self.assertIsNone(record.error)

    def test_a_finalizing_job_is_not_cancelled_as_a_running_one(self):
        record = JobRecord(job_id="j4", state=JobState.FINALIZING)
        self.assertFalse(record.can(JobState.CANCELED))
        with self.assertRaises(JobConflict):
            record.request_cancel()
        with self.assertRaises(InvalidTransition):
            record.cancel_confirmed()

    def test_a_running_job_is_cancelled_only_when_the_engine_confirms(self):
        record = JobRecord(job_id="j5", state=JobState.RUNNING)
        self.assertTrue(record.request_cancel(now=5.0))
        self.assertEqual(record.state, JobState.RUNNING, "отмена — просьба, а не смена состояния")
        self.assertFalse(record.request_cancel(), "второе нажатие ничего не добавляет")
        self.assertTrue(record.cancel_confirmed(now=6.0))
        self.assertEqual(record.state, JobState.CANCELED)
        self.assertEqual(public_state(record.state), "interrupted")

    def test_an_engine_that_does_not_answer_is_not_a_failure(self):
        record = JobRecord(job_id="j6", state=JobState.SUBMITTING)
        record.apply(JobState.UNCERTAIN)
        self.assertEqual(record.state, JobState.UNCERTAIN)
        self.assertNotEqual(record.state, JobState.FAILED, "молчащий движок — не провал задания")
        self.assertTrue(record.can(JobState.QUEUED))
        self.assertTrue(record.can(JobState.CANCELED))

    def test_an_event_for_a_finished_job_can_be_checked_without_an_exception(self):
        record = JobRecord(job_id="j7", state=JobState.CANCELED)
        self.assertFalse(record.can(JobState.RUNNING))   # the websocket loop asks, and stays alive

    def test_progress_is_not_a_state_change(self):
        record = JobRecord(job_id="j8", state=JobState.RUNNING, version=4)
        record.set_progress(stage="sample", step=3, step_max=8)
        self.assertEqual(record.progress["step"], 3)
        self.assertEqual(record.version, 4, "шаг сэмплера — не событие для записи в базу")

    def test_the_identifiers_stay_apart(self):
        record = JobRecord(job_id="internal", prompt_id="public", backend="cloud", backend_id="runninghub",
                           backend_job_id="task-9")
        self.assertNotEqual(record.job_id, record.prompt_id)
        self.assertNotEqual(record.prompt_id, record.backend_job_id)
        self.assertEqual(record.to_dict()["backend_job_id"], "task-9")

    def test_the_record_survives_serialization(self):
        record = JobRecord(job_id="j9", spec=spec(), state=JobState.FINALIZING, version=5,
                           backend="comfy", backend_id="http://127.0.0.1:8188",
                           backend_job_id="p-1", prompt_id="p-1", created_at=1.0, updated_at=2.0)
        record.set_progress(stage="save", step=7, step_max=8)
        again = JobRecord.from_dict(json.loads(json.dumps(record.to_dict())))
        self.assertEqual(again.to_dict(), record.to_dict())

    def test_a_jump_over_the_engine_is_refused(self):
        record = JobRecord(job_id="j10")
        with self.assertRaises(InvalidTransition):
            record.apply(JobState.SUCCEEDED)   # created → succeeded is not a path

    def test_every_state_has_a_public_string(self):
        allowed = {"queued", "running", "done", "error", "interrupted"}
        for state in JobState:
            self.assertIn(public_state(state), allowed)
        self.assertEqual(public_state(JobState.FINALIZING), "running",
                         "окно и агент не должны увидеть новое значение состояния")

    def test_terminal_states_have_no_way_out(self):
        for state in (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELED):
            self.assertTrue(state.terminal)
            self.assertEqual(TRANSITIONS[state], set())

    def test_a_draft_result_policy_is_a_decision(self):
        record = JobRecord(job_id="j11", spec=spec())
        self.assertEqual(record.spec.result_policy, ResultPolicy.LIBRARY)


if __name__ == "__main__":
    unittest.main()
