"""Stages live in the database and belong to a project (migration 0004).

The point of the move: a stage is a record of the project, not of one browser profile — it is the
same after a restart, and a section shows the stages of the project that is open.
"""

import json
import unittest

from aiohttp import web

import support
from domain.projects import DEFAULT_PROJECT_ID


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
