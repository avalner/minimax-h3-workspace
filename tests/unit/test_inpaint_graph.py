"""«Инпейнт»: the redrawn area is grown before it is blurred, and the prompt asks to carry the surroundings into it.

The seam of the old recipe: the painted area became a soft mask exactly on the brush stroke, so the lines and hatching
of the neighbourhood stopped dead at the edge of what was redrawn. Now the mask is grown by six pixels before the blur
(the soft edge sits outside the stroke, and the model joins the neighbouring drawing instead of cutting it) and the
server adds a sentence about the surroundings after the user's own words.

    python tests/unit/test_inpaint_graph.py
"""

from __future__ import annotations

import unittest

from aiohttp import web

import support  # noqa: F401  (puts app/ on sys.path and a temporary data folder)

import workflows as wf

MODELS = {"qwen_unet": "Images\\qwen_image_2.1_int8_convrot.safetensors",
          "qwen_clip": "Images\\qwen3vl_8b_int8_convrot.safetensors",
          "qwen_vae": "Images\\qwen_image_2.1_vae_bf16.safetensors"}
CROP = (192, 160)


def node(g, cls):
    """The first node of a class (the graphs name nodes by number, the class is what matters)."""
    return next((n for n in g.values() if n["class_type"] == cls), None)


def node_id(g, cls):
    return next((k for k, n in g.items() if n["class_type"] == cls), None)


class InpaintGraphTest(unittest.TestCase):
    def build(self, **kw):
        return wf.qwen_inpaint_graph(MODELS, wf.inpaint_prompt("дорисуй красную чашку"),
                                     "image.png", "crop.png", "mask.png", 64, 32, 1.0, 7, CROP, **kw)

    def test_the_stroke_is_grown_before_the_blur(self):
        g, _ = self.build()
        grow = node(g, "GrowMask")
        self.assertIsNotNone(grow, "без GrowMask блюр размывает край внутрь и наружу и шов остаётся")
        self.assertEqual(grow["inputs"]["expand"], 6, "маска расширяется на 6 px до блюра")
        self.assertEqual(g["13"]["inputs"]["mask"], [node_id(g, "GrowMask"), 0],
                         "в MaskToImage должна идти уже расширенная маска")
        self.assertEqual(grow["inputs"]["mask"], [node_id(g, "InvertMask"), 0],
                         "расширяется маска закраски, а не что-то ещё")
        self.assertEqual(g["14"]["inputs"]["image"], ["13", 0], "блюр стоит после расширения")

    def test_the_grow_is_a_knob_not_a_fixed_number(self):
        g, _ = self.build(grow=0)
        self.assertEqual(node(g, "GrowMask")["inputs"]["expand"], 0, "расширение можно выключить, не трогая граф")

    def test_the_same_soft_mask_feeds_the_sampler_and_the_paste(self):
        g, _ = self.build()
        self.assertEqual(node(g, "ImageCompositeMasked")["inputs"]["mask"], ["15", 0])
        self.assertEqual(node(g, "SetLatentNoiseMask")["inputs"]["mask"], ["17", 0])
        self.assertEqual(g["15"]["inputs"]["image"], ["14", 0], "вклейка идёт по размытой расширенной маске")
        self.assertEqual(g["16"]["inputs"]["image"], ["14", 0],
                         "маска для семплера — та же размытая, только в масштабе модели")

    def test_the_new_node_is_marked_in_the_stages(self):
        g, stages = self.build()
        self.assertEqual(stages[node_id(g, "GrowMask")], "encode", "нода должна попасть в стадии прогресса")


