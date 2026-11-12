"""The server really uses the typed record: a job is born with a spec, walks the phases and its
result becomes a MediaRecord — while the window and the agent client keep seeing the old words.

    python tests/unit/test_job_record_wiring.py
"""

from __future__ import annotations

import asyncio
import unittest

import support
from support import VIDEO_OUTPUT

import server
from domain.jobs import JobRecord, JobSpec, JobState
from domain.media import MediaState

GRAPH = {"1": {"class_type": "Test", "inputs": {}}}


class WiringTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = support.fresh_app()
        self.events = support.capture_broadcast(self.app)
        # one job already in the queue: queue() then skips «free the models» (it waits 1.5 s)
        self.app.jobs["warm"] = support.job("warm")

        async def outputs(pid):
            return VIDEO_OUTPUT

        self.app.job_outputs = outputs

    async def comfy(self, method, path, **kwargs):
        if path == "/prompt":
            return 200, {"prompt_id": "p-new", "number": 3}
        raise AssertionError("unexpected request: " + path)

    def spec(self, **overrides):
        kwargs = dict(prompt="кадр", aspect="16:9", quality="720p", seconds=5, seed=42,
                      width=1280, height=720, models={"fast_unet": "f.safetensors"}, source="ui")
        kwargs.update(overrides)
        return JobSpec.video(**kwargs)

    async def start(self, **overrides):
        self.app.comfy_json = self.comfy
        response = await self.app.queue(GRAPH, {}, {"mode": "video", "seed": 42}, spec=self.spec(**overrides))
        self.assertEqual(response.status, 200)
        return self.app.jobs["p-new"]

    async def test_a_new_job_gets_a_record_with_its_spec(self):
        job = await self.start()
        record = job["record"]
        self.assertEqual(record.state, JobState.QUEUED)
        self.assertEqual(job["state"], "queued", "окно и агент по-прежнему видят старые слова")
        self.assertEqual(record.spec.prompt, "кадр")
        self.assertEqual(record.prompt_id, "p-new")
        self.assertEqual(record.backend_job_id, "p-new")
        self.assertNotEqual(record.job_id, "p-new", "внутренний id не должен совпадать с публичным")
        self.assertTrue(record.backend_id.startswith("comfy:"))

    async def test_finalizing_is_a_phase_but_the_window_still_sees_running(self):
        job = await self.start()
        release = asyncio.Event()

        async def slow(pid):
            await release.wait()
            return VIDEO_OUTPUT

        self.app.job_outputs = slow
        task = asyncio.create_task(self.app.finish_job(job))
        await support.until(lambda: job.get("finalizing"))
        self.assertEqual(job["state"], "running")
        self.assertEqual(job["record"].state, JobState.FINALIZING)
        release.set()
        await task
        self.assertEqual(job["record"].state, JobState.SUCCEEDED)
        self.assertNotIn("p-new", self.app.jobs)
        result = job["record"].results[0]
        self.assertEqual(result.state, MediaState.LIBRARY)
        self.assertEqual(result.library_id, self.app.library.items[0]["id"])
        self.assertTrue(result.has_copy or result.path is None)

    async def test_a_draft_policy_keeps_the_result_out_of_the_library(self):
        job = await self.start(result_policy="draft")
        await self.app.finish_job(job)
        self.assertEqual(self.app.library.items, [])
        self.assertEqual(len(self.app.pending), 1)
        self.assertEqual(job["record"].results[0].state, MediaState.DRAFT)

    async def test_a_late_event_cannot_revive_a_finished_job(self):
        record = JobRecord(job_id="j-cancelled", state=JobState.CANCELED, prompt_id="p9")
        job = support.job("p9", record=record)
        self.app.jobs["p9"] = job
        self.assertFalse(self.app.try_set_job_state(job, JobState.RUNNING))
        self.assertEqual(record.state, JobState.CANCELED)

    async def test_failure_wins_over_a_later_success_event(self):
        record = JobRecord(job_id="j8", state=JobState.RUNNING, prompt_id="p8")
        job = support.job("p8", record=record)
        self.app.jobs["p8"] = job
        self.assertTrue(self.app.set_job_state(job, JobState.FAILED, error="движок упал"))
        self.assertFalse(self.app.try_set_job_state(job, JobState.SUCCEEDED))
        self.assertEqual(record.state, JobState.FAILED)
        self.assertEqual(job["state"], "error")
        self.assertEqual(record.error, "движок упал")

    async def test_cancel_is_a_request_until_the_engine_confirms(self):
        record = JobRecord(job_id="j7", state=JobState.RUNNING, prompt_id="p7", backend="comfy",
                           backend_id="comfy:test", backend_job_id="p7")
        job = support.job("p7", state="running", record=record)
        self.app.jobs["p7"] = job

        async def comfy(method, path, **kwargs):
            if path == "/interrupt":
                return 200, {}
            raise AssertionError("unexpected request: " + path)

        async def no_watch(pid):
            return None

        self.app.comfy_json = comfy
        self.app.interrupt_watch = no_watch
        await self.app.cancel_job("p7")
        self.assertTrue(record.cancel_requested)
        self.assertEqual(record.state, JobState.RUNNING, "состояние меняется только после подтверждения")
        self.assertEqual(job["state"], "running")
        self.app.try_set_job_state(job, JobState.CANCELED)
        self.assertEqual(record.state, JobState.CANCELED)

    async def test_the_details_window_shows_the_phase(self):
        job = await self.start()
        self.app.jobs["p10"] = job   # h_job reads by prompt id
        response = await self.app.h_job(_Match("p-new"))
        body = __import__("json").loads(response.text)
        self.assertEqual(body["state"], "queued")
        self.assertEqual(body["phase"], "queued")
        self.assertFalse(body["finalizing"])

    async def test_an_unknown_id_does_not_stop_whatever_is_running(self):
        calls = []

        async def comfy(method, path, **kwargs):
            calls.append(path)
            return 200, {}

        self.app.comfy_json = comfy
        await self.app.cancel_job("nope")
        self.assertEqual(calls, [], "отмена неизвестного id не должна трогать движок")
        self.assertTrue(any("останавливать нечего" in (e.get("text") or "") for e in self.events))

    async def test_stop_the_current_thing_is_its_own_operation(self):
        calls = []

        async def comfy(method, path, **kwargs):
            calls.append((path, kwargs))
            return 200, {}

        self.app.comfy_json = comfy
        await self.app.h_interrupt(support.Req({}))   # the window sends no id: «остановить текущее»
        self.assertEqual(calls, [("/interrupt", {"json": {}})])

    async def test_a_known_job_is_stopped_by_its_own_id(self):
        record = JobRecord(job_id="j9", state=JobState.RUNNING, prompt_id="p9", backend="comfy",
                           backend_id="comfy:test", backend_job_id="p9")
        self.app.jobs["p9"] = support.job("p9", state="running", record=record)
        calls = []

        async def comfy(method, path, **kwargs):
            calls.append((path, kwargs))
            return 200, {}

        async def no_watch(pid):
            return None

        self.app.comfy_json = comfy
        self.app.interrupt_watch = no_watch
        await self.app.h_interrupt(support.Req({"prompt_id": "p9"}))
        self.assertEqual(calls[0], ("/interrupt", {"json": {"prompt_id": "p9"}}),
                         "должно прерываться только это задание")


class _Match:
    """The only part of an aiohttp request h_job uses."""

    def __init__(self, pid):
        self.match_info = {"pid": pid}


if __name__ == "__main__":
    unittest.main()
