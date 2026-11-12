"""«Добавить в библиотеку»: the draft moves into the Library once, and a failed write keeps it a draft.

    python tests/unit/test_preview_keep.py
"""

from __future__ import annotations

import json
import sqlite3
import unittest
from unittest import mock

import support

import server
from aiohttp import web


class PreviewKeepTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = support.fresh_app()
        self.item = {"id": "d1", "kind": "image", "mode": "krea", "prompt": "тест", "preview": True,
                     "file": {"filename": "pic.png", "subfolder": "", "type": "output"}}
        self.app.hold_preview(dict(self.item))

    async def keep(self, item_id="d1"):
        return await self.app.h_preview_keep(support.Req({"id": item_id}))

    async def test_the_draft_moves_into_the_library(self):
        body = json.loads((await self.keep()).text)
        self.assertEqual(body["item"]["id"], "d1")
        self.assertEqual([x["id"] for x in self.app.library.items], ["d1"])
        self.assertEqual(self.app.pending, {})
        self.assertNotIn("preview", self.app.library.items[0])
        self.assertEqual([x["id"] for x in server.Library(self.app.db).items], ["d1"],
                         "запись должна быть в таблице, а не только в памяти")
        media = self.app.media_repo.get("d1")
        self.assertIsNotNone(media, "у файла нет записи в media")
        self.assertEqual(media.state.value, "library")
        self.assertEqual(media.library_id, "d1")

    async def test_a_second_press_returns_the_same_item(self):
        first = json.loads((await self.keep()).text)
        second = json.loads((await self.keep()).text)
        self.assertEqual(first["item"]["id"], second["item"]["id"])
        self.assertEqual(len(self.app.library.items), 1, "повторное нажатие создало вторую запись")
        self.assertTrue(second.get("kept"))

    async def test_a_failed_write_keeps_the_draft_and_the_button_works_again(self):
        with mock.patch.object(self.app.library, "add", side_effect=sqlite3.OperationalError("disk I/O error")):
            with self.assertRaises(web.HTTPInternalServerError):
                await self.keep()
        self.assertIn("d1", self.app.pending, "черновик потерян из-за неудачной записи")
        self.assertEqual(self.app.library.items, [])
        self.assertTrue(self.app.pending["d1"].get("preview"))
        body = json.loads((await self.keep()).text)   # the same draft can still be kept
        self.assertEqual(body["item"]["id"], "d1")
        self.assertEqual(self.app.pending, {})

    async def test_an_unknown_draft_is_a_bad_request(self):
        with self.assertRaises(web.HTTPBadRequest):
            await self.keep("nope")


if __name__ == "__main__":
    unittest.main()
