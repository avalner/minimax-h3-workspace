"""A film of the long studio belongs to a project (not to «the last one saved»).

The studio used to load whichever run was saved last, so a project could continue another project's
film. The record on the disk now carries its project, and a request without a run id answers with the
freshest film of the project that asks.

    python tests/unit/test_long_project.py
"""

from __future__ import annotations

import json
import time
import unittest

from aiohttp import web

import support
from domain.projects import DEFAULT_PROJECT_ID


class Post(support.Req):
    method = "POST"


class Get(support.Req):
    method = "GET"


class LongProjectTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = support.fresh_app()

    async def save(self, run, *, project=None, project_id=None):
        body = {"run": run, "project": {"run": run, "t": 1,
                                        "scenes": [{"prompt": run, "sec": 10}]}}
        if project:
            body["project"]["project"] = project
        if project_id:
            body["project_id"] = project_id
        return json.loads((await self.app.h_long_project(Post(body))).text)

    async def film(self, **query):
        return json.loads((await self.app.h_long_project(Get(query=query))).text)

    async def test_a_film_is_stamped_with_the_active_project(self):
        saved = await self.save("run-one")
        self.assertEqual(saved["project"], DEFAULT_PROJECT_ID)
        self.assertEqual((await self.film(run="run-one"))["project"]["project"], DEFAULT_PROJECT_ID)

    async def test_the_record_carries_the_project_it_was_made_in(self):
        await self.save("mine", project=DEFAULT_PROJECT_ID)
        stored = json.loads((support.DATA / "long" / "mine" / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["project"], DEFAULT_PROJECT_ID)

    async def test_a_project_gets_its_own_film_and_not_the_newest_one(self):
        await self.save("first")
        project = self.app.create_project("Второй")   # creating a project makes it the active one
        time.sleep(0.02)                              # the file times decide which film is the freshest
        await self.save("second")
        self.assertEqual((await self.film(project=project["id"]))["run"], "second")
        self.assertEqual((await self.film(project=DEFAULT_PROJECT_ID))["run"], "first")
        self.assertEqual(await self.film(project="nobody"), {})

    async def test_the_freshest_film_of_one_project_wins(self):
        await self.save("old")
        time.sleep(0.02)
        await self.save("new")
        self.assertEqual((await self.film(project=DEFAULT_PROJECT_ID))["run"], "new")

    async def test_last_still_answers_for_the_films_of_the_old_version(self):
        await self.save("only")
        self.assertEqual((await self.film(last="1"))["run"], "only")
        self.assertEqual((await self.film()), {})

    async def test_an_unknown_project_does_not_write_a_dangling_film(self):
        saved = await self.save("stray", project_id="gone")
        self.assertEqual(saved["project"], DEFAULT_PROJECT_ID)
        self.assertEqual((await self.film(run="stray"))["project"]["project"], DEFAULT_PROJECT_ID)

    async def test_a_bad_record_is_refused(self):
        with self.assertRaises(web.HTTPBadRequest):
            await self.app.h_long_project(Post({"run": "no-project"}))
        with self.assertRaises(web.HTTPBadRequest):
            await self.app.h_long_project(Post({"project": {"scenes": []}}))


if __name__ == "__main__":
    unittest.main()
