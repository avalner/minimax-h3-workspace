"""Data written by an older version still loads, and a rewrite keeps what this version does not know.

The fixtures in `tests/fixtures/legacy` are synthetic: no real prompts, no tokens, no user paths.
They are the starting point for the migration of stage 3.

    python tests/unit/test_legacy_files.py
"""

from __future__ import annotations

import json
import shutil
import time
import unittest
from pathlib import Path

import support

import engine
import server

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "legacy"


class LegacyFilesTest(unittest.TestCase):
    def setUp(self):
        self.folder = support.new_dir("legacy")
        self.data = self.folder / "data"
        shutil.copytree(FIXTURES, self.data)

    def test_the_library_keeps_ids_paths_favourites_and_unknown_fields(self):
        library = server.Library(self.data / "library.json")
        self.assertEqual([x["id"] for x in library.items],
                         ["a1b2c3d4e5f6", "b2c3d4e5f6a1", "c3d4e5f6a1b2"])
        self.assertTrue(library.get("a1b2c3d4e5f6")["fav"])
        self.assertEqual(library.get("c3d4e5f6a1b2")["file"]["path"], "E:\\Clips\\my_clip.mp4")
        self.assertEqual(library.get("b2c3d4e5f6a1")["note"], "поле, которого текущая версия не знает")

    def test_a_rewrite_does_not_lose_anything(self):
        path = self.data / "library.json"
        server.Library(path).save()
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                         json.loads((FIXTURES / "library.json").read_text(encoding="utf-8")))

    def test_the_config_keeps_unknown_fields(self):
        config = engine.Config(self.data / "config.json")
        self.assertEqual(config.get("modes"), ["video", "refs"])
        config.update(engine="external")
        written = json.loads((self.data / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(written["unknown_future_field"], "must survive a rewrite")
        self.assertEqual(written["field_from_a_later_version"], {"keep": True})

    def test_a_project_of_the_long_video_is_plain_json(self):
        project = json.loads((self.data / "long" / "testrun" / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(len(project["scenes"]), 2)
        self.assertEqual(project["unknown_future_field"]["nested"], [1, 2, 3])

    def test_a_week_old_queue_is_not_restored(self):
        app = support.fresh_app()
        shutil.copy(FIXTURES / "queue.json", server.QUEUE_FILE)
        self.assertEqual(app.load_queue(), [], "запись старше недели не должна возвращаться в очередь")

    def test_a_recent_queue_is_restored_as_it_was(self):
        app = support.fresh_app()
        records = json.loads((FIXTURES / "queue.json").read_text(encoding="utf-8"))
        for record in records:
            record["saved"] = time.time() - 60
        server.QUEUE_FILE.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        loaded = app.load_queue()
        self.assertEqual([r["pid"] for r in loaded], ["aaaa-bbbb-cccc-dddd", "eeee-ffff-0000-1111"])
        self.assertTrue(loaded[1]["preview"])
        self.assertTrue(loaded[1]["finalizing"], "старый файл без этого поля тоже должен читаться")


if __name__ == "__main__":
    unittest.main()
