"""The one promise of atomic_write: the target is either its old content or the new one, never half.

    python tests/unit/test_atomic_write.py
"""

from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path

import support
from storage import atomic
from storage.atomic import AtomicWriteError, atomic_write_bytes, atomic_write_json, atomic_write_text


class AtomicWriteTest(unittest.TestCase):
    def setUp(self):
        self.dir = support.new_dir("atomic")
        self.path = self.dir / "state.json"

    def files(self):
        return sorted(p.name for p in self.dir.iterdir())

    def test_writes_and_replaces(self):
        atomic_write_text(self.path, "старое")
        atomic_write_text(self.path, "новое")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "новое")
        self.assertEqual(self.files(), ["state.json"], "временный файл остался рядом с данными")

    def test_previous_version_survives_a_failed_replace(self):
        atomic_write_text(self.path, "старое")
        real = atomic.os.replace

        def boom(src, dst):
            raise OSError(13, "locked")

        atomic.os.replace = boom
        try:
            with self.assertRaises(AtomicWriteError):
                atomic_write_text(self.path, "новое")
        finally:
            atomic.os.replace = real
        self.assertEqual(self.path.read_text(encoding="utf-8"), "старое")
        self.assertEqual(self.files(), ["state.json"], "временный файл не убран после ошибки")

    def test_error_names_the_file(self):
        real = atomic.os.replace
        atomic.os.replace = lambda src, dst: (_ for _ in ()).throw(OSError(13, "locked"))
        try:
            with self.assertRaises(AtomicWriteError) as caught:
                atomic_write_text(self.path, "x")
        finally:
            atomic.os.replace = real
        self.assertEqual(caught.exception.path, self.path)
        self.assertIn(str(self.path), str(caught.exception))

    def test_a_momentary_lock_is_retried(self):
        calls = []
        real = atomic.os.replace

        def flaky(src, dst):
            calls.append(dst)
            if len(calls) < 3:
                raise PermissionError(13, "sharing violation")
            return real(src, dst)

        atomic.os.replace = flaky
        try:
            atomic_write_text(self.path, "после блокировки")
        finally:
            atomic.os.replace = real
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "после блокировки")

    def test_json_is_readable_utf8(self):
        data = {"prompt": "девушка в лесу", "seed": 42, "nested": [1, 2]}
        atomic_write_json(self.path, data, indent=1)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), data)
        self.assertIn("\n", self.path.read_text(encoding="utf-8"))
        self.assertNotIn("\\u", self.path.read_text(encoding="utf-8"))

    def test_concurrent_writers_do_not_mix(self):
        payloads = [("а" * 5000).encode(), ("б" * 5000).encode(), ("в" * 5000).encode()]

        def writer(data):
            for _ in range(20):
                atomic_write_bytes(self.path, data)

        threads = [threading.Thread(target=writer, args=(p,)) for p in payloads]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertIn(self.path.read_bytes(), payloads, "файл собрался из двух записей сразу")
        self.assertEqual(self.files(), ["state.json"])

    def test_writes_without_fsync_still_replace(self):
        atomic_write_text(self.path, "быстро", fsync=False)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "быстро")


if __name__ == "__main__":
    unittest.main()
