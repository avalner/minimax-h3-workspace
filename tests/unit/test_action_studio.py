"""The action studio: one scene of a scene-by-scene action video, and what it continues from.

The studio counts scenes the way the Видеостудия does (each next scene continues the tail of the previous one), but the
model and the modules are the action ones: the action hybrid (or the studio's Singularity under the same modules),
DMAD + Speed Slider, the sound made by the model. One pass at full size with a noise mask is what the studio uses,
because the exact SelfLift scheme cannot continue a scene.

    python tests/unit/test_action_studio.py
"""

from __future__ import annotations

import unittest

import support  # noqa: F401  (puts app/ on sys.path)

import workflows as wf

MODELS = {"action_unet": "MinimaxH3\\hybrid_fl2va_ref2va_int8.safetensors",
          "lipsync_unet": "MinimaxH3\\Singularity_ref2va_v2_pruned_int8.safetensors",
          "text_encoder": "MinimaxH3\\qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
          "video_vae": "MinimaxH3\\minimax_h3_video_vae_int8_convrot.safetensors",
          "audio_vae": "MinimaxH3\\minimax_h3_audio_vae_fp32.safetensors",
          "action_upscaler": "MinimaxH3\\h3_upscaler_lms_v0.1.safetensors",
          "upscaler": "MinimaxH3\\minimax_h3_latent_upscaler_3d_bf16.safetensors"}
DMAD = [("minimax_h3_dmad_full_dareties_v4_step600_comfy.safetensors", 0.8)]
SCENES = [("she sprints through the market", 4, []), ("she vaults a stall", 4, []), ("she runs into the alley", 4, [])]


def node(g, cls):
    """The first node of a class (the graphs name nodes by number, the class is what matters)."""
    return next((n for n in g.values() if n["class_type"] == cls), None)


def nodes(g, cls):
    return [n for n in g.values() if n["class_type"] == cls]


class ActionSceneTest(unittest.TestCase):
    def scene(self, k=0, state=None, fresh=(), sing=True, steps=12, single=True):
        return wf.action_part_graph(MODELS, SCENES, k, 1024, 576, 42, ["hero.png"], DMAD, None, state, fresh,
                                    False, None, single, sing, steps)

    def test_the_studio_counts_one_full_size_pass(self):
        g, stages = self.scene()
        self.assertEqual(node(g, "MiniMaxH3ReferenceToVideo")["inputs"]["width"], 1024)
        self.assertEqual(node(g, "MiniMaxH3ReferenceToVideo")["inputs"]["height"], 576)
        self.assertEqual(node(g, "SamplerCustomAdvanced")["inputs"]["sigmas"], ["10", 0],
                         "студия считает сцену одним проходом, без SplitSigmas")
        self.assertNotIn("MinimaxH3LatentUpscaler3D", [n["class_type"] for n in g.values()])
        self.assertEqual(stages["__scenes__"]["n"], 3, "прогресс знает, какая это сцена из скольких")

    def test_the_first_scene_starts_from_the_pictures(self):
        g, _ = self.scene()
        self.assertIsNone(node(g, "LoadLatent"))
        self.assertIsNone(node(g, "SetLatentNoiseMask"))
        self.assertIsNotNone(node(g, "LoadImage"), "референсы грузятся")
        self.assertIsNotNone(node(g, "CreateVideo"), "сцена собирается в клип")
        self.assertIsNotNone(node(g, "VAEDecode"))

    def test_a_later_scene_continues_the_saved_tail(self):
        g, stages = self.scene(k=1, state="state_0.latent")
        load = node(g, "LoadLatent")
        self.assertIsNotNone(load, "продолжаем сохранённый хвост предыдущей сцены")
        self.assertEqual(load["inputs"]["latent"], "state_0.latent")
        self.assertIsNotNone(node(g, "SetLatentNoiseMask"), "хвост держится маской, а не пересчитывается")
        self.assertEqual(set(stages["__scenes__"]["map"].values()), {1}, "сцена 2 из 3")
        cut = node(g, "ImageFromBatch")
        self.assertIsNotNone(cut, "клип обрезан на перекрытие, чтобы сцены встали встык")
        self.assertGreater(cut["inputs"]["batch_index"], 0)

    def test_a_new_shot_does_not_continue_anything(self):
        g, _ = self.scene(k=1, state="state_0.latent", fresh=(False, True))
        self.assertIsNone(node(g, "LoadLatent"), "«Сменить план» строится заново по картинкам")
        self.assertIsNone(node(g, "ImageFromBatch"))

    def test_the_sound_of_the_join_is_trimmed_and_faded(self):
        g, _ = self.scene(k=1, state="state_0.latent")
        trims = nodes(g, "TrimAudioDuration")
        self.assertTrue(trims)
        self.assertAlmostEqual(trims[0]["inputs"]["start_index"], round(wf.long_plan([4, 4])[1]["ov"] / wf.FPS, 3), places=3)
        self.assertIsNotNone(node(g, "AudioConcat"), "на стыке короткое затухание, без щелчка")

    def test_the_tail_of_the_scene_is_kept_for_the_next_one(self):
        g, _ = self.scene()
        keep = node(g, "SaveLatent")
        self.assertIsNotNone(keep)
        self.assertEqual(keep["inputs"]["filename_prefix"], "H3Workspace/long_state")
        tail = next(n for n in g.values() if n["class_type"] == "LatentCut" and n["inputs"].get("amount") == wf.LONG_STATE_TOKENS)
        self.assertEqual(tail["inputs"]["index"], -wf.LONG_STATE_TOKENS)