class InpaintPromptTest(unittest.TestCase):
    def test_the_sentence_asks_for_the_lines_and_hatching_around(self):
        text = wf.INPAINT_PROMPT.lower()
        for word in ("lines", "hatching", "surrounding", "no visible edge"):
            self.assertIn(word, text, "фраза должна просить продолжить линии и штриховку вокруг")

    def test_the_user_words_come_first(self):
        prompt = wf.inpaint_prompt("  дорисуй красную чашку  ")
        self.assertTrue(prompt.startswith("дорисуй красную чашку"), "что рисовать, по-прежнему говорит человек")
        self.assertTrue(prompt.endswith(wf.INPAINT_PROMPT))

    def test_the_sentence_is_never_doubled(self):
        self.assertEqual(wf.inpaint_prompt(wf.INPAINT_PROMPT), wf.INPAINT_PROMPT)
        self.assertEqual(wf.inpaint_prompt("чашка " + wf.INPAINT_PROMPT).count(wf.INPAINT_PROMPT), 1,
                         "агент может написать фразу сам — второй раз её добавлять нельзя")

    def test_an_empty_text_is_only_the_sentence(self):
        self.assertEqual(wf.inpaint_prompt(""), wf.INPAINT_PROMPT)
        self.assertEqual(wf.inpaint_prompt(None), wf.INPAINT_PROMPT)


class _Field:
    def __init__(self, name, value=None, filename=None):
        self.name, self.value, self.filename = name, value, filename
        self.headers = {}

    async def text(self):
        return self.value or ""

    async def read(self):
        return b"pixels"


class _Form:
    """The part of an aiohttp request the picture handlers use: a multipart form."""

    def __init__(self, fields):
        self.fields = fields

    async def multipart(self):
        return _Iter(self.fields)


class _Iter:
    def __init__(self, items):
        self.items = list(items)

    def __aiter__(self):
        self._i = iter(self.items)
        return self

    async def __anext__(self):
        try:
            return next(self._i)
        except StopIteration:
            raise StopAsyncIteration


def form(**fields):
    return [_Field(k, "" if v is None else str(v)) for k, v in fields.items()]


def painted(names=("image", "crop", "mask")):
    return [_Field(n, filename=n + ".png") for n in names]


class InpaintRouteTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = support.fresh_app()
        self.app.config.update(engine="external", modes=["qwen"])
        self.app.model_names = dict(MODELS)
        self.app.missing = []
        self.sent = None

        async def upload(field, rh=None):
            return "h3studio/" + (field.filename or "x.png")

        async def queue(graph, stages, meta, last_output=False, titles=None, preview=False, spec=None):
            self.sent = {"graph": graph, "stages": stages, "meta": meta, "preview": preview}
            return web.json_response({"ok": True})

        self.app.upload_input = upload
        self.app.queue = queue

    async def call(self, text="дорисуй красную чашку", **extra):
        fields = form(prompt=text, seed="42", x="10", y="20", crop_w=str(CROP[0]), crop_h=str(CROP[1]),
                      size="1", **extra) + painted()
        response = await self.app.h_generate_inpaint(_Form(fields))
        self.assertEqual(response.status, 200)
        return self.sent

    async def test_the_route_sends_the_surroundings_sentence_with_the_job(self):
        sent = await self.call()
        self.assertEqual(sent["meta"]["sub"], "Инпейнт")
        self.assertEqual(sent["meta"]["prompt"], wf.inpaint_prompt("дорисуй красную чашку"),
                         "в Библиотеке должен лежать тот промпт, который ушёл в модель")
        encoder = next(n for n in sent["graph"].values() if n["class_type"] == "TextEncodeQwenImage21")
        self.assertEqual(encoder["inputs"]["prompt"], sent["meta"]["prompt"])

    async def test_the_graph_of_the_route_carries_the_grow(self):
        sent = await self.call()
        grow = node(sent["graph"], "GrowMask")
        self.assertIsNotNone(grow)
        self.assertEqual(grow["inputs"]["expand"], 6)
        self.assertEqual(sent["stages"][node_id(sent["graph"], "GrowMask")], "encode")

    async def test_without_words_there_is_nothing_to_draw(self):
        fields = form(prompt="", seed="42", x="0", y="0", crop_w="192", crop_h="160", size="1") + painted()
        with self.assertRaises(web.HTTPBadRequest):
            await self.app.h_generate_inpaint(_Form(fields))


if __name__ == "__main__":
    unittest.main(verbosity=2)
