"""Parameters of a job for the window: explicit when the record has them, a marked guess when it does not.

    python tests/unit/test_jobmeta.py
"""

from __future__ import annotations

import unittest

import support  # noqa: F401  (puts app/ on sys.path)

import jobmeta


class ParamsTest(unittest.TestCase):
    def test_a_new_job_has_the_parameters_themselves(self):
        meta = jobmeta.for_ui({"mode": "video", "sub": "Кадр → кадр · Fused Turbo",
                               "model": "fused", "acceleration": "normal"})
        self.assertEqual((meta["model"], meta["acceleration"]), ("fused", "normal"))
        self.assertEqual(meta["params_source"], "record", "новое задание не должно считаться догадкой")

    def test_an_old_record_is_read_from_its_caption_and_marked(self):
        meta = jobmeta.for_ui({"mode": "video", "sub": "Текст → видео · Fused Turbo"})
        self.assertEqual((meta["model"], meta["acceleration"]), ("fused", "normal"))
        self.assertEqual(meta["params_source"], "legacy")

    def test_an_old_fast_draft_is_recognised(self):
        meta = jobmeta.for_ui({"sub": "Изображение → видео · быстрый черновик"})
        self.assertEqual((meta["model"], meta["acceleration"]), ("fast", "sla"))
        self.assertEqual(meta["params_source"], "legacy")

    def test_an_old_record_without_those_words_is_the_default_and_still_marked(self):
        meta = jobmeta.for_ui({"sub": "Текст → видео"})
        self.assertEqual((meta["model"], meta["acceleration"]), ("fast", "normal"))
        self.assertEqual(meta["params_source"], "legacy")

    def test_a_record_with_nothing_known_says_so(self):
        meta = jobmeta.for_ui({})
        self.assertEqual((meta["model"], meta["acceleration"]), ("fast", "normal"))
        self.assertEqual(meta["params_source"], "unknown")

    def test_an_explicit_model_but_no_acceleration_still_counts_as_recorded(self):
        meta = jobmeta.for_ui({"model": "fused"})
        self.assertEqual(meta["params_source"], "record")
        self.assertEqual(meta["acceleration"], "normal")

    def test_the_caller_s_meta_is_not_changed(self):
        original = {"sub": "Текст → видео · Fused Turbo"}
        jobmeta.for_ui(original)
        self.assertNotIn("model", original)


if __name__ == "__main__":
    unittest.main()