class ActionModelChoiceTest(unittest.TestCase):
    def test_singularity_is_the_studio_model_under_the_action_modules(self):
        g, _ = wf.action_part_graph(MODELS, SCENES, 0, 1024, 576, 42, [], DMAD, None, None, (), False, None, True, True, 8)
        self.assertEqual(node(g, "UNETLoader")["inputs"]["unet_name"], MODELS["lipsync_unet"])
        self.assertEqual(node(g, "BlockSparseAttention")["inputs"]["selection"], "sol-attn", "модули экшна остаются")
        self.assertEqual(node(g, "MiniMaxH3SigmaShift")["inputs"]["shift_video"], 12.0)
        self.assertEqual(node(g, "BasicScheduler")["inputs"]["steps"], 8, "с Pika — 8 шагов")

    def test_the_hybrid_model_is_the_other_choice(self):
        g, _ = wf.action_part_graph(MODELS, SCENES, 0, 1024, 576, 42, [], DMAD, None, None, (), False, None, True, False, 12)
        self.assertEqual(node(g, "UNETLoader")["inputs"]["unet_name"], MODELS["action_unet"])
        self.assertEqual(node(g, "BasicScheduler")["inputs"]["steps"], 12)

    def test_the_lora_chain_sits_under_the_attention_backend(self):
        g, _ = wf.action_part_graph(MODELS, SCENES, 0, 1024, 576, 42, [], DMAD, None, None, (), False, None, True, True, 8)
        lora = node(g, "LoraLoaderModelOnly")
        self.assertEqual(lora["inputs"]["lora_name"], DMAD[0][0])
        self.assertEqual(lora["inputs"]["strength_model"], 0.8)
        self.assertEqual(node(g, "ModelAttentionBackend")["inputs"]["model"], [next(k for k, v in g.items() if v is lora), 0])

    def test_the_exact_scheme_is_available_for_a_first_scene(self):
        g, _ = wf.action_part_graph(MODELS, SCENES, 0, 1024, 576, 42, [], DMAD, None, None, (), True, None, False, False, 12)
        self.assertEqual(node(g, "SelfLiftH3Sampler")["inputs"]["highres_tiling"], True)
        self.assertEqual(node(g, "SelfLiftH3Sampler")["inputs"]["upscaler_model"], MODELS["action_upscaler"])
        self.assertTrue(node(g, "SelfLiftH3Sampler")["inputs"]["upscaler_unload"])


class SizesTest(unittest.TestCase):
    def test_the_two_pass_route_runs_at_six_tenths_and_aligns_to_32(self):
        lw, lh, fw, fh = wf.action_sizes(1280, 720)
        # 0.6 of the full size, rounded to the nearest block of 32 (432 lands on 448), then lifted back
        self.assertEqual((lw, lh), (768, 448), "первый проход — примерно 0,6 от полного размера")
        self.assertEqual((fw, fh), (1280, 736), "подъём возвращает полный размер по блокам 32")
        for v in (lw, lh, fw, fh):
            self.assertEqual(v % 32, 0, "все размеры выровнены по 32")


if __name__ == "__main__":
    unittest.main(verbosity=2)
