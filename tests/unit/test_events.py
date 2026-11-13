"""Event delivery: a fact is recorded without windows, and one slow window holds nothing up.

    python tests/unit/test_events.py
"""

from __future__ import annotations

import asyncio
import time
import unittest

import support  # noqa: F401  (puts app/ on sys.path)

from runtime.events import EventHub
from runtime.tasks import TaskSupervisor


class FakeSocket:
    """A window: it remembers what it was sent, and it can refuse to take anything for a while."""

    def __init__(self, block=None):
        self.sent = []
        self.closed = False
        self.block = block

    async def send_str(self, text):
        if self.block is not None:
            await self.block.wait()
        self.sent.append(text)

    async def send_bytes(self, data):
        if self.block is not None:
            await self.block.wait()
        self.sent.append(data)

    async def close(self):
        self.closed = True


class HubTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tasks = TaskSupervisor()
        self.remembered = []
        self.errors = []
        self.hub = EventHub(tasks=self.tasks, remember=self.remembered.append, size=4, timeout=0.2,
                            note_error=self.errors.append)

    def window(self, **kwargs):
        ws = FakeSocket(**kwargs)
        sub = self.hub.attach(ws, hello={"type": "hello"})
        return ws, sub

    async def sent(self, ws, count=1):
        await support.until(lambda: len(ws.sent) >= count)
        return ws.sent

    async def test_an_event_reaches_the_window(self):
        ws, _ = self.window()
        await self.hub.publish({"type": "stage", "prompt_id": "p1", "stage": "sample"})
        self.assertEqual(len(await self.sent(ws, 2)), 2)
        self.assertIn('"stage": "sample"', ws.sent[1])

    async def test_the_outcome_is_remembered_without_any_window(self):
        await self.hub.publish({"type": "done", "prompt_id": "p1", "item": {"id": "m1"}})
        self.assertEqual([p["type"] for p in self.remembered], ["done"],
                         "исход задания зависел от наличия окна")

    async def test_a_slow_window_does_not_hold_the_publisher(self):
        gate = asyncio.Event()
        ws, _ = self.window(block=gate)
        started = time.monotonic()
        await asyncio.wait_for(self.hub.publish({"type": "done", "prompt_id": "p1"}), timeout=1.0)
        self.assertLess(time.monotonic() - started, 1.0, "публикация ждала медленное окно")
        gate.set()
        await self.sent(ws, 2)

    async def test_a_frozen_window_is_dropped_after_the_timeout(self):
        gate = asyncio.Event()
        ws, sub = self.window(block=gate)
        await self.hub.publish({"type": "stage", "prompt_id": "p1"})
        await support.until(lambda: ws.closed)
        self.assertTrue(self.errors, "окно, которое не принимает события, закрыто молча")

    async def test_an_outcome_pushes_out_progress_when_the_queue_is_full(self):
        gate = asyncio.Event()
        ws, _ = self.window(block=gate)
        for step in range(1, 8):
            await self.hub.publish({"type": "stage", "prompt_id": "p1", "step": step})
        await self.hub.publish({"type": "done", "prompt_id": "p1"})
        gate.set()
        await support.until(lambda: any("done" in str(item) for item in ws.sent))
        self.assertTrue(any("done" in str(item) for item in ws.sent), "исход потерялся в переполненной очереди")

    async def test_hello_comes_before_any_event(self):
        ws, _ = self.window()
        await self.hub.publish({"type": "stage", "prompt_id": "p1"})
        await self.sent(ws, 2)
        self.assertIn("hello", ws.sent[0])

    async def test_a_preview_is_dropped_when_the_window_cannot_keep_up(self):
        gate = asyncio.Event()
        ws, _ = self.window(block=gate)
        await self.hub.publish({"type": "stage", "prompt_id": "p1"})   # fills the sender
        for _ in range(10):
            self.hub.preview(b"\x89PNG")
        gate.set()
        await asyncio.sleep(0.1)
        self.assertLessEqual(len([item for item in ws.sent if isinstance(item, bytes)]), 4,
                             "кадры превью не отбрасывались")

    async def test_detach_stops_the_sender(self):
        ws, sub = self.window()
        await self.hub.detach(sub)
        await asyncio.sleep(0)
        self.assertEqual(self.tasks.names(), [], "задача-отправитель осталась жить")
        self.assertEqual(self.hub.windows(), 0)

    async def test_bytes_are_sent_as_bytes(self):
        ws, sub = self.window()
        sub.offer(b"\x89PNG")
        await support.until(lambda: any(isinstance(item, bytes) for item in ws.sent))


class AppEventTest(unittest.IsolatedAsyncioTestCase):
    """The finalization never waits for a window, and an outcome exists without one (plan §4 stage 7)."""

    def setUp(self):
        self.app = support.fresh_app()

        async def outputs(pid):
            return support.VIDEO_OUTPUT

        self.app.job_outputs = outputs

    async def test_a_frozen_window_does_not_delay_the_result(self):
        gate = asyncio.Event()
        ws = FakeSocket(block=gate)
        sub = self.app.events.attach(ws, hello={"type": "hello"})
        job = support.job("p1", record=support.job_record("p1"))
        self.app.jobs["p1"] = job
        await asyncio.wait_for(self.app.finish_job(job), timeout=5)
        self.assertEqual(len(self.app.library.items), 1, "замерзшее окно задержало запись результата")
        self.assertEqual(self.app.job_outcome("p1")["state"], "done")
        await self.app.events.detach(sub)

    async def test_a_finished_job_is_not_lost_without_any_window(self):
        job = support.job("p2", record=support.job_record("p2"))
        self.app.jobs["p2"] = job
        await self.app.finish_job(job)
        self.assertEqual(self.app.job_outcome("p2")["state"], "done", "исход записан только в окно")
        self.assertEqual(len(self.app.library.items), 1, "результат не попал в Библиотеку без окна")
        self.assertEqual(self.app.events.windows(), 0)


if __name__ == "__main__":
    unittest.main()
