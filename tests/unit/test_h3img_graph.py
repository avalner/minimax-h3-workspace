"""MiniMax H3 Image graphs: picture by text, an edit guided by up to five pictures, the sheet and the storyboard.

Three things are easy to break here and all three are checked:
* the recipe — 6 steps, the beta schedule, CFG 1 and no negative branch (the model was trained without one);
* the shape of the result — <Picture 1> gives it, unless the caller passes a size of its own;
* the node ids of the reference loaders must not collide with the nodes that measure the first picture (the third
  reference once overwrote the shape of the result).

    python tests/unit/test_h3img_graph.py
"""

from __future__ import annotations

import unittest

import support  # noqa: F401  (puts app/ on sys.path)

import workflows as wf

MODELS = {"h3img_unet": "MinimaxH3\\minimax_h3_image_pruned_w4a8.safetensors",
          "text_encoder": "MinimaxH3\\qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
          "h3img_vae": "MinimaxH3\\minimax_h3_image_vae_fp16.safetensors"}
REFS = [f"ref{i}.png" for i in range(1, 7)]


def nodes(g, cls):
    return [(k, n) for k, n in g.items() if n["class_type"] == cls]


def one(g, cls):
    found = nodes(g, cls)
    return found[0][1] if found else None


def linked(g, link):
    """The node a [id, slot] link points at."""
    return g[str(link[0])]


class H3ImgTextTest(unittest.TestCase):
    def setUp(self):
        self.g, self.stages = wf.h3img_t2i_graph(MODELS, "a cat on a windowsill", 2048, 2048, 11)

    def test_the_recipe_is_six_steps_cfg_one_on_the_official_schedule(self):
        # Comfy-Org's own template (image_minimax_h3_image_t2i.json): KSampler, 6 steps, cfg 1, euler, simple
        ks = one(self.g, "KSampler")
        self.assertEqual((ks["inputs"]["steps"], ks["inputs"]["cfg"]), (6, 1))
        self.assertEqual((ks["inputs"]["sampler_name"], ks["inputs"]["scheduler"]), ("euler", "simple"))
        self.assertEqual(ks["inputs"]["denoise"], 1)
        self.assertIsNone(one(self.g, "BasicScheduler"), "расписание задаёт KSampler, отдельных сигм нет")
        self.assertIsNone(one(self.g, "SamplerCustomAdvanced"))

    def test_there_is_no_negative_prompt(self):
        # CFG 1: the model was trained without a negative branch, so one would change nothing
        self.assertEqual(one(self.g, "KSampler")["inputs"]["negative"], ["6", 0])
        self.assertEqual(self.g["6"]["class_type"], "ConditioningZeroOut")
        self.assertIsNone(one(self.g, "CFGGuider"))

    def test_the_conditioning_carries_the_size_and_the_vae(self):
        cond = one(self.g, "MiniMaxH3ImageConditioning")["inputs"]
        self.assertEqual((cond["width"], cond["height"]), (2048, 2048))
        self.assertEqual(cond["prompt"], "a cat on a windowsill")
        self.assertEqual(cond["vae"], ["3", 0], "с VAE картинки ещё и якорятся латентами")
        self.assertEqual([k for k in cond if k.startswith("images")], [], "без референсов слотов нет")

    def test_the_latent_comes_from_the_conditioning_node(self):
        sample = one(self.g, "KSampler")
        self.assertEqual(sample["inputs"]["latent_image"], ["5", 1])
        self.assertEqual(one(self.g, "VAEDecode")["inputs"]["samples"], ["7", 0])

    def test_the_attention_is_the_same_comfy_kitchen_as_in_the_other_modes(self):
        att = one(self.g, "ModelAttentionBackend")
        self.assertEqual(att["inputs"]["attention"], "comfy kitchen attention")
        self.assertEqual(att["inputs"]["model"], ["1", 0])

    def test_it_is_saved_as_a_picture_of_the_h3img_family(self):
        save = one(self.g, "SaveImage")
        self.assertTrue(save["inputs"]["filename_prefix"].startswith("H3Workspace/h3img"))
        self.assertEqual(self.stages["7"], "sample")
        self.assertEqual(self.stages["9"], "save")


