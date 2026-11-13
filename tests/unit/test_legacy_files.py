"""Data written by an older version is imported into the database and a rewrite keeps what this
version does not know. The fixtures in `tests/fixtures/legacy` are synthetic: no real prompts, no
tokens, no user paths.

    python tests/unit/test_legacy_files.py
"""

from __future__ import annotations

import json
import shutil
import time
import unittest
from pathlib import Path

import support

import server

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "legacy"
LIBRARY_IDS = ["a1b2c3d4e5f6", "b2c3d4e5f6a1", "c3d4e5f6a1b2"]
QUEUE_IDS = ["aaaa-bbbb-cccc-dddd", "eeee-ffff-0000-1111"]


class LegacyFilesTest(unittest.TestCase):
    """An older data folder, opened by this version: the files are imported once and never again."""

    def setUp(self):
        support.clean_data()

    def prepare(self, queue_age=60):
        shutil.copy(FIXTURES / "library.json", server.LIBRARY_FILE)
        records = json.loads((FIXTURES / "queue.json").read_text(encoding="utf-8"))
        for record in records:
            record["saved"] = time.time() - queue_age
        server.QUEUE_FILE.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        return support.new_app()

    def test_the_library_keeps_ids_paths_favourites_and_unknown_fields(self):
        app = self.prepare()
        self.assertEqual([x["id"] for x in app.library.items], LIBRARY_IDS)
        first = app.library.items[0]
        self.assertTrue(first["fav"])
        self.assertEqual(first["local"], "D:\\My Library\\20261001_a1b2c3d4e5f6_h3s_0001.mp4")
        self.assertEqual(app.library.items[1]["note"], "поле, которого текущая версия не знает")

    def test_the_queue_is_waiting_for_the_user_and_nothing_was_sent(self):
        app = self.prepare()
        self.assertEqual([r.prompt_id for r in app.pending_resume], QUEUE_IDS)
        self.assertEqual(app.jobs, {}, "импорт очереди не должен ничего запускать")
        self.assertTrue(app.pending_resume[0].graph, "граф сохранён, чтобы задание можно было отправить")
        self.assertEqual(app.pending_resume[1].legacy["preview"], True)

    def test_a_second_start_imports_nothing_again(self):
        first = self.prepare()
        self.assertEqual(first.migration.skipped, [])
        second = support.new_app()
        self.assertEqual([x["id"] for x in second.library.items], LIBRARY_IDS, "импорт повторился")
        self.assertEqual(len(second.pending_resume), 2)
        self.assertEqual(second.migration.applied, [])
        self.assertEqual(second.migration.skipped, ["0001-schema", "1000-import-library", "1001-import-queue",
                                                    "0002-drafts", "0003-projects"])

    def test_the_original_files_are_kept(self):
        self.prepare()
        self.assertTrue(server.LIBRARY_FILE.exists(), "старый файл удалён — откатиться будет некуда")
        self.assertTrue(server.QUEUE_FILE.exists())

    def test_the_import_made_a_backup(self):
        app = self.prepare()
        backup = Path(app.migration.backup)
        self.assertTrue((backup / "library.json").exists())
        self.assertTrue((backup / "queue.json").exists())

    def test_a_week_old_queue_is_not_offered(self):
        app = self.prepare(queue_age=8 * 86400)
        self.assertEqual(app.pending_resume, [], "запись старше недели не должна возвращаться в очередь")
        self.assertEqual(app.jobs_repo.count(), 0, "и не должна оставаться в таблице")


if __name__ == "__main__":
    unittest.main()
