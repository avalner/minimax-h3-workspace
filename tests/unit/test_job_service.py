"""JobService: durable submission, idempotency, cancellation and reconciliation, without a server.

    python tests/unit/test_job_service.py
"""

from __future__ import annotations

import asyncio
import time
import unittest

import support  # noqa: F401  (puts app/ on sys.path)

from backends.base import BackendUnavailable, Capabilities, InspectResult, SubmitRejected, SubmitResult
from domain.errors import JobConflict, JobNotFound
from domain.jobs import JobSpec, JobState
from services.jobs import JobService, spec_fingerprint
from storage.database import Database
from storage.migrations import migrate
from storage.repositories import IdempotencyRepository, JobRepository


class FakeBackend:
    name = "fake"
    capabilities = Capabilities()

    def __init__(self, submit=None, inspect=None, cancel=True):
        self.sent = []
        self.cancelled = []
        self._submit = submit
        self._inspect = inspect
        self._cancel = cancel

    async def submit(self, spec, **kwargs):
        self.sent.append(spec)
        if self._submit is not None:
            return await self._submit(spec)
        return SubmitResult(backend_job_id=f"b-{len(self.sent)}", graph={"1": {"class_type": "Test"}},
                            stages={"1": "save"})

    async def inspect(self, record):
        if self._inspect is not None:
            return await self._inspect(record)
        return InspectResult(state="unknown")

    async def cancel(self, record, force=False):
        self.cancelled.append(record.backend_job_id)
        return self._cancel

    async def outputs(self, record):
        return {}

    async def download(self, record, file_ref, destination):
        raise NotImplementedError


class ServiceCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = support.new_dir("service")
        self.db = Database(self.folder / "state.db")
        self.addCleanup(self.db.close)
        migrate(self.db, self.folder)
        self.repo = JobRepository(self.db)
        self.keys = IdempotencyRepository(self.db)
        self.backend = FakeBackend()
        self.service = self.make_service(self.backend)

    def make_service(self, backend):
        return JobService(self.repo, backend, keys=self.keys, ids=self._ids(), clock=lambda: 100.0)

    def _ids(self):
        counter = {"n": 0}

        def make():
            counter["n"] += 1
            return f"job-{counter['n']}"
        return make

    def spec(self, **overrides):
        kwargs = dict(prompt="кадр", aspect="16:9", quality="720p", seconds=5, seed=42, width=1280, height=720)
        kwargs.update(overrides)
        return JobSpec.video(**kwargs)


class SubmitTest(ServiceCase):
    async def test_the_dangerous_moment_is_written_down_before_the_post(self):
        record = self.service.begin(self.spec(), backend_id="comfy:test")
        seen = {}

        async def submit(spec):
            seen["state"] = self.repo.get(record.job_id).state
            return SubmitResult(backend_job_id="b1", graph={}, stages={})

        self.service.backend = FakeBackend(submit=submit)
        await self.service.submit(record)
        self.assertIs(seen["state"], JobState.SUBMITTING, "запись должна быть на диске до отправки")

    async def test_a_sent_job_is_queued_with_the_engine_id(self):
        record = await self.service.submit(self.service.begin(self.spec(), backend_id="comfy:test"))
        self.assertIs(record.state, JobState.QUEUED)
        self.assertEqual(record.backend_job_id, "b-1")
        self.assertEqual(record.prompt_id, "b-1", "публичный id остаётся прежним")
        self.assertEqual(record.graph, {"1": {"class_type": "Test"}})
        self.assertIs(self.repo.get(record.job_id).state, JobState.QUEUED)

    async def test_a_rejected_job_is_failed_and_nothing_is_created(self):
        async def reject(spec):
            raise SubmitRejected("нет модели")

        self.service.backend = FakeBackend(submit=reject)
        record = self.service.begin(self.spec(), backend_id="comfy:test")
        with self.assertRaises(SubmitRejected):
            await self.service.submit(record)
        self.assertIs(record.state, JobState.FAILED)
        self.assertEqual(record.error, "нет модели")
        self.assertIs(self.repo.get(record.job_id).state, JobState.FAILED)

    async def test_an_engine_that_does_not_answer_leaves_the_job_uncertain(self):
        async def down(spec):
            raise BackendUnavailable("движок не отвечает")

        self.service.backend = FakeBackend(submit=down)
        record = self.service.begin(self.spec(), backend_id="comfy:test")
        with self.assertRaises(BackendUnavailable):
            await self.service.submit(record)
        self.assertIs(record.state, JobState.UNCERTAIN, "это не провал задания")
        self.assertIs(self.repo.get(record.job_id).state, JobState.UNCERTAIN)
        with self.assertRaises(JobConflict):
            await self.service.submit(record)   # автоматической повторной отправки нет
        self.assertEqual(len(self.service.backend.sent), 1)

    async def test_a_new_job_is_the_way_to_try_again(self):
        record = self.service.begin(self.spec(), backend_id="comfy:test")
        retried = self.service.retry(record)
        self.assertNotEqual(retried.job_id, record.job_id)
        self.assertEqual(retried.spec, record.spec)
        self.assertIs(retried.state, JobState.CREATED)


