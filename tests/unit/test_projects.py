"""Projects: the label every result carries, and migration 0003 that moves old records into it.

A project is not a sandbox: the checks here are about the label (who made this, in which work),
about the default that always exists, and about the old records landing in it instead of vanishing.
"""

import json
import unittest
from unittest import mock

import support
import storage.migrations as migrations
from agentapi import AgentError
from aiohttp import web
from domain.jobs import JobRecord, JobState
from domain.projects import DEFAULT_PROJECT_ID, DEFAULT_PROJECT_NAME
from storage.database import Database, SCHEMA_VERSION
from storage.migrations import migrate
from storage.repositories import LibraryRepository


class MigrationTest(unittest.TestCase):
    def open_db(self, path):
        database = Database(path)
        self.addCleanup(database.close)
        return database

    def test_a_fresh_database_gets_the_default_project(self):
        folder = support.new_dir("projects")
        db = self.open_db(folder / "h3studio.db")
        report = migrate(db, folder)
        self.assertIn("0003-projects", report.applied)
        self.assertEqual(db.schema_version(), SCHEMA_VERSION)
        rows = db.query("SELECT id, name FROM projects")
        self.assertEqual([(row["id"], row["name"]) for row in rows],
                         [(DEFAULT_PROJECT_ID, DEFAULT_PROJECT_NAME)])

    def test_existing_results_and_drafts_move_to_the_default_project(self):
        folder = support.new_dir("projects")
        db = self.open_db(folder / "h3studio.db")
        without_projects = tuple(step for step in migrations.STEPS if step[0] != "0003-projects")
        with mock.patch("storage.migrations.STEPS", without_projects):
            migrate(db, folder)   # the database as the previous version left it
        db.execute("INSERT INTO library(id, position, date, fav, kind, mode, prompt_id, payload) "
                   "VALUES('old','0','2026-11-01',0,'video','video','p1',?)",
                   (json.dumps({"id": "old", "mode": "video"}, ensure_ascii=False),))
        db.execute("INSERT INTO drafts(id, payload) VALUES('d1',?)",
                   (json.dumps({"id": "d1", "kind": "video"}, ensure_ascii=False),))
        report = migrate(db, folder)
        self.assertIn("0003-projects", report.applied)
        row = db.one("SELECT project, payload FROM library WHERE id='old'")
        self.assertEqual(row["project"], DEFAULT_PROJECT_ID)
        self.assertEqual(json.loads(row["payload"])["project"], DEFAULT_PROJECT_ID)
        self.assertEqual(json.loads(db.one("SELECT payload FROM drafts WHERE id='d1'")["payload"])["project"],
                         DEFAULT_PROJECT_ID)
        self.assertEqual([item["project"] for item in LibraryRepository(db).load()], [DEFAULT_PROJECT_ID])


class ProjectAppTest(unittest.TestCase):
    def setUp(self):
        self.app = support.fresh_app()

    def test_new_results_are_stamped_with_the_active_project(self):
        first = self.app.attach_job(support.job_record("p1"), meta={"mode": "video"})
        self.assertEqual(first["meta"]["project"], DEFAULT_PROJECT_ID)
        project = self.app.create_project("  Мой   фильм ")
        self.assertEqual(project["name"], "Мой фильм")
        self.assertEqual(self.app.active_project(), project["id"])
        second = self.app.attach_job(support.job_record("p2"), meta={"mode": "video"})
        self.assertEqual(second["meta"]["project"], project["id"])
        self.assertEqual(first["meta"]["project"], DEFAULT_PROJECT_ID,
                         "проект уже отправленного задания не меняется задним числом")

    def test_a_project_needs_a_name_and_a_real_id(self):
        with self.assertRaises(ValueError):
            self.app.create_project("   ")
        with self.assertRaises(KeyError):
            self.app.set_active_project("nope")

    def test_the_active_project_survives_a_restart(self):
        project = self.app.create_project("Второй")
        self.app.db.close()
        restarted = support.new_app()
        self.assertEqual(restarted.active_project(), project["id"])
        self.assertEqual([item["name"] for item in restarted.projects()],
                         [DEFAULT_PROJECT_NAME, "Второй"])

    def test_an_unknown_project_in_the_settings_falls_back_to_the_default(self):
        self.app.config.update(active_project="gone")
        self.assertEqual(self.app.active_project(), DEFAULT_PROJECT_ID)

    def test_a_draft_carries_the_project_too(self):
        self.app.hold_preview({"id": "draft-1", "mode": "video"})
        self.assertEqual(self.app.pending["draft-1"]["project"], DEFAULT_PROJECT_ID)
        project = self.app.create_project("Другой")
        self.app.hold_preview({"id": "draft-2", "mode": "video"})
        self.assertEqual(self.app.pending["draft-2"]["project"], project["id"])

    def test_the_durable_record_of_any_mode_carries_the_project(self):
        project = self.app.create_project("Монтаж")
        record = self.app.begin_submission(None, None, {}, {"mode": "montage", "seed": 0},
                                           backend="ffmpeg", backend_id="ffmpeg:local")
        self.assertEqual(record.legacy["meta"]["project"], project["id"])


class ProjectRoutesTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = support.fresh_app()

    async def test_the_window_can_list_create_and_select_projects(self):
        reply = json.loads((await self.app.h_projects(None)).text)
        self.assertEqual([project["id"] for project in reply["projects"]], [DEFAULT_PROJECT_ID])
        self.assertEqual(reply["active"], DEFAULT_PROJECT_ID)

        created = await self.app.h_project_create(support.Req({"name": "Новый"}))
        self.assertEqual(created.status, 201)
        body = json.loads(created.text)
        self.assertEqual(body["active"], body["project"]["id"])

        chosen = json.loads((await self.app.h_project_active(
            support.Req({"id": DEFAULT_PROJECT_ID}))).text)
        self.assertEqual(chosen["active"], DEFAULT_PROJECT_ID)

        with self.assertRaises(web.HTTPNotFound):
            await self.app.h_project_active(support.Req({"id": "gone"}))
        with self.assertRaises(web.HTTPBadRequest):
            await self.app.h_project_create(support.Req({"name": "   "}))

    async def test_the_agent_sees_the_same_projects(self):
        self.app.create_project("Агентский")
        body = json.loads((await self.app.agents.h_projects(None)).text)
        self.assertEqual([(project["name"], project["active"]) for project in body["projects"]],
                         [(DEFAULT_PROJECT_NAME, False), ("Агентский", True)])


class AgentProjectTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = support.fresh_app()
        self.app.config.data.update(modes=["video"], engine="external")
        self.app.need_models = mock.AsyncMock()
        self.app.prepare_engine = mock.AsyncMock()

    async def test_an_unknown_project_is_refused_before_any_work(self):
        with self.assertRaises(AgentError) as error:
            await self.app.agents._submit({"prompt": "тест", "project": "nope"})
        self.assertEqual(error.exception.code, "PROJECT_UNKNOWN")

    async def test_an_agent_may_name_the_project(self):
        project = self.app.create_project("Агентский")
        captured = []

        async def fake_submit(spec, **kwargs):
            captured.append(kwargs.get("meta"))
            return JobRecord(job_id="j", prompt_id="p9", spec=spec, state=JobState.QUEUED), False

        self.app.start_video_job = fake_submit
        reply = await self.app.agents._submit({"prompt": "тест", "project": project["id"]})
        self.assertEqual(captured[0]["project"], project["id"])
        self.assertEqual(json.loads(reply.text)["job"], "p9")
