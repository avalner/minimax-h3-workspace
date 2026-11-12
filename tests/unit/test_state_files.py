"""The settings and the stored state: a write that did not happen must never look like a save.

    python tests/unit/test_state_files.py
"""

from __future__ import annotations

import json
import sqlite3
import unittest
from unittest import mock

import support
from storage.atomic import AtomicWriteError
from storage.database import Database
from storage.migrations import migrate
from storage.repositories import JobRepository

import server
from domain.jobs import JobState


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
    """The Library lives in the database now: the table is the truth, `items` is what the window shows."""

    def setUp(self):
        self.folder = support.new_dir("library")
        self.db = Database(self.folder / "state.db")
        self.addCleanup(self.db.close)
        migrate(self.db, self.folder)
        self.library = server.Library(self.db)
        self.item = {"id": "a1", "mode": "video", "prompt": "тест",
                     "file": {"filename": "x.mp4", "subfolder": "", "type": "output"}}

    def test_add_and_remove_are_written(self):
        self.library.add(dict(self.item))
        self.assertEqual([x["id"] for x in self.library.items], ["a1"])
        self.assertEqual([x["id"] for x in server.Library(self.db).items], ["a1"])
        self.library.remove("a1")
        self.assertEqual(self.library.items, [])
        self.assertEqual(server.Library(self.db).items, [])

    def test_a_changed_item_is_written_where_it_stands(self):
        self.library.add(dict(self.item))
        self.library.add({"id": "a2", "mode": "krea", "kind": "image"})
        first = self.library.items[1]
        first["fav"] = True
        self.library.save(first)
        again = server.Library(self.db).items
        self.assertEqual([x["id"] for x in again], ["a2", "a1"], "избранное не должно менять порядок")
        self.assertTrue(again[1]["fav"])

    def test_a_failed_add_does_not_keep_the_item(self):
        self.db.close()   # a database that cannot be used stands in for a failed write
        with self.assertRaises(sqlite3.Error):
            self.library.add(dict(self.item))
        self.assertEqual(self.library.items, [], "элемент остался в памяти, хотя запись не удалась")

    def test_a_failed_remove_keeps_the_item(self):
        self.library.add(dict(self.item))
        self.db.close()
        with self.assertRaises(sqlite3.Error):
            self.library.remove("a1")
        self.assertEqual([x["id"] for x in self.library.items], ["a1"])


class QueuePersistTest(unittest.TestCase):
    """The live queue is the jobs table: a job is written on every change and removed when it ends."""

    def setUp(self):
        self.app = support.fresh_app()

    def test_a_new_job_is_written_with_its_record(self):
        self.app.jobs["p1"] = support.job("p1", record=support.job_record("p1"))
        record = self.app.jobs_repo.by_prompt("p1")
        self.assertIsNotNone(record)
        self.assertEqual(record.graph["1"]["class_type"], "Test")

    def test_a_job_that_leaves_the_queue_leaves_the_table(self):
        self.app.jobs["p1"] = support.job("p1", record=support.job_record("p1"))
        self.app.jobs.pop("p1")
        self.assertIsNone(self.app.jobs_repo.by_prompt("p1"))

    def test_the_finalizing_phase_is_written(self):
        job = support.job("p1", record=support.job_record("p1"))
        self.app.jobs["p1"] = job
        self.app.set_job_state(job, JobState.FINALIZING)
        self.app.save_queue()   # what finish_job does right after marking the job
        self.assertEqual(self.app.jobs_repo.by_prompt("p1").state, JobState.FINALIZING)
        self.assertTrue(job["finalizing"])

    def test_a_failed_write_is_reported_not_hidden(self):
        self.app.jobs["p1"] = support.job("p1", record=support.job_record("p1"))
        self.app.db.close()   # the next write must fail
        self.app.save_queue()   # must not raise into the state change it was called from
        self.assertTrue(self.app.queue_save_error)
        self.assertTrue(any("очередь" in text.lower() for text in self.app.recent_errors))

    def test_a_successful_write_clears_the_error(self):
        self.app.db.close()
        self.app.save_queue()
        self.assertTrue(self.app.queue_save_error)
        self.app.db = Database(server.STATE_DB)
        self.addCleanup(self.app.db.close)
        self.app.jobs_repo = JobRepository(self.app.db)
        self.app.save_queue()
        self.assertIsNone(self.app.queue_save_error)


if __name__ == "__main__":
    unittest.main()