class IdempotencyTest(ServiceCase):
    async def test_the_same_key_sends_once(self):
        first, deduplicated = await self.service.submit_idempotent(
            self.spec(), backend_id="comfy:test", key="k1")
        second, again = await self.service.submit_idempotent(
            self.spec(), backend_id="comfy:test", key="k1")
        self.assertFalse(deduplicated)
        self.assertTrue(again)
        self.assertEqual(first.job_id, second.job_id)
        self.assertEqual(len(self.backend.sent), 1, "повтор с тем же ключом отправил задание второй раз")

    async def test_another_payload_under_the_same_key_is_a_conflict(self):
        await self.service.submit_idempotent(self.spec(), backend_id="comfy:test", key="k1")
        with self.assertRaises(JobConflict) as caught:
            await self.service.submit_idempotent(self.spec(prompt="другой"), backend_id="comfy:test", key="k1")
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")

    async def test_two_requests_at_the_same_moment_send_once(self):
        gate = asyncio.Event()
        sent = []

        async def slow(spec):
            sent.append(spec)
            await gate.wait()
            return SubmitResult(backend_job_id="slow-1", graph={}, stages={})

        self.service.backend = FakeBackend(submit=slow)
        first = asyncio.create_task(self.service.submit_idempotent(self.spec(), backend_id="comfy:test", key="k1"))
        await support.until(lambda: sent)
        second = asyncio.create_task(self.service.submit_idempotent(self.spec(), backend_id="comfy:test", key="k1"))
        await asyncio.sleep(0.05)
        self.assertEqual(len(sent), 1, "второй запрос успел отправить своё")
        gate.set()
        one, two = await first, await second
        self.assertEqual(one[0].job_id, two[0].job_id)
        self.assertTrue(two[1])
        self.assertEqual(len(sent), 1)

    async def test_a_key_survives_a_restart(self):
        first, _ = await self.service.submit_idempotent(self.spec(), backend_id="comfy:test", key="k1")
        restarted = JobService(JobRepository(self.db), FakeBackend(), keys=IdempotencyRepository(self.db))
        again, deduplicated = await restarted.submit_idempotent(self.spec(), backend_id="comfy:test", key="k1")
        self.assertTrue(deduplicated)
        self.assertEqual(again.job_id, first.job_id)
        self.assertEqual(restarted.backend.sent, [], "после рестарта задание отправлено заново")

    async def test_two_different_keys_are_two_jobs(self):
        one, _ = await self.service.submit_idempotent(self.spec(), backend_id="comfy:test", key="k1")
        two, _ = await self.service.submit_idempotent(self.spec(), backend_id="comfy:test", key="k2")
        self.assertNotEqual(one.job_id, two.job_id)
        self.assertEqual(len(self.backend.sent), 2)

    async def test_the_fingerprint_ignores_the_display_label(self):
        one = self.spec(sub="Текст → видео")
        two = self.spec(sub="Текст → видео · агент", source="agent")
        self.assertEqual(spec_fingerprint(one), spec_fingerprint(two))