class H3ImgEditTest(unittest.TestCase):
    def test_up_to_five_references(self):
        g, _ = wf.h3img_edit_graph(MODELS, "p", REFS, 8.0, 3)
        cond = g["5"]["inputs"]
        self.assertEqual([k for k in cond if k.startswith("images")],
                         [f"images.image_{i}" for i in range(wf.H3IMG_MAX_REFS)])
        self.assertEqual(len(nodes(g, "LoadImage")), wf.H3IMG_MAX_REFS, "шестой файл отбрасывается")

    def test_fewer_references_are_all_used(self):
        g, _ = wf.h3img_edit_graph(MODELS, "p", REFS[:2], 8.0, 3)
        self.assertEqual([k for k in g["5"]["inputs"] if k.startswith("images")], ["images.image_0", "images.image_1"])
        self.assertEqual(len(nodes(g, "LoadImage")), 2)

    def test_the_shape_comes_from_the_first_picture(self):
        g, _ = wf.h3img_edit_graph(MODELS, "p", REFS[:2], 8.0, 3)
        cond = g["5"]["inputs"]
        size = g["272"]
        self.assertEqual(size["class_type"], "GetImageSize")
        self.assertEqual(cond["width"], ["272", 0])
        self.assertEqual(cond["height"], ["272", 1])
        # 272 меряет 270 (кратность 32), 270 берёт 301 (площадь 8 МП), 301 — картинку 300 (LoadImage)
        rounded = g[str(linked(g, size["inputs"]["image"])["inputs"]["input"][0])]
        self.assertEqual(rounded["class_type"], "ResizeImageMaskNode")
        scaled = linked(g, rounded["inputs"]["input"])
        self.assertEqual(scaled["class_type"], "LoadImage")
        self.assertEqual(scaled["inputs"]["image"], "ref1.png", "меряем именно первую картинку")

    def test_a_size_of_its_own_wins(self):
        g, _ = wf.h3img_edit_graph(MODELS, "p", REFS[:1], 16.0, 3, size=(5344, 3008))
        self.assertEqual((g["5"]["inputs"]["width"], g["5"]["inputs"]["height"]), (5344, 3008))

    def test_the_third_reference_no_longer_overwrites_the_shape(self):
        """The ids of the loaders sit in their own hundreds: 220/221 used to be both a loader and the shape."""
        g, _ = wf.h3img_edit_graph(MODELS, "p", REFS[:5], 8.0, 3)
        self.assertEqual(g["272"]["class_type"], "GetImageSize", "узел измерения должен остаться собой")
        self.assertEqual(g["270"]["class_type"], "ResizeImageMaskNode")
        self.assertEqual(g["5"]["inputs"]["width"], ["272", 0])
        # и у каждого референса свой загрузчик, никто никого не затёр
        self.assertEqual(len({n["inputs"]["image"] for _, n in nodes(g, "LoadImage")}), wf.H3IMG_MAX_REFS)

    def test_the_references_are_fed_smaller_than_the_result_when_asked(self):
        g, _ = wf.h3img_edit_graph(MODELS, "p", REFS[:1], 16.0, 3, None, 4.0)
        big = g["271"]   # the same picture at the area of the result: the shape
        self.assertEqual(big["inputs"]["resize_type.megapixels"], 16.0)
        self.assertEqual(g["301"]["inputs"]["resize_type.megapixels"], 4.0, "модель видит референс на 4 МП")
        self.assertEqual(g["5"]["inputs"]["images.image_0"], ["301", 0], "референс идёт с уменьшенной картинки")

    def test_cfg_one_with_a_zeroed_negative(self):
        g, _ = wf.h3img_edit_graph(MODELS, "p", REFS[:1], 8.0, 3)
        ks = one(g, "KSampler")
        self.assertEqual(ks["inputs"]["cfg"], 1)
        self.assertEqual(ks["inputs"]["negative"], ["6", 0])
        self.assertEqual(g["6"]["class_type"], "ConditioningZeroOut")

    def test_the_pictures_are_scaled_by_area_not_by_a_fixed_side(self):
        g, _ = wf.h3img_edit_graph(MODELS, "p", REFS[:1], 8.0, 3)
        resize = g["301"]
        self.assertEqual(resize["inputs"]["resize_type"], "scale total pixels")
        self.assertEqual(resize["inputs"]["scale_method"], "area")


