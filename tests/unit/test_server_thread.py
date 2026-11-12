"""main.ServerThread: a start that failed says so, and its event loop is closed.

    python tests/unit/test_server_thread.py
"""

from __future__ import annotations

import unittest
from unittest import mock

import support  # noqa: F401  (puts app/ on sys.path)

import main
import server


class StartFailureTest(unittest.TestCase):
    """The window must not be opened for a server that is not there (plan §4 stage 6, item 7)."""

    def test_a_failed_start_is_remembered_and_the_loop_is_closed(self):
        with mock.patch.object(server, "build_app", side_effect=RuntimeError("база новее программы")):
            thread = main.ServerThread(port=1)   # the port is never used: build_app fails first
            thread.start()
            self.assertTrue(thread.ready.wait(10), "поток не сообщил о готовности вообще")
            self.assertIsNotNone(thread.error, "ошибка старта потеряна")
            self.assertIn("база новее программы", thread.error)
            self.assertIsNone(thread.loop, "цикл остался открытым после ошибки старта")
            thread.join(5)
            self.assertFalse(thread.is_alive(), "поток сервера остался работать")

    def test_closing_a_server_that_never_started_does_not_hang(self):
        thread = main.ServerThread(port=1)
        thread.shutdown()   # no loop, no runner: nothing to clean, nothing to wait for
        self.assertIsNone(thread.loop)
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
