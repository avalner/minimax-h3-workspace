"""MiniMax H3 Image in the catalog: three roles, a shared text encoder, two sizes of the UNET and the profiles.

The picture model does NOT bring its own text encoder: it is the same qwen3vl_32b file the video modes already
download, so the mode lists the existing `text_encoder` role and a second copy can never sneak in. The UNET comes
in a light W4A8 (12,5 GB) and a heavier int8 (21 GB) build, and the profile decides which one is picked.

    python tests/unit/test_h3img_catalog.py
"""

from __future__ import annotations

import unittest

import support  # noqa: F401  (puts app/ on sys.path)

import catalog

UNET_W4A8 = "minimax_h3_image_pruned_w4a8.safetensors"
UNET_INT8 = "minimax_h3_image_pruned_int8_convrot.safetensors"
VAE = "minimax_h3_image_vae_fp16.safetensors"


class H3ImgRolesTest(unittest.TestCase):
    def test_the_mode_needs_its_own_unet_and_vae_and_the_video_text_encoder(self):
        self.assertEqual(catalog.MODE_ROLES["h3img"], ["h3img_unet", "text_encoder", "h3img_vae"])
        self.assertIn("text_encoder", catalog.MODE_ROLES["video"], "энкодер должен быть тот же, что у видео")
        self.assertEqual(catalog.VARIANTS["text_encoder"]["nvfp4"]["file"], "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors")

    def test_every_role_has_a_title_and_a_variant_label(self):
        for role in ("h3img_unet", "h3img_vae"):
            self.assertIn(role, catalog.ROLE_TITLES)
            self.assertIn(role, catalog.VARIANTS)
        self.assertIn("h3img", catalog.MODE_TITLES)
        self.assertEqual(catalog.VARIANT_LABELS["h3img_unet"]["w4a8"], "лёгкая W4A8 (12,5 ГБ)")

    def test_the_mode_has_no_optional_half(self):
        self.assertEqual(catalog.MODE_OPTIONAL.get("h3img", []), [], "всё нужное обязательно: без любого файла не запустить")
        self.assertEqual(catalog.MAIN_ROLE["h3img"], "h3img_unet")

    def test_the_mode_is_offered_to_existing_installs(self):
        self.assertIn("h3img", catalog.OFFER_MODES, "иначе его не предложат тем, кто обновился")

    def test_the_downloaded_files_are_the_published_ones(self):
        sizes = {k: round(v["size"] / 1e9, 2) for k, v in catalog.VARIANTS["h3img_unet"].items()}
        self.assertEqual(sizes, {"w4a8": 12.55, "int8": 20.98})
        self.assertEqual(round(catalog.VARIANTS["h3img_vae"]["fp16"]["size"] / 1e9, 2), 1.17)
        self.assertEqual(catalog.VARIANTS["h3img_unet"]["int8"]["file"], UNET_INT8)
        self.assertEqual(catalog.VARIANTS["h3img_vae"]["fp16"]["file"], VAE)
        for v in (*catalog.VARIANTS["h3img_unet"].values(), catalog.VARIANTS["h3img_vae"]["fp16"]):
            self.assertIn("Comfy-Org/MiniMax-H3-Image", v["url"])
            self.assertEqual(v["category"], "diffusion_models" if "vae" not in v["file"] else "vae")


class H3ImgFilesTest(unittest.TestCase):
    def test_the_known_file_names_belong_to_the_right_roles(self):
        self.assertEqual(catalog.KNOWN_FILES[UNET_W4A8], "h3img_unet")
        self.assertEqual(catalog.KNOWN_FILES[UNET_INT8], "h3img_unet")
        self.assertEqual(catalog.KNOWN_FILES[VAE], "h3img_vae")

    def test_a_file_of_the_other_role_is_refused(self):
        self.assertEqual(catalog.file_fit("h3img_unet", UNET_W4A8)[0], "ok")
        self.assertEqual(catalog.file_fit("h3img_vae", VAE)[0], "ok")
        fit, why = catalog.file_fit("h3img_unet", VAE)
        self.assertEqual(fit, "no", "VAE нельзя выбрать как модель")
        self.assertIn("VAE", why)
        self.assertEqual(catalog.file_fit("h3img_vae", UNET_INT8)[0], "no")

    def test_the_name_rule_recognises_the_family(self):
        self.assertTrue(catalog.looks_right("h3img_unet", "minimax_h3_image_pruned_w4a8.safetensors"))
        self.assertTrue(catalog.looks_right("h3img_vae", "minimax_h3_image_vae_fp16.safetensors"))
        self.assertFalse(catalog.looks_right("h3img_unet", "qwen_image_2.1_int8_convrot.safetensors"))


class H3ImgPlanTest(unittest.TestCase):
    def test_the_plan_counts_the_new_files(self):
        alone = catalog.plan_size(catalog.DEFAULT_CHOICE, [])
        with_mode = catalog.plan_size(catalog.DEFAULT_CHOICE, ["h3img"])
        added = {r: catalog.VARIANTS[r][catalog.DEFAULT_CHOICE[r]]["size"] for r in catalog.MODE_ROLES["h3img"]}
        # the text encoder is part of the install plan already (video modes), so it may add nothing
        self.assertGreaterEqual(with_mode - alone, added["h3img_unet"] + added["h3img_vae"] - 1)
        self.assertIn("h3img_unet", catalog.roles_for(["h3img"]))
        self.assertIn("text_encoder", catalog.roles_for(["h3img"]))

    def test_the_profiles_pick_the_light_or_the_heavy_unet(self):
        self.assertEqual(catalog.PROFILES["lite"]["h3img_unet"], "w4a8")
        self.assertEqual(catalog.PROFILES["standard"]["h3img_unet"], "w4a8")
        self.assertEqual(catalog.PROFILES["max"]["h3img_unet"], "int8")

    def test_choose_models_follows_the_profile(self):
        hw = {"vram_gb": 16, "ram_gb": 64, "nvidia": True, "compute_cap": 8.9}
        for profile, want in (("lite", "w4a8"), ("standard", "w4a8"), ("max", "int8")):
            choice, _, _, picked = catalog.choose_models(hw, profile)
            self.assertEqual(picked, profile)
            self.assertEqual(choice["h3img_unet"], want, f"профиль {profile}")
        # a big card recommends «max», and there the heavy int8 is taken
        big = {"vram_gb": 32, "ram_gb": 64, "nvidia": True, "compute_cap": 9.0}
        choice, _, _, profile = catalog.choose_models(big)
        self.assertEqual(profile, "max")
        self.assertEqual(choice["h3img_unet"], "int8")


if __name__ == "__main__":
    unittest.main(verbosity=2)