class InspectTest(ServiceCase):
    async def test_what_the_engine_said(self):
        record = await self.service.submit(self.service.begin(self.spec(), backend_id="comfy:test"))
        result = InspectResult(state="running")
        self.assertEqual(self.service.apply_inspection(record, result), "attached")
        self.assertIs(record.state, JobState.RUNNING)
        self.assertEqual(self.service.apply_inspection(record, InspectResult(state="finished")), "finalize")
        self.assertIs(record.state, JobState.FINALIZING)
        self.assertIs(self.repo.get(record.job_id).state, JobState.FINALIZING)

    async def test_a_job_the_engine_does_not_remember_is_not_resent(self):
        record = await self.service.submit(self.service.begin(self.spec(), backend_id="comfy:test"))
        self.assertEqual(self.service.apply_inspection(record, InspectResult(state="unknown")), "unknown")
        self.assertIs(record.state, JobState.QUEUED)
        self.assertEqual(len(self.backend.sent), 1, "неизвестность не повод отправлять снова")

    async def test_a_failed_inspection_fails_the_job(self):
        record = await self.service.submit(self.service.begin(self.spec(), backend_id="comfy:test"))
        self.assertEqual(self.service.apply_inspection(record, InspectResult(state="failed", error="узел упал")),
                         "failed")
        self.assertIs(record.state, JobState.FAILED)
        self.assertEqual(record.error, "узел упал")


class CancelTest(ServiceCase):
    async def test_a_queued_job_is_cancelled_at_once(self):
        record = await self.service.submit(self.service.begin(self.spec(), backend_id="comfy:test"))
        self.assertTrue(await self.service.request_cancel(record))
        self.assertIs(record.state, JobState.CANCELED)
        self.assertEqual(self.backend.cancelled, ["b-1"])
        self.assertIs(self.repo.get(record.job_id).state, JobState.CANCELED)

    async def test_a_running_job_waits_for_the_engine_to_confirm(self):
        record = await self.service.submit(self.service.begin(self.spec(), backend_id="comfy:test"))
        record.apply(JobState.RUNNING)
        await self.service.request_cancel(record)
        self.assertTrue(record.cancel_requested)
        self.assertIs(record.state, JobState.RUNNING, "отмена — просьба, пока движок не подтвердил")
        self.service.canceled(record)
        self.assertIs(record.state, JobState.CANCELED)

    async def test_a_finalizing_job_cannot_be_cancelled(self):
        record = await self.service.submit(self.service.begin(self.spec(), backend_id="comfy:test"))
        self.service.finalize(record)
        with self.assertRaises(JobConflict):
            await self.service.request_cancel(record)

    async def test_an_engine_that_refuses_the_cancel_leaves_the_request_marked(self):
        record = await self.service.submit(self.service.begin(self.spec(), backend_id="comfy:test"))
        self.service.backend = FakeBackend(cancel=False)
        self.assertFalse(await self.service.request_cancel(record))
        self.assertTrue(record.cancel_requested)
        self.assertIs(record.state, JobState.QUEUED)


class LifecycleTest(ServiceCase):
    async def test_success_is_terminal_and_written(self):
        record = await self.service.submit(self.service.begin(self.spec(), backend_id="comfy:test"))
        self.service.finalize(record)
        self.assertTrue(self.service.succeeded(record))
        self.assertFalse(self.service.succeeded(record), "второй раз — не событие")
        self.assertIs(self.repo.get(record.job_id).state, JobState.SUCCEEDED)

    async def test_a_failed_finalization_can_be_retried_in_place(self):
        record = await self.service.submit(self.service.begin(self.spec(), backend_id="comfy:test"))
        self.service.finalize(record)
        self.assertFalse(self.service.finalize(record), "финализация уже идёт")
        self.assertIs(record.state, JobState.FINALIZING)

    async def test_an_unknown_job_is_a_service_error(self):
        with self.assertRaises(JobNotFound):
            self.service.get("nope")

    async def test_unfinished_jobs_are_listed_for_a_restart(self):
        sent = await self.service.submit(self.service.begin(self.spec(), backend_id="comfy:test"))
        restarted = JobService(JobRepository(self.db), FakeBackend(), keys=IdempotencyRepository(self.db))
        self.assertEqual([r.job_id for r in restarted.active()], [sent.job_id])


if __name__ == "__main__":
    unittest.main()
