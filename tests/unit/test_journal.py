"""The job journal: one line per event, nothing private in it, and the work never depends on it.

    python tests/unit/test_journal.py
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401  (sets the temporary data folder before the app is imported)

import journal
from domain.jobs import JobState


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.folder = support.new_dir("journal")
        self.path = self.folder / "logs" / "journal.jsonl"
        self.journal = journal.Journal(self.path)

    def lines(self):
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_an_event_is_json_with_its_fields(self):
        self.journal.event("submitted", job_id="j1", prompt_id="p1", mode="video", ms=12, keyed=False)
        entry = self.lines()[0]
        self.assertEqual(entry["event"], "submitted")
        self.assertEqual((entry["job_id"], entry["prompt_id"], entry["mode"], entry["ms"], entry["keyed"]),
                         ("j1", "p1", "video", 12, False))
        self.assertIn("at", entry)

    def test_only_primitives_are_written(self):
        self.journal.event("odd", config={"rh_key": "secret"}, message="x" * 500, ok=True, error=None)
        entry = self.lines()[0]
        self.assertNotIn("config", entry, "словарь конфигурации попал в журнал: так утекают токены")
        self.assertEqual(len(entry["message"]), journal.MAX_TEXT)
        self.assertIs(entry["ok"], True)
        self.assertNotIn("error", entry, "пустое значение не должно занимать поле")

    def test_the_tail_is_the_last_entries_oldest_first(self):
        for n in range(5):
            self.journal.event("tick", n=n)
        self.assertEqual([e["n"] for e in self.journal.tail(3)], [2, 3, 4])

    def test_the_file_rotates_once_and_keeps_the_previous_run(self):
        with mock.patch.object(journal, "MAX_BYTES", 200):
            for n in range(20):
                self.journal.event("tick", n=n)
        self.assertTrue(self.path.exists())
        rotated = self.path.with_suffix(self.path.suffix + ".1")
        self.assertTrue(rotated.exists(), "старый журнал не сохранён при переполнении")
        self.assertLess(self.path.stat().st_size, 400, "новый файл не начат заново")

    def test_a_read_only_folder_does_not_break_the_app(self):
        blocker = self.folder / "not-a-folder"
        blocker.write_text("x", encoding="utf-8")
        broken = journal.Journal(blocker / "journal.jsonl")
        broken.event("submitted", job_id="j1")   # must not raise
        self.assertIsNotNone(broken.write_error, "сбой записи не виден")
        self.assertEqual(broken.tail(1)[0]["job_id"], "j1", "событие потеряно вместе с файлом")

    def test_a_transition_is_recorded_with_both_ids(self):
        app = support.fresh_app()
        record = support.job_record("p1")
        app.jobs_repo.save(record)
        job = support.job("p1", record=record)
        app.jobs["p1"] = job
        app.set_job_state(job, JobState.RUNNING)
        app.set_job_state(job, JobState.FAILED, error="нет модели")
        moves = [e for e in app.journal.tail(10) if e["event"] == "transition"]
        self.assertEqual([(m["was"], m["now"]) for m in moves],
                         [("queued", "running"), ("running", "failed")])
        self.assertEqual({m["job_id"] for m in moves}, {record.job_id})
        self.assertEqual(moves[-1]["error"], "нет модели")
        self.assertIn("backend_job_id", moves[-1])


if __name__ == "__main__":
    unittest.main()
