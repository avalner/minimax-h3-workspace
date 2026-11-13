"""The routes of MiniMax H3 Image: picture by text and the picture editor's tasks on the new model.

The handler is called with a form and a fake ComfyUI: what is checked is the choice of the recipe, the area in
megapixels (this model lives at 4-16 MP and its own size is not cut down to the 2 MP of the other picture modes),
the number of reference pictures, the words written into the job (the Library restores the section from them) and
the fallback of the tasks that stay on Qwen.

    python tests/unit/test_h3img_routes.py
"""

from __future__ import annotations

import unittest

from aiohttp import web

import support  # noqa: F401  (puts app/ on sys.path and a temporary data folder)

import workflows as wf

H3_MODELS = {"h3img_unet": "MinimaxH3\\h3img_w4a8.safetensors",
             "text_encoder": "MinimaxH3\\qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
             "h3img_vae": "MinimaxH3\\h3img_vae.safetensors",
             "qwen_unet": "Images\\qwen_unet.safetensors", "qwen_clip": "Images\\qwen_clip.safetensors",
             "qwen_vae": "Images\\qwen_vae.safetensors"}


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
    """Text fields of the form plus any number of uploaded pictures (`image`, `image2`, ...)."""
    out = []
    for key, value in fields.items():
        out.append(_Field(key, "" if value is None else str(value)))
    return out


def pics(names, fieldnames=("image", "image2", "image3", "image4", "image5")):
    return [_Field(fieldnames[i], filename=name) for i, name in enumerate(names)]


class RouteCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = support.fresh_app()
        self.app.config.update(engine="external", modes=["krea", "qwen", "flux", "h3img"])
        self.app.model_names = dict(H3_MODELS)
        self.app.missing = []
        self.sent = None

        async def upload(field, rh=None):
            return "h3studio/" + (field.filename or "x.png")

        async def queue(graph, stages, meta, last_output=False, titles=None, preview=False, spec=None):
            self.sent = {"graph": graph, "stages": stages, "meta": meta, "titles": titles, "preview": preview}
            return web.json_response({"ok": True})

        self.app.upload_input = upload
        self.app.queue = queue

    async def call(self, handler, fields):
        response = await handler(_Form(fields))
        self.assertEqual(response.status, 200)
        return self.sent

    def h3_graphs(self):
        return [n for n in self.sent["graph"].values() if n["class_type"] == "MiniMaxH3ImageConditioning"]

    def conditioning(self):
        found = self.h3_graphs()
        self.assertEqual(len(found), 1, "у H3 Image одна ветка обусловливания на изображение")
        return found[0]["inputs"]

    def loaders(self):
        return [n["inputs"]["image"] for n in self.sent["graph"].values() if n["class_type"] == "LoadImage"]


class TextToPictureTest(RouteCase):
    async def test_the_area_comes_from_the_chosen_megapixels(self):
        await self.call(self.app.h_generate_krea, form(prompt="a lighthouse", aspect="16:9", engine="h3img", mp="8"))
        self.assertEqual((self.conditioning()["width"], self.conditioning()["height"]), wf.h3img_size("16:9", 8.0))
        self.assertEqual(self.sent["meta"]["sub"], "Изображение (MiniMax H3 Image)")
        self.assertEqual(self.sent["meta"]["mode"], "krea")
        self.assertEqual(self.sent["meta"]["q"], "8 МП")
        self.assertEqual(self.sent["meta"]["res"], "%d×%d" % wf.h3img_size("16:9", 8.0))

    async def test_the_default_area_is_four_megapixels(self):
        await self.call(self.app.h_generate_krea, form(prompt="a lighthouse", aspect="1:1", engine="h3img"))
        self.assertEqual((self.conditioning()["width"], self.conditioning()["height"]), wf.h3img_size("1:1", 4.0))

    async def test_the_six_steps_and_cfg_one_are_in_the_graph(self):
        await self.call(self.app.h_generate_krea, form(prompt="a lighthouse", aspect="16:9", engine="h3img", mp="16"))
        sampler = next(n for n in self.sent["graph"].values() if n["class_type"] == "KSampler")
        self.assertEqual((sampler["inputs"]["steps"], sampler["inputs"]["cfg"]), (6, 1))
        self.assertEqual(sampler["inputs"]["scheduler"], "simple")

    async def test_a_big_own_size_is_not_cut_down_to_two_megapixels(self):
        await self.call(self.app.h_generate_krea,
                        form(prompt="a poster", aspect="16:9", engine="h3img", width="4096", height="4096"))
        w, h = self.conditioning()["width"], self.conditioning()["height"]
        self.assertEqual((w, h), (4096, 4096), "16,7 МП — рабочий размер этой модели, обрезать его нельзя")
        self.assertEqual(self.sent["meta"]["res"], "4096×4096")

    async def test_an_own_size_above_the_limit_is_scaled_down(self):
        await self.call(self.app.h_generate_krea,
                        form(prompt="a poster", aspect="16:9", engine="h3img", width="8192", height="8192"))
        w, h = self.conditioning()["width"], self.conditioning()["height"]
        self.assertLessEqual(w * h, 20_000_000 * 1.02)
        self.assertLessEqual(max(w, h), 8192)
        self.assertEqual(w % 32, 0)


