"""SQLite storage: schema, the import of the older files, and the refusal to touch a newer database.

    python tests/unit/test_storage.py
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import unittest
from pathlib import Path

import support  # noqa: F401  (puts app/ on sys.path)

from domain.errors import SchemaVersionError
from domain.jobs import JobRecord, JobState
from domain.media import MediaRecord, MediaState
from storage import migrations
from storage.database import Database
from storage.migrations import migrate
from storage.repositories import (IdempotencyRepository, JobRepository, LibraryRepository,
                                  ResultRepository)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "legacy"
LIBRARY_IDS = ["a1b2c3d4e5f6", "b2c3d4e5f6a1", "c3d4e5f6a1b2"]


def data_dir(*files):
    folder = support.new_dir("storage")
    for name in files or ("library.json", "queue.json"):
        shutil.copy(FIXTURES / name, folder / name)
    return folder


class DbCase(unittest.TestCase):
    """Every test gets its database closed, so an open connection does not outlive the check."""

    def open_db(self, path):
        database = Database(path)
        self.addCleanup(database.close)
        return database

    def imported(self, folder):
        database = self.open_db(folder / "h3studio.db")
        migrate(database, folder)
        return database


class SchemaTest(DbCase):
    def test_the_schema_is_created_and_marked_once(self):
        folder = data_dir()
        db = self.open_db(folder / "h3studio.db")
        report = migrate(db, folder)
        self.assertEqual(report.applied, ["0001-schema", "1000-import-library", "1001-import-queue"])
        self.assertEqual(db.schema_version(), 1)
        self.assertEqual(db.one("PRAGMA journal_mode")["journal_mode"].lower(), "wal")

    def test_a_second_start_imports_nothing_again(self):
        folder = data_dir()
        db = self.imported(folder)
        again = migrate(db, folder)
        self.assertEqual(again.applied, [])
        self.assertEqual(again.skipped, ["0001-schema", "1000-import-library", "1001-import-queue"])
        self.assertEqual(LibraryRepository(db).count(), len(LIBRARY_IDS))
        self.assertEqual(JobRepository(db).count(), 2)

    def test_a_database_of_a_newer_program_is_refused(self):
        folder = data_dir()
        db = self.imported(folder)
        db.execute("INSERT INTO schema_migrations(name, version, applied_at) VALUES('9999-future', 2, 0)")
        with self.assertRaises(SchemaVersionError):
            migrate(db, folder)
        self.assertEqual(LibraryRepository(db).count(), len(LIBRARY_IDS), "база не должна переписываться")

    def test_the_table_order_follows_the_file(self):
        db = self.imported(data_dir())
        self.assertEqual([row["id"] for row in db.query("SELECT id FROM library ORDER BY position")], LIBRARY_IDS)


class LibraryImportTest(DbCase):
    def test_ids_paths_favourites_and_unknown_fields_survive(self):
        db = self.imported(data_dir())
        items = LibraryRepository(db).load()
        self.assertEqual([x["id"] for x in items], LIBRARY_IDS)
        self.assertTrue(items[0]["fav"])
        self.assertEqual(items[1]["note"], "поле, которого текущая версия не знает")
        self.assertEqual(items[2]["file"]["path"], "E:\\Clips\\my_clip.mp4")
        self.assertEqual(items[0]["local"], "D:\\My Library\\20261001_a1b2c3d4e5f6_h3s_0001.mp4")

    def test_a_new_item_goes_on_top_and_an_old_one_stays_in_place(self):
        db = self.imported(data_dir())
        library = LibraryRepository(db)
        library.add({"id": "new1", "kind": "video", "mode": "video", "date": "2026-11-12T10:00:00"})
        self.assertEqual(library.load()[0]["id"], "new1")
        item = library.load()[1]
        item["fav"] = True   # the favourite flag changes, the position must not
        library.add(item)
        self.assertEqual([x["id"] for x in library.load()], ["new1", *LIBRARY_IDS])
        self.assertTrue(library.load()[1]["fav"])

    def test_a_broken_file_is_not_replaced_by_an_empty_library(self):
        folder = support.new_dir("storage")
        (folder / "library.json").write_text("{не json", encoding="utf-8")
        db = self.open_db(folder / "h3studio.db")
        report = migrate(db, folder)
        self.assertIn("0001-schema", report.applied)
        self.assertNotIn("1000-import-library", report.applied)
        self.assertTrue(report.problems)
        self.assertEqual(LibraryRepository(db).count(), 0)
        self.assertTrue((folder / "library.json").exists(), "битый файл должен остаться для разбора")
        problem = db.one("SELECT * FROM import_problems")
        self.assertIn("library.json", problem["name"])

        (folder / "library.json").write_text(json.dumps([{"id": "x1"}]), encoding="utf-8")
        again = migrate(db, folder)
        self.assertIn("1000-import-library", again.applied, "исправленный файл должен импортироваться")
        self.assertEqual(LibraryRepository(db).count(), 1)


class QueueImportTest(DbCase):
    def test_the_queue_is_imported_and_nothing_is_sent(self):
        db = self.imported(data_dir())
        jobs = JobRepository(db).active()
        self.assertEqual([j.prompt_id for j in jobs], ["aaaa-bbbb-cccc-dddd", "eeee-ffff-0000-1111"])
        self.assertTrue(all(j.state is JobState.QUEUED for j in jobs))
        self.assertTrue(jobs[0].graph, "граф нужен, чтобы задание можно было отправить заново")
        self.assertEqual(jobs[1].legacy["preview"], True)
        self.assertIsNone(jobs[1].spec, "спецификация не выдумывается из старых полей")

    def test_a_queue_record_without_a_graph_is_skipped(self):
        folder = support.new_dir("storage")
        (folder / "queue.json").write_text(json.dumps([{"pid": "x"}, {"graph": {"1": {}}, "pid": "y"}]),
                                           encoding="utf-8")
        db = self.open_db(folder / "h3studio.db")
        report = migrate(db, folder)
        self.assertEqual(report.imported.get("queue"), 1)
        self.assertEqual([j.prompt_id for j in JobRepository(db).active()], ["y"])


class InterruptedImportTest(DbCase):
    def test_a_failed_step_leaves_no_marker_and_no_rows(self):
        folder = data_dir()
        db = self.open_db(folder / "h3studio.db")
        original = migrations.STEPS

        def boom(_db, _data_dir, _report):
            raise RuntimeError("падение посреди импорта")

        migrations.STEPS = (original[0], (original[1][0], original[1][1], boom, original[1][3]), original[2])
        try:
            report = migrate(db, folder)
        finally:
            migrations.STEPS = original
        self.assertNotIn("1000-import-library", report.applied)
        self.assertNotIn("1000-import-library", db.applied())
        self.assertEqual(LibraryRepository(db).count(), 0, "недоделанный импорт не должен оставить половину")
        self.assertEqual(JobRepository(db).count(), 0, "и не должен перейти к следующему шагу")

        report = migrate(db, folder)
        self.assertIn("1000-import-library", report.applied)
        self.assertEqual(LibraryRepository(db).count(), len(LIBRARY_IDS))


class BackupTest(DbCase):
    def test_the_backup_holds_the_original_files(self):
        folder = data_dir()
        db = self.open_db(folder / "h3studio.db")
        report = migrate(db, folder)
        self.assertIsNotNone(report.backup)
        backup = Path(report.backup)
        self.assertEqual(json.loads((backup / "library.json").read_text(encoding="utf-8")),
                         json.loads((FIXTURES / "library.json").read_text(encoding="utf-8")))
        self.assertTrue((backup / "queue.json").exists())

    def test_a_copy_of_the_database_opens_and_has_the_same_rows(self):
        folder = data_dir()
        db = self.imported(folder)
        target = folder / "copy.db"
        db.backup_to(target)
        copy = sqlite3.connect(str(target))
        try:
            self.assertEqual(copy.execute("SELECT COUNT(*) FROM library").fetchone()[0], len(LIBRARY_IDS))
        finally:
            copy.close()

    def test_a_moved_data_folder_keeps_the_records(self):
        folder = data_dir()
        db = self.imported(folder)
        db.close()   # a closed database checkpoints its WAL: the folder can be copied
        moved = Path(str(folder) + "-moved")
        shutil.copytree(folder, moved)
        moved_db = self.open_db(moved / "h3studio.db")
        self.assertEqual(LibraryRepository(moved_db).count(), len(LIBRARY_IDS))
        self.assertEqual(JobRepository(moved_db).count(), 2)
        self.assertEqual(migrate(moved_db, moved).applied, [], "в перенесённой базе импортировать нечего")


class ResultAndKeyTest(DbCase):
    def setUp(self):
        self.database = self.imported(data_dir())

    def test_the_same_engine_output_is_registered_once(self):
        record = JobRecord(job_id="j1", prompt_id="p1", state=JobState.FINALIZING, created_at=1.0, updated_at=1.0)
        record.add_result(MediaRecord(media_id="m1", backend_file={"filename": "clip.mp4", "subfolder": "",
                                                                  "type": "output"}))
        JobRepository(self.database).save(record)
        self.assertEqual(len(ResultRepository(self.database).by_job("j1")), 1)
        self.assertFalse(ResultRepository(self.database).register("j1", record.results[0]))
        self.assertEqual(len(ResultRepository(self.database).by_job("j1")), 1,
                         "повторная регистрация дала вторую строку")

    def test_an_idempotency_key_is_reserved_once(self):
        keys = IdempotencyRepository(self.database)
        self.assertTrue(keys.reserve("k1", "отпечаток", "job-1"))
        self.assertFalse(keys.reserve("k1", "отпечаток", "job-2"))
        self.assertEqual(keys.get("k1")["job_id"], "job-1")

    def test_a_job_and_its_results_survive_a_round_trip(self):
        record = JobRecord(job_id="j2", prompt_id="p2", state=JobState.RUNNING, created_at=1.0, updated_at=2.0,
                           graph={"1": {"class_type": "Test", "inputs": {}}})
        record.add_result(MediaRecord(media_id="m2", state=MediaState.LIBRARY,
                                      backend_file={"filename": "a.png", "type": "output"}))
        JobRepository(self.database).save(record)
        again = JobRepository(self.database).get("j2")
        self.assertEqual(again.graph, record.graph)
        self.assertEqual(len(again.results), 1)
        self.assertEqual(again.results[0].media_id, "m2")


if __name__ == "__main__":
    unittest.main()
