"""A model file picked by hand: found in any subfolder, and offered only for the role it fits.

    python tests/unit/test_model_pick.py
"""

from __future__ import annotations

import unittest

import support  # noqa: F401  (puts app/ on sys.path)

import catalog

FAST = "fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors"


class SameFileTest(unittest.TestCase):
    def test_a_subfolder_does_not_change_the_file(self):
        self.assertTrue(catalog.same_file(rf"MinimaxH3\{FAST}", FAST))
        self.assertTrue(catalog.same_file(f"MinimaxH3/{FAST}", rf"MinimaxH3\{FAST}"))
        self.assertTrue(catalog.same_file(rf"MinimaxH3\{FAST}", rf"MinimaxH3\{FAST.upper()}"))

    def test_empty_names_are_not_a_file(self):
        self.assertFalse(catalog.same_file("", ""))
        self.assertFalse(catalog.same_file(None, FAST))

    def test_another_file_is_not_the_same(self):
        self.assertFalse(catalog.same_file("a.safetensors", "b.safetensors"))


class FileFitTest(unittest.TestCase):
    def test_the_roles_own_file_fits(self):
        self.assertEqual(catalog.file_fit("fast_unet", rf"MinimaxH3\{FAST}")[0], "ok")

    def test_the_file_of_another_role_does_not_fit(self):
        fit, why = catalog.file_fit("fast_unet", r"MinimaxH3\Minimax-h3_Singularity_ref2va_v1.3_int8.safetensors")
        self.assertEqual(fit, "no")
        self.assertIn("Lipsync", why)

    def test_another_h3_type_does_not_fit_by_name(self):
        self.assertEqual(catalog.file_fit("fast_unet", "minimax_h3_ref2va_pruned_int8_convrot.safetensors")[0], "no")

    def test_a_renamed_model_is_unknown_but_still_offered(self):
        self.assertEqual(catalog.file_fit("fast_unet", "my_h3_model.safetensors")[0], "unknown")

    def test_a_foreign_model_is_rejected(self):
        self.assertEqual(catalog.file_fit("fast_unet", "wan2.1_flf2v_720p_14B_fp8_e4m3fn.safetensors")[0], "no")

    def test_an_image_model_does_not_fit_a_video_role(self):
        self.assertEqual(catalog.file_fit("krea_unet", "minimax_h3_fl2va_pruned_int8_convrot.safetensors")[0], "no")
        self.assertEqual(catalog.file_fit("krea_unet", r"Images\krea2_turbo_int8_convrot.safetensors")[0], "ok")

    def test_no_file_name_belongs_to_two_roles(self):
        names = [v["file"].lower() for variants in catalog.VARIANTS.values() for v in variants.values()]
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
