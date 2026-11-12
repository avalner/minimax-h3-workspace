"""The supervisor of background tasks: a reference, the error, and a bounded stop.

    python tests/unit/test_task_supervisor.py
"""

from __future__ import annotations

import asyncio
import contextlib
import unittest

import support  # noqa: F401  (puts app/ on sys.path)

from runtime.tasks import TaskSupervisor


async def stop_for_good(task):
    """Cancel a task that swallows cancellation until it really ends (a check must tidy up after itself)."""
    for _ in range(200):
        if task.done():
            break
        task.cancel()
        await asyncio.sleep(0.01)
    with contextlib.suppress(asyncio.CancelledError):
        await task


class SupervisorTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.errors = []
        self.tasks = TaskSupervisor(note_error=self.errors.append)

    async def test_a_task_runs_under_its_name_and_is_forgotten_when_it_ends(self):
        seen = []

        async def work():
            await asyncio.sleep(0)
            seen.append("ran")
            return 7

        task = self.tasks.spawn("work", work())
        self.assertEqual(self.tasks.names(), ["work"])
        self.assertIs(self.tasks.get("work"), task)
        self.assertEqual(await task, 7)
        await asyncio.sleep(0)
        self.assertEqual(seen, ["ran"])
        self.assertEqual(self.tasks.names(), [], "завершённая задача осталась в списке")

    async def test_an_error_is_written_down_and_reported_once(self):
        async def boom():
            raise RuntimeError("диск кончился")

        task = self.tasks.spawn("boom", boom())
        with self.assertRaises(RuntimeError):
            await task
        await asyncio.sleep(0)
        self.assertEqual(len(self.errors), 1)
        self.assertIn("boom", self.errors[0])
        self.assertIn("диск кончился", self.errors[0])
        self.assertEqual(len(self.tasks.errors()), 1)

    async def test_a_cancelled_task_is_not_an_error(self):
        async def long():
            await asyncio.sleep(60)

        task = self.tasks.spawn("long", long())
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        self.assertEqual(self.errors, [])

    async def test_after_close_no_new_task_starts(self):
        async def work():
            return 1

        self.tasks.close()
        self.assertIsNone(self.tasks.spawn("late", work()), "задача стартовала при закрытии")
        self.assertEqual(self.tasks.refused, ["late"])

    async def test_the_same_name_is_one_task_unless_replaced(self):
        started = []

        async def work(tag):
            started.append(tag)
            await asyncio.sleep(60)

        first = self.tasks.spawn("same", work("first"))
        second = self.tasks.spawn("same", work("second"))
        self.assertIs(first, second, "под одним именем оказались две задачи")
        self.assertEqual(self.tasks.running(), {"same": first})
        third = self.tasks.spawn("same", work("third"), replace=True)
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertIs(self.tasks.get("same"), third, "замена потеряла новую задачу")
        await asyncio.sleep(0)
        self.assertEqual(started, ["third"])
        await stop_for_good(third)

    async def test_shutdown_cancels_everything_and_reports_nothing_unfinished(self):
        async def loop():
            while True:
                await asyncio.sleep(0.01)

        self.tasks.spawn("a", loop())
        self.tasks.spawn("b", loop())
        await asyncio.sleep(0)
        unfinished = await self.tasks.shutdown(timeout=2)
        self.assertEqual(unfinished, [])
        self.assertEqual(self.tasks.names(), [])
        self.assertEqual(self.errors, [], "отмена не должна выглядеть ошибкой")

    async def test_a_task_that_ignores_cancellation_is_reported_not_hidden(self):
        async def stubborn():
            swallowed = 0
            while True:
                try:
                    await asyncio.sleep(5)
                except asyncio.CancelledError:
                    swallowed += 1
                    if swallowed >= 3:   # cancelling a task twice at once delivers one exception
                        raise

        task = self.tasks.spawn("stubborn", stubborn())
        await asyncio.sleep(0)
        unfinished = await self.tasks.shutdown(timeout=0.1)
        self.assertEqual(unfinished, ["stubborn"], "незавершённая задача спрятана")
        await stop_for_good(task)   # the test cleans up its own stubborn task

    async def test_wait_idle_does_not_cancel(self):
        done = asyncio.Event()

        async def work():
            await asyncio.sleep(0.05)
            done.set()

        self.tasks.spawn("quick", work())
        self.assertEqual(await self.tasks.wait_idle(timeout=2), [])
        self.assertTrue(done.is_set())

    async def test_wait_idle_names_what_is_still_running(self):
        async def long():
            await asyncio.sleep(60)

        task = self.tasks.spawn("long", long())
        self.assertEqual(await self.tasks.wait_idle(timeout=0.05), ["long"])
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task


if __name__ == "__main__":
    unittest.main()