class EditTest(RouteCase):
    async def test_three_pictures_go_in_at_their_own_places(self):
        fields = form(prompt="put them in one room", kind="edit", engine="h3img", size="8") + pics(["a.png", "b.png", "c.png"])
        await self.call(self.app.h_generate_qwen, fields)
        cond = self.conditioning()
        self.assertEqual([k for k in cond if k.startswith("images")], ["images.image_0", "images.image_1", "images.image_2"])
        self.assertEqual(self.loaders(), ["h3studio/a.png", "h3studio/b.png", "h3studio/c.png"])
        self.assertEqual(self.sent["meta"]["sub"], "Правка (MiniMax H3 Image, картинок: 3)")
        self.assertEqual(self.sent["meta"]["mode"], "qwen")
        self.assertEqual(self.sent["meta"]["q"], "8 МП")

    async def test_five_pictures_are_all_taken(self):
        names = [f"p{i}.png" for i in range(1, 6)]
        await self.call(self.app.h_generate_qwen,
                        form(prompt="a group photo", kind="edit", engine="h3img", size="16") + pics(names))
        self.assertEqual(len(self.loaders()), wf.H3IMG_MAX_REFS)
        self.assertEqual(len([k for k in self.conditioning() if k.startswith("images")]), wf.H3IMG_MAX_REFS)
        self.assertIn("картинок: 5", self.sent["meta"]["sub"])

    async def test_without_a_format_the_shape_comes_from_the_first_picture(self):
        await self.call(self.app.h_generate_qwen,
                        form(prompt="make it winter", kind="edit", engine="h3img", size="8", aspect="") + pics(["hero.png"]))
        cond = self.conditioning()
        self.assertIsInstance(cond["width"], list, "ширина берётся из картинки, а не из числа")
        self.assertEqual(cond["width"][1], 0)
        self.assertEqual(self.sent["meta"]["res"], "")

    async def test_a_chosen_format_sets_the_size(self):
        await self.call(self.app.h_generate_qwen,
                        form(prompt="make it winter", kind="edit", engine="h3img", size="16", aspect="9:16") + pics(["h.png"]))
        self.assertEqual((self.conditioning()["width"], self.conditioning()["height"]), wf.h3img_size("9:16", 16.0))

    async def test_the_references_are_sent_smaller_than_the_result(self):
        await self.call(self.app.h_generate_qwen,
                        form(prompt="make it winter", kind="edit", engine="h3img", size="16") + pics(["h.png"]))
        resizes = [n["inputs"]["resize_type.megapixels"] for n in self.sent["graph"].values()
                   if n["class_type"] == "ResizeImageMaskNode" and "resize_type.megapixels" in n["inputs"]]
        self.assertIn(16.0, resizes, "площадь результата")
        self.assertIn(4.0, resizes, "референс в модель идёт на 4 МП")

    async def test_a_job_carries_what_the_library_needs_to_restore_it(self):
        await self.call(self.app.h_generate_qwen,
                        form(prompt="make it winter", kind="edit", engine="h3img", size="8") + pics(["h.png"]))
        self.assertEqual(self.sent["meta"]["engine"], "h3img")
        self.assertEqual(self.sent["meta"]["mp"], "8")

    async def test_an_empty_prompt_is_refused(self):
        with self.assertRaises(web.HTTPBadRequest) as caught:
            await self.call(self.app.h_generate_qwen,
                            form(prompt="", kind="edit", engine="h3img", size="8") + pics(["h.png"]))
        self.assertIn("что сделать", caught.exception.text)

    async def test_removing_the_background_stays_on_qwen(self):
        """bg and outpaint are Qwen recipes: choosing H3 Image in that card must not build an H3 graph."""
        fields = form(prompt="", kind="bg", engine="h3img", size="1") + pics(["h.png"])
        await self.call(self.app.h_generate_qwen, fields)
        self.assertEqual(self.h3_graphs(), [])
        self.assertTrue([n for n in self.sent["graph"].values() if n["class_type"] == "TextEncodeQwenImage21"])


