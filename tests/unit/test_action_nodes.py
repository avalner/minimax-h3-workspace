"""The exact action workflow: its two node packs are part of the install, and the graph asks for them.

The action section has two ways to sample: the original Prompt Mastery scheme (SelfLiftH3Sampler + H3SigmaRefiner, a
single sampler that lifts the latent with the learned upscaler) and a built-in stand-in with the same 8 + 4 schedule.
The packs of the original used to be missing from the catalog, so a set-up made by the app could only ever run the
stand-in. Now both are installed with the app, and the exact scheme is what the section asks for.

    python tests/unit/test_action_nodes.py
"""

from __future__ import annotations

import unittest

import support  # noqa: F401  (puts app/ on sys.path)

import catalog
import workflows as wf

PACKS = ("comfyui-SelfLift", "ComfyUI-YCNodes-MiniMax-H3")
MODELS = {"action_unet": "MinimaxH3\\minimax_h3_hybrid_fl2va_ref2va_int8.safetensors",
          "text_encoder": "MinimaxH3\\qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
          "video_vae": "MinimaxH3\\minimax_h3_video_vae_int8_convrot.safetensors",
          "audio_vae": "MinimaxH3\\minimax_h3_audio_vae_fp32.safetensors",
          "action_upscaler": "MinimaxH3\\h3_upscaler_lms_v0.1.safetensors",
          "upscaler": "MinimaxH3\\minimax_h3_latent_upscaler_3d_bf16.safetensors"}


class ActionPacksTest(unittest.TestCase):
    def test_both_packs_are_in_the_install_with_pinned_commits(self):
        packs = {p["name"]: p for p in catalog.NODE_PACKS}
        for name in PACKS:
            self.assertIn(name, packs, "пак должен ставиться программой, а не руками пользователя")
            self.assertRegex(packs[name]["commit"], r"^[0-9a-f]{40}$", "версия пака должна быть закреплена коммитом")
            self.assertNotIn("/", name, "имя пака — это и имя папки в custom_nodes")
        self.assertEqual(packs["comfyui-SelfLift"]["repo"], "facok/comfyui-SelfLift")
        self.assertEqual(packs["ComfyUI-YCNodes-MiniMax-H3"]["repo"], "yichengup/ComfyUI-YCNodes-MiniMax-H3")

    def test_both_are_required_for_the_action_section(self):
        checks = {c["pack"]: c for c in catalog.NODE_CHECKS}
        for name in PACKS:
            self.assertIn(name, checks, "о недостающей ноде должно быть сказано в «Настройках»")
            self.assertEqual(checks[name]["modes"], ["action"])
            self.assertFalse(checks[name]["optional"], "это обязательные ноды, а не украшение")
        self.assertIn("SelfLiftH3Sampler", checks["comfyui-SelfLift"]["classes"])
        self.assertIn("H3SigmaRefiner", checks["ComfyUI-YCNodes-MiniMax-H3"]["classes"])

    def test_the_pack_of_a_mode_has_a_repository_to_link_to(self):
        for c in catalog.NODE_CHECKS:
            self.assertTrue(catalog.node_repo(c["pack"]), f"у пака {c['pack']} нет ссылки на репозиторий")


class ExactGraphTest(unittest.TestCase):
    def faithful(self):
        return wf.action_graph(MODELS, "промпт", 1024, 576, 5, 7, ["face.png"], [], True, [])

    def stand_in(self):
        return wf.action_graph(MODELS, "промпт", 1024, 576, 5, 7, ["face.png"], [], False, [])

    def test_the_exact_scheme_is_one_selflift_sampler_with_the_refined_sigmas(self):
        g, stages = self.faithful()
        lift = g["14"]
        self.assertEqual(lift["class_type"], "SelfLiftH3Sampler")
        self.assertEqual(g["13"]["class_type"], "H3SigmaRefiner")
        self.assertEqual(lift["inputs"]["sigmas"], ["13", 0], "сигмы идут из доводочного шага, а не из планировщика")
        self.assertEqual(lift["inputs"]["latent_image"], ["8", 1])
        self.assertEqual(lift["inputs"]["upscaler_model"], MODELS["action_upscaler"])
        self.assertEqual(lift["inputs"]["lowres_scale"], wf.ACTION_LOWRES)
        self.assertEqual(lift["inputs"]["transition_step"], 8, "8 шагов в низком разрешении, 4 в высоком")
        self.assertTrue(lift["inputs"]["upscaler_unload"], "апскейлер должен уходить из видеопамяти после подъёма")
        self.assertEqual(stages["14"], "sample")

    def test_one_sampler_means_no_second_pass_of_our_own(self):
        g, _ = self.faithful()
        classes = [n["class_type"] for n in g.values()]
        self.assertEqual(classes.count("SelfLiftH3Sampler"), 1)
        self.assertNotIn("SamplerCustomAdvanced", classes, "в точной схеме второй проход делает сам SelfLift")
        self.assertNotIn("MinimaxH3LatentUpscaler3D", classes)

    def test_the_stand_in_keeps_its_own_two_passes(self):
        g, stages = self.stand_in()
        classes = [n["class_type"] for n in g.values()]
        self.assertEqual(classes.count("SamplerCustomAdvanced"), 2)
        self.assertIn("MinimaxH3LatentUpscaler3D", classes)
        self.assertNotIn("SelfLiftH3Sampler", classes)
        self.assertNotIn("H3SigmaRefiner", classes)
        self.assertEqual((stages["14"], stages["18"]), ("sample", "sample2"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
