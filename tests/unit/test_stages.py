"""Stages live in the database and belong to a project (migration 0004).

The point of the move: a stage is a record of the project, not of one browser profile — it is the
same after a restart, and a section shows the stages of the project that is open.
"""

import json
import unittest

from aiohttp import web

import support
from domain.projects import DEFAULT_PROJECT_ID


class Part:
    """One uploaded file part, as `request.multipart()` hands it to the handler."""

    name = "file"

    def __init__(self, chunks, filename="frame.png"):
        self.filename = filename
        self._chunks = list(chunks)

    async def read_chunk(self, size):
        return self._chunks.pop(0) if self._chunks else b""


class UploadReq(support.Req):
    def __init__(self, part, **kwargs):
        super().__init__(**kwargs)
        self._part = part

    async def multipart(self):
        return self

    async def next(self):
        part, self._part = self._part, None
        return part


class StageApiTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = support.fresh_app()

    async def save(self, body):
        return await self.app.h_stage_save(support.Req(body))

    async def stages(self):
        return json.loads((await self.app.h_stages(None)).text)["stages"]

    async def test_a_stage_carries_the_active_project_and_comes_back(self):
        reply = await self.save({"mode": "video", "name": "Вечер", "s": {"q": "720p"}, "prompt": "тест",
                                 "files": ["video/first"], "date": "2026-11-13T12:00:00"})
        self.assertEqual(reply.status, 201)
        saved = json.loads(reply.text)["stage"]
        self.assertEqual(saved["project"], DEFAULT_PROJECT_ID)
        listed = await self.stages()
        self.assertEqual([stage["id"] for stage in listed], [saved["id"]])
        self.assertEqual(listed[0]["s"], {"q": "720p"})
        self.assertEqual(listed[0]["files"], ["video/first"])

    async def test_each_project_keeps_its_own_stages(self):
        first = json.loads((await self.save({"mode": "video", "name": "Тут"})).text)["stage"]
        project = self.app.create_project("Второй")
        second = json.loads((await self.save({"mode": "video", "name": "Там"})).text)["stage"]
        self.assertEqual(first["project"], DEFAULT_PROJECT_ID)
        self.assertEqual(second["project"], project["id"])

    async def test_the_before_opening_stage_is_replaced_not_multiplied(self):
        await self.save({"mode": "video", "name": "Было до открытия этапа", "auto": True})
        await self.save({"mode": "video", "name": "Было до открытия этапа", "auto": True})
        self.assertEqual(len([stage for stage in await self.stages() if stage["auto"]]), 1)

    async def test_the_list_is_kept_at_its_ceiling(self):
        for number in range(17):
            await self.save({"mode": "video", "name": f"этап {number}",
                             "date": f"2026-11-{number + 1:02d}T10:00:00"})
        mine = [stage for stage in await self.stages() if stage["mode"] == "video" and not stage["auto"]]
        self.assertEqual(len(mine), 15, "старые этапы должны уходить, новые — оставаться")
        self.assertNotIn("этап 0", {stage["name"] for stage in mine})

    async def test_a_stage_can_be_deleted(self):
        saved = json.loads((await self.save({"mode": "video", "name": "Удалить"})).text)["stage"]
        reply = await self.app.h_stage_delete(support.Req(match_info={"id": saved["id"]}))
        self.assertEqual(reply.status, 200)
        self.assertEqual(await self.stages(), [])

    async def test_a_stage_needs_a_mode(self):
        with self.assertRaises(web.HTTPBadRequest):
            await self.save({"name": "без раздела"})

    async def test_stages_survive_a_restart(self):
        saved = json.loads((await self.save({"mode": "video", "name": "Переживёт"})).text)["stage"]
        self.app.db.close()
        self.app = support.new_app()
        self.assertEqual([stage["id"] for stage in await self.stages()], [saved["id"]])

    async def test_a_file_is_uploaded_served_and_deleted_with_its_stage(self):
        saved = json.loads((await self.save({"mode": "video", "name": "С файлом", "files": ["video/first"],
                                             "names": {"video/first": "frame.png"}})).text)["stage"]
        reply = await self.app.h_stage_file_put(
            UploadReq(Part([b"abc", b"de"]), match_info={"id": saved["id"]},
                      query={"key": "video/first"}))
        self.assertEqual(reply.status, 200)
        path = self.app.stages_repo.file_path(saved["id"], "video/first")
        self.assertEqual(path.read_bytes(), b"abcde")
        served = await self.app.h_stage_file_get(
            support.Req(match_info={"id": saved["id"]}, query={"key": "video/first"}))
        self.assertEqual(served.content_type, "image/png")
        self.assertIn("frame.png", served.headers["Content-Disposition"])
        await self.app.h_stage_delete(support.Req(match_info={"id": saved["id"]}))
        self.assertFalse(path.exists(), "файлы этапа удаляются вместе с записью")

    async def test_a_file_that_was_not_saved_with_the_stage_is_refused(self):
        saved = json.loads((await self.save({"mode": "video", "name": "Без файла",
                                             "files": ["video/first"]})).text)["stage"]
        with self.assertRaises(web.HTTPBadRequest):
            await self.app.h_stage_file_put(
                UploadReq(Part([b"x"]), match_info={"id": saved["id"]}, query={"key": "video/last"}))
