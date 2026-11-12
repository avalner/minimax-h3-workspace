"""Settings, Library and queue.json: a write that did not happen must never look like a save.

    python tests/unit/test_state_files.py
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

import support
from storage.atomic import AtomicWriteError

import server


class ConfigTest(unittest.TestCase):
    def setUp(self):
        import engine

        self.engine = engine
        self.folder = support.new_dir("config")
        self.path = self.folder / "config.json"
        self.config = engine.Config(self.path)

    def test_update_is_saved(self):
        self.config.update(engine="external", setup_done=True)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["engine"], "external")
        self.assertEqual(self.config.get("engine"), "external")

    def test_failed_write_keeps_the_old_values(self):
        self.config.update(engine="external")
        with mock.patch.object(self.engine, "atomic_write_json",
                               side_effect=AtomicWriteError(self.path, "диск полон")):
            with self.assertRaises(OSError):
                self.config.update(engine="managed")
        self.assertEqual(self.config.get("engine"), "external", "память заявила сохранение, которого не было")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["engine"], "external")

    def test_a_broken_file_is_kept_aside(self):
        self.path.write_text("{не json", encoding="utf-8")
        config = self.engine.Config(self.path)
        self.assertEqual(config.data, {})
        aside = [p.name for p in self.folder.iterdir() if ".broken-" in p.name]
        self.assertEqual(len(aside), 1, "битый файл не сохранён для разбора")


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.folder = support.new_dir("library")
        self.path = self.folder / "library.json"
        self.library = server.Library(self.path)
        self.item = {"id": "a1", "mode": "video", "prompt": "тест",
                     "file": {"filename": "x.mp4", "subfolder": "", "type": "output"}}

    def written(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_add_and_remove_are_written(self):
        self.library.add(dict(self.item))
        self.assertEqual([x["id"] for x in self.written()], ["a1"])
        self.library.remove("a1")
        self.assertEqual(self.written(), [])
        self.assertEqual(self.library.items, [])

    def test_failed_add_does_not_keep_the_item(self):
        with mock.patch.object(server, "atomic_write_json",
                               side_effect=AtomicWriteError(self.path, "диск полон")):
            with self.assertRaises(OSError):
                self.library.add(dict(self.item))
        self.assertEqual(self.library.items, [], "элемент остался в памяти, хотя на диск не попал")
        self.assertFalse(self.path.exists())

    def test_failed_remove_keeps_the_item(self):
        self.library.add(dict(self.item))
        with mock.patch.object(server, "atomic_write_json",
                               side_effect=AtomicWriteError(self.path, "диск полон")):
            with self.assertRaises(OSError):
                self.library.remove("a1")
        self.assertEqual([x["id"] for x in self.library.items], ["a1"])


class QueueFileTest(unittest.TestCase):
    def setUp(self):
        self.app = support.fresh_app()

    def test_a_failed_queue_write_is_reported_not_hidden(self):
        self.app.jobs["p1"] = support.job("p1")
        with mock.patch.object(server, "atomic_write_json",
                               side_effect=AtomicWriteError(server.QUEUE_FILE, "диск полон")):
            self.app.save_queue()   # must not raise into the state change it was called from
        self.assertIn("диск полон", self.app.queue_save_error or "")
        self.assertTrue(any("очередь" in text.lower() for text in self.app.recent_errors))

    def test_a_successful_write_clears_the_error(self):
        self.app.jobs["p1"] = support.job("p1")
        with mock.patch.object(server, "atomic_write_json",
                               side_effect=AtomicWriteError(server.QUEUE_FILE, "диск полон")):
            self.app.save_queue()
        self.app.save_queue()
        self.assertIsNone(self.app.queue_save_error)

    def test_the_finalizing_mark_is_saved(self):
        self.app.jobs["p1"] = support.job("p1")
        self.app.jobs["p1"]["finalizing"] = True
        self.app.save_queue()
        saved = json.loads(server.QUEUE_FILE.read_text(encoding="utf-8"))
        self.assertTrue(saved[0]["finalizing"], "рестарт не узнает, что результат уже сгенерирован")
        self.app.jobs["p1"]["finalizing"] = False
        self.app.save_queue()
        self.assertFalse(json.loads(server.QUEUE_FILE.read_text(encoding="utf-8"))[0]["finalizing"])

    def test_a_finished_job_leaves_the_file(self):
        self.app.jobs["p1"] = support.job("p1")
        self.app.jobs.pop("p1", None)
        self.assertFalse(server.QUEUE_FILE.exists())


if __name__ == "__main__":
    unittest.main()
