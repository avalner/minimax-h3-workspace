"""The engine manager: one restart at a time, the owner of the process, the VRAM handshake.

    python tests/unit/test_engine_manager.py
"""

from __future__ import annotations

import asyncio
import threading
import unittest

import support  # noqa: F401  (puts app/ on sys.path)

from services.engines import EngineManager


class FakeProcess:
    """engine.ComfyProcess without a real child process."""

    def __init__(self, *, fail=False, block=None):
        self.restarts = 0
        self.stops = 0
        self.fail = fail
        self.block = block

    def restart(self):
        self.restarts += 1
        if self.block is not None:
            self.block.wait(5)
        if self.fail:
            raise RuntimeError("порт занят")

    def stop(self):
        self.stops += 1

    def alive(self):
        return True

    def log_tail(self, n=15):
        return "журнал движка"


class FakeExternal:
    def __init__(self):
        self.polls = 0

    def poll(self):
        self.polls += 1
        return None


class ManagerTest(unittest.IsolatedAsyncioTestCase):
    def manager(self, *, local="http://127.0.0.1:8188", process=None, ready=None, external=None):
        self.waited = []

        async def wait_ready(timeout=240):
            self.waited.append(timeout)
            if ready is not None:
                await ready()
        self.external_calls = []

        def start_external(stats, log_path, url):
            self.external_calls.append((dict(stats), url))
            return external or FakeExternal()

        manager = EngineManager(url=lambda: local, wait_ready=wait_ready, log_path="x.log",
                                external_restart=start_external, stats=lambda: {"system": {"argv": ["a"]}},
                                clock=lambda: 1000.0)
        manager.process = process
        return manager

    async def test_the_apps_own_engine_is_restarted_and_waited_for(self):
        process = FakeProcess()
        manager = self.manager(process=process)
        ok, why = await manager.restart()
        self.assertTrue(ok, why)
        self.assertEqual(process.restarts, 1)
        self.assertEqual(self.waited, [240])
        self.assertEqual(manager.last, {"at": 1000.0, "ok": True, "why": ""})

    async def test_the_users_engine_is_started_again_from_its_command_line(self):
        manager = self.manager(process=None)
        ok, why = await manager.restart()
        self.assertTrue(ok, why)
        self.assertEqual(self.external_calls, [({"system": {"argv": ["a"]}}, "http://127.0.0.1:8188")])
        self.assertIsNotNone(manager.external)

    async def test_two_restarts_at_once_start_one_engine(self):
        gate = threading.Event()
        process = FakeProcess(block=gate)
        manager = self.manager(process=process)
        first = asyncio.create_task(manager.restart())
        second = asyncio.create_task(manager.restart())
        await support.until(lambda: manager.restarting)
        gate.set()
        (ok1, why1), (ok2, why2) = await asyncio.gather(first, second)
        self.assertTrue(ok1 and ok2, (why1, why2))
        self.assertEqual(process.restarts, 1, "два перезапуска запустили два ComfyUI")

    async def test_a_remote_engine_is_not_restarted_from_here(self):
        manager = self.manager(local="http://10.0.0.5:8188", process=FakeProcess())
        ok, why = await manager.restart()
        self.assertFalse(ok)
        self.assertIn("другом компьютере", why)
        self.assertEqual(manager.process.restarts, 0)

    async def test_a_failed_restart_keeps_its_reason(self):
        manager = self.manager(process=FakeProcess(fail=True))
        ok, why = await manager.restart()
        self.assertFalse(ok)
        self.assertIn("порт занят", why)
        self.assertFalse(manager.last["ok"])
        self.assertEqual(manager.last["why"], why)

    async def test_the_restart_is_not_cleared_while_it_runs_and_is_cleared_after(self):
        gate = threading.Event()
        manager = self.manager(process=FakeProcess(block=gate))
        task = asyncio.create_task(manager.restart())
        await support.until(lambda: manager.restarting)
        gate.set()
        await task
        self.assertFalse(manager.restarting)

    async def test_stop_stops_only_the_process_it_owns_and_only_once(self):
        process = FakeProcess()
        manager = self.manager(process=process)
        await manager.stop()
        self.assertEqual(process.stops, 1)
        self.assertIsNone(manager.process)
        await manager.stop()
        self.assertEqual(process.stops, 1, "остановка повторилась")

    async def test_the_vram_handshake_keeps_the_models_between_pictures_of_one_mode(self):
        calls = {"llm": 0, "vram": 0}

        async def stop_llm():
            calls["llm"] += 1

        async def release_vram():
            calls["vram"] += 1
        manager = EngineManager(url=lambda: "http://127.0.0.1:8188", wait_ready=lambda timeout=0: None,
                                log_path="x", external_restart=lambda *a: None,
                                stop_llm=stop_llm, release_vram=release_vram, busy=lambda: False)
        self.assertFalse(await manager.prepare("krea"))
        self.assertTrue(await manager.prepare("krea"), "модель одного и того же режима выгрузили зря")
        await manager.prepare("video")
        self.assertEqual(calls, {"llm": 3, "vram": 2}, "видеопамять освобождается не тогда")

    async def test_the_vram_is_not_freed_while_other_jobs_run(self):
        calls = {"vram": 0}

        async def release_vram():
            calls["vram"] += 1
        manager = EngineManager(url=lambda: "http://127.0.0.1:8188", wait_ready=lambda timeout=0: None,
                                log_path="x", external_restart=lambda *a: None,
                                release_vram=release_vram, busy=lambda: True)
        await manager.prepare("video")
        self.assertEqual(calls["vram"], 0, "модели выгружены под работающим заданием")


if __name__ == "__main__":
    unittest.main()
