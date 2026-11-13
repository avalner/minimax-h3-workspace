"""The DMAD file of the action sections: a 3.9 GB LoRA from Civitai that is put in place for the user.

The section's «Я скачал — поставить файл» looks in «Загрузки» and on the desktop, refuses a small file (the «turbo» LoRAs
are hundreds of megabytes and a different training) and puts the real one into the loras folder under the name the app
looks for. No ComfyUI and no real 3.9 GB file: the size floor is lowered for the test.

    python tests/unit/test_dmad.py
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

import support  # noqa: F401  (puts app/ on sys.path and a temporary data folder)

import catalog
from aiohttp import web

REAL = "minimax_h3_fl2v_lightx2v_v0.1_dareties_v4_step600_comfy_fro.safetensors"


class DmadInstallTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = support.fresh_app()
        self.home = support.new_dir("home")
        self.models = support.new_dir("models")   # the user's own ComfyUI models folder
        self.app.config.update(engine="external", ext_models_dir=str(self.models))

    def download(self, name, size=4096):
        d = self.home / "Downloads"
        d.mkdir(parents=True, exist_ok=True)
        f = d / name
        f.write_bytes(b"\0" * size)
        return f

    async def test_the_downloaded_file_is_moved_in_and_renamed(self):
        with mock.patch("pathlib.Path.home", return_value=self.home):
            src = self.download(REAL)
            self.app.DMAD_MIN_BYTES = 1
            reply = await self.app.h_dmad_install(None)
        data = json.loads(reply.text)
        self.assertTrue(data["ok"])
        self.assertEqual(data["name"], self.app.DMAD_SAVED_AS, "файл кладётся под именем, которое ищет программа")
        dst = self.models / "loras" / data["name"]
        self.assertTrue(dst.is_file())
        self.assertFalse(src.exists(), "из «Загрузок» файл переносится, а не копируется")
        self.assertTrue(catalog.is_action_dmad(data["name"]))

    async def test_a_small_file_is_a_different_lora_and_is_refused(self):
        with mock.patch("pathlib.Path.home", return_value=self.home):
            self.download("minimax_h3_dmad_full_dareties_v4_step600_comfy.safetensors", 4096)
            with self.assertRaises(web.HTTPBadRequest) as caught:
                await self.app.h_dmad_install(None)
        self.assertIn("слишком маленький", caught.exception.text)

    async def test_nothing_found_says_what_to_do(self):
        with mock.patch("pathlib.Path.home", return_value=self.home):
            with self.assertRaises(web.HTTPNotFound) as caught:
                await self.app.h_dmad_install(None)
        self.assertIn("minimax_h3_dmad_full_dareties_v4_step600_comfy.safetensors", caught.exception.text)

    async def test_the_folder_of_an_own_comfyui_is_where_the_file_goes(self):
        self.assertEqual(self.app.loras_dir(), self.models / "loras")

    async def test_without_a_known_models_folder_the_section_says_so(self):
        self.app.config.update(engine="external", ext_models_dir="")
        with self.assertRaises(web.HTTPBadRequest) as caught:
            await self.app.h_dmad_install(None)
        self.assertIn("не известна", caught.exception.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
