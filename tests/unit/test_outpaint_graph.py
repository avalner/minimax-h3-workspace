"""«Расширение»: the model paints the new edges, the original stays whole and the seam is soft.

The v2 recipe of the outpaint (Qwen Image 2.1): the canvas goes to the model at its own size, the outpaint LoRA sits
between the model and the sampler when it is installed, and the untouched original is pasted back through a mask that
is shrunk by a few pixels and blurred — a hard paste leaves a visible edge. A call without the place of the original
(an old window) keeps the old hard paste.

    python tests/unit/test_outpaint_graph.py
"""

from __future__ import annotations

import unittest

import support  # noqa: F401  (puts app/ on sys.path)

import catalog
import workflows as wf

MODELS = {"qwen_unet": "Images\\qwen_image_2.1_int8_convrot.safetensors",
          "qwen_clip": "Images\\qwen3vl_8b_int8_convrot.safetensors",
          "qwen_vae": "Images\\qwen_image_2.1_vae_bf16.safetensors"}
LORA = "Images\\qwen-image-2.1-outpaint-v2.safetensors"
CANVAS = (1536, 1024)
PLACE = (1024, 1024)


def node(g, cls):
    """The first node of a class (the graphs name nodes by number, the class is what matters)."""
    return next((n for n in g.values() if n["class_type"] == cls), None)


class OutpaintGraphTest(unittest.TestCase):
    def build(self, lora=None, osize=PLACE):
        return wf.qwen_outpaint_graph(MODELS, wf.OUTPAINT_PROMPT, "canvas.png", "orig.png", 256, 0, CANVAS, 7, lora, osize)

    def test_the_model_sees_the_canvas_at_its_own_size(self):
        g, _ = self.build()
        scale = node(g, "ImageScaleToTotalPixels")
        self.assertAlmostEqual(scale["inputs"]["megapixels"], CANVAS[0] * CANVAS[1] / 1e6, places=3,
                               msg="холст должен уходить в модель в своём размере, иначе шов съедет на пиксели")

    def test_the_lora_is_between_the_model_and_the_sampler(self):
        without, _ = self.build()
        with_lora, _ = self.build(lora=LORA)
        self.assertIsNone(node(without, "LoraLoaderModelOnly"), "без файла LoRA граф не должен её грузить")
        lora = node(with_lora, "LoraLoaderModelOnly")
        self.assertEqual(lora["inputs"]["lora_name"], LORA)
        self.assertEqual(lora["inputs"]["model"], ["4", 0], "LoRA ставится после основной модели")
        self.assertEqual(node(with_lora, "KSampler")["inputs"]["model"], ["23", 0],
                         "семплер должен брать модель с уже подключённой LoRA")
        self.assertEqual(node(without, "KSampler")["inputs"]["model"], ["4", 0])

    def test_the_original_is_pasted_through_a_soft_mask(self):
        g, stages = self.build()
        grow, blur = node(g, "GrowMask"), node(g, "ImageBlur")
        self.assertEqual(grow["inputs"]["expand"], -6, "маска оригинала ужимается внутрь, чтобы край растворился")
        self.assertEqual(blur["inputs"]["blur_radius"], 5)
        composite = node(g, "ImageCompositeMasked")
        self.assertEqual(composite["inputs"]["x"], 0)
        self.assertEqual(composite["inputs"]["y"], 0)
        self.assertEqual(composite["inputs"]["destination"], ["10", 0], "клеим на нарисованный холст")
        self.assertEqual(composite["inputs"]["source"], ["5", 0], "источник — исходное изображение, не копия")
        self.assertEqual(node(g, "SaveImage")["inputs"]["images"], ["22", 0], "наружу идёт результат склейки")
        self.assertEqual(stages["22"], "decode")

    def test_the_original_is_not_loaded_twice(self):
        g, _ = self.build()
        self.assertNotIn("20", g, "при мягком шве оригинал берётся из той же ячейки, второй LoadImage не нужен")

    def test_an_old_window_without_the_place_keeps_the_hard_paste(self):
        g, stages = self.build(osize=None)
        self.assertNotIn("36", g, "без места оригинала мягкий шов построить нельзя")
        composite = node(g, "ImageCompositeMasked")
        self.assertEqual(composite["inputs"]["source"], ["20", 0])
        self.assertEqual((composite["inputs"]["x"], composite["inputs"]["y"]), (256, 0))
        self.assertEqual(stages["20"], "load")

    def test_the_instruction_is_the_one_the_lora_was_trained_on(self):
        self.assertTrue(wf.OUTPAINT_PROMPT.startswith("Outpaint the image: replace the solid gray areas"),
                        "LoRA обучена на этой фразе: другая формулировка оставляет заливку как есть")
        self.assertIn("gray", wf.OUTPAINT_PROMPT_LEGACY.lower() + wf.OUTPAINT_PROMPT.lower())


class OutpaintLoraTest(unittest.TestCase):
    def test_the_lora_is_an_extra_of_the_picture_editor(self):
        self.assertIn("outpaint_lora", catalog.VARIANTS, "роль должна быть в каталоге: её скачивает «Модели»")
        self.assertIn("outpaint_lora", catalog.MODE_OPTIONAL.get("qwen", []))
        self.assertNotIn("outpaint_lora", catalog.MODE_ROLES["qwen"], "без LoRA правка работает, это не обязательный файл")
        self.assertIn("outpaint_lora", catalog.roles_for(["qwen"]), "она входит в план установки раздела")
        self.assertEqual(catalog.VARIANTS["outpaint_lora"]["v2"]["category"], "loras")
        self.assertTrue(catalog.looks_right("outpaint_lora", "qwen-image-2.1-outpaint-v2.safetensors"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