class H3ImgSheetTest(unittest.TestCase):
    def test_a_four_panel_sheet_keeps_the_usual_size(self):
        g, stages, prompt = wf.h3img_sheet_graph(MODELS, "hero.png", 5)
        self.assertEqual((g["5"]["inputs"]["width"], g["5"]["inputs"]["height"]), wf.H3IMG_SHEET_SIZE)
        self.assertIn("<Picture 1>", prompt)
        self.assertNotIn("<image1>", prompt)

    def test_a_six_panel_sheet_has_its_own_wide_size(self):
        g, _, _ = wf.h3img_sheet_graph(MODELS, "hero.png", 5, panels=6)
        self.assertEqual((g["5"]["inputs"]["width"], g["5"]["inputs"]["height"]), wf.H3IMG_SHEET6_SIZE)

    def test_an_area_given_by_the_section_sets_the_sheet_size(self):
        g, _, _ = wf.h3img_sheet_graph(MODELS, "hero.png", 5, panels=4, mp=16.0)
        want = wf.h3img_size("16:9", 16.0)
        self.assertEqual((g["5"]["inputs"]["width"], g["5"]["inputs"]["height"]), want)
        self.assertGreaterEqual(wf.H3IMG_SHEET_SIZE[0] * wf.H3IMG_SHEET_SIZE[1], 8_000_000,
                                "лист меньше 8 МП у этой модели получается зернистым")

    def test_the_sheet_is_one_picture_from_one_reference(self):
        g, _, _ = wf.h3img_sheet_graph(MODELS, "hero.png", 5)
        self.assertEqual(len(nodes(g, "LoadImage")), 1)
        self.assertEqual(len(nodes(g, "SaveImage")), 1)


class H3ImgStoryTest(unittest.TestCase):
    def test_the_story_is_one_picture_from_five_pictures(self):
        g, stages = wf.h3img_story_graph(MODELS, REFS[:3], "the hero in the place", 9, size=(3840, 2176))
        self.assertEqual(len(nodes(g, "SaveImage")), 1, "раскадровка одним листом — одно изображение")
        self.assertEqual(len(nodes(g, "LoadImage")), 3)
        self.assertEqual((g["5"]["inputs"]["width"], g["5"]["inputs"]["height"]), (3840, 2176))
        self.assertEqual(stages["7"], "sample")


class H3ImgFramesTest(unittest.TestCase):
    def test_every_frame_is_its_own_picture_plus_the_glued_sheet(self):
        g, stages, titles = wf.h3img_frames_graph(MODELS, REFS[:2], ["кадр 1", "кадр 2", "кадр 3"], 4)
        frames = [k for k, n in nodes(g, "SaveImage") if n["inputs"]["filename_prefix"].endswith("story_frame")]
        self.assertEqual(len(frames), 3)
        self.assertEqual([titles[k] for k in frames], ["Кадр 1", "Кадр 2", "Кадр 3"])
        self.assertEqual(titles["500"], "Лист раскадровки")
        self.assertTrue(g["500"]["inputs"]["filename_prefix"].endswith("story_sheet"))
        self.assertEqual(len(nodes(g, "KSampler")), 3, "каждый кадр — своя выборка")
        self.assertEqual(len(nodes(g, "LoadImage")), 2, "референсы грузятся один раз на все кадры")

    def test_the_frames_take_at_most_five_references(self):
        g, _, _ = wf.h3img_frames_graph(MODELS, REFS, ["a", "b"], 4)
        self.assertEqual(len(nodes(g, "LoadImage")), wf.H3IMG_MAX_REFS)

    def test_the_frames_do_not_collide_with_the_sheet(self):
        g, _, _ = wf.h3img_frames_graph(MODELS, REFS[:1], ["a", "b", "c", "d"], 4)
        ids = [k for k in g]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotIn("500", [k for k, n in nodes(g, "KSampler")])


class H3ImgSizesTest(unittest.TestCase):
    def test_the_areas_are_the_offered_ones(self):
        self.assertEqual(sorted(wf.H3IMG_MP), ["16", "4", "8"])
        for key, mp in wf.H3IMG_MP.items():
            w, h = wf.h3img_size("16:9", mp)
            self.assertAlmostEqual(w * h / mp / 1e6, 1.0, delta=0.03, msg=f"{key} МП")
            self.assertEqual(w % 32, 0)
            self.assertEqual(h % 32, 0)

    def test_the_frame_size_stays_under_the_area_of_a_sheet(self):
        w, h = wf.H3IMG_FRAME_SIZE
        self.assertLessEqual(w * h, 8_000_000, "кадр раскадровки не больше 8 МП: 9 кадров по 16 МП считались бы час")

    def test_the_tags_are_renamed_for_the_model(self):
        self.assertEqual(wf.h3img_tags("the character from <image1> in <image2>"), "the character from <Picture 1> in <Picture 2>")
        self.assertEqual(wf.h3img_tags("<image 3>"), "<Picture 3>")
        self.assertEqual(wf.h3img_tags("<Picture 1>"), "<Picture 1>", "уже переведённый текст не портится")

    def test_a_broken_aspect_does_not_crash(self):
        self.assertEqual(wf.h3img_size("", 4.0), wf.h3img_size("1:1", 4.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