class SheetAndStoryTest(RouteCase):
    async def test_a_sheet_of_four_panels_at_sixteen_megapixels(self):
        await self.call(self.app.h_generate_qwen,
                        form(kind="sheet", engine="h3img", size="16", panels="4") + pics(["hero.png"]))
        w, h = self.conditioning()["width"], self.conditioning()["height"]
        self.assertEqual((w, h), wf.h3img_size("16:9", 16.0))
        self.assertIn("<Picture 1>", self.conditioning()["prompt"])
        self.assertEqual(self.sent["meta"]["sub"], "Лист персонажа")
        self.assertEqual(self.sent["meta"]["engine"], "h3img")

    async def test_a_six_panel_sheet_is_wider(self):
        await self.call(self.app.h_generate_qwen,
                        form(kind="sheet", engine="h3img", size="8", panels="6") + pics(["hero.png"]))
        w, h = self.conditioning()["width"], self.conditioning()["height"]
        self.assertEqual((w, h), wf.h3img_size("11:5", 8.0))
        self.assertGreater(w, h)

    async def test_a_default_sheet_is_sixteen_megapixels(self):
        await self.call(self.app.h_generate_qwen, form(kind="sheet", engine="h3img") + pics(["hero.png"]))
        w, h = self.conditioning()["width"], self.conditioning()["height"]
        self.assertEqual((w, h), wf.h3img_size("16:9", 16.0))

    async def test_the_storyboard_as_one_picture(self):
        await self.call(self.app.h_generate_qwen,
                        form(kind="story", engine="h3img", size="16", prompt="three scenes") + pics(["hero.png", "place.png"]))
        self.assertEqual(len(self.loaders()), 2)
        self.assertEqual(len([n for n in self.sent["graph"].values() if n["class_type"] == "SaveImage"]), 1)
        self.assertEqual((self.conditioning()["width"], self.conditioning()["height"]), wf.h3img_size("16:9", 16.0))
        self.assertEqual(self.sent["meta"]["engine"], "h3img")

    async def test_the_storyboard_frame_by_frame_stops_at_eight_megapixels_a_frame(self):
        frames = "1: first scene\n2: second scene\n3: third scene\n4: fourth scene"
        await self.call(self.app.h_generate_qwen,
                        form(kind="story", frames="1", engine="h3img", size="16", prompt=frames) + pics(["hero.png"]))
        sizes = {(n["inputs"]["width"], n["inputs"]["height"]) for n in self.sent["graph"].values()
                 if n["class_type"] == "MiniMaxH3ImageConditioning"}
        self.assertEqual(len(sizes), 1)
        w, h = sizes.pop()
        self.assertEqual((w, h), wf.h3img_size("16:9", 8.0), "кадр крупнее 8 МП не рисуется: девять таких — час")
        self.assertEqual(len(self.sent["titles"]), 5, "четыре кадра и склеенный лист")


class ModelsMissingTest(RouteCase):
    async def test_a_mode_that_is_not_installed_says_so(self):
        self.app.config.update(modes=["qwen"])
        with self.assertRaises(web.HTTPBadRequest) as caught:
            await self.call(self.app.h_generate_krea, form(prompt="x", aspect="1:1", engine="h3img", mp="4"))
        self.assertIn("не установлен", caught.exception.text)

    async def test_a_missing_file_is_named(self):
        self.app.missing = [{"file": "minimax_h3_image_vae_fp16.safetensors", "role": "h3img_vae"}]
        with self.assertRaises(web.HTTPBadRequest) as caught:
            await self.call(self.app.h_generate_krea, form(prompt="x", aspect="1:1", engine="h3img", mp="4"))
        self.assertIn("minimax_h3_image_vae_fp16.safetensors", caught.exception.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
