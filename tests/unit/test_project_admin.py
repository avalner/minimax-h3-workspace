"""Renaming and deleting a project: the two things a user does with a finished work.

A project is a label, not a folder: renaming must touch nothing but the name, and keeping a part of a
deleted project must only change that label. What is not kept goes for good — files to the Recycle
Bin, stages and the studio's films from the disk — and the dialog's list (`project_assets`) is what
the window shows before any of it happens.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from aiohttp import web

import support
import routes.library
from domain.projects import DEFAULT_PROJECT_ID, DEFAULT_PROJECT_NAME
from webcommon import LONG_DIR


class AdminCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = support.fresh_app()
        self.events = support.capture_broadcast(self.app)
        self.other = self.app.create_project("Мой фильм")

    # ---------- what a project holds ----------
    def library_item(self, name, project=None):
        folder = support.new_dir("admin-lib")
        path = folder / f"{name}.mp4"
        path.write_bytes(b"clip")
        item = {"id": f"lib-{name}", "mode": "video", "kind": "video",
                "date": "2026-11-13T10:00:00", "prompt": f"клип {name}",
                "file": {"type": "local", "path": str(path), "filename": path.name},
                "project": project or self.other["id"]}
        self.app.library.add(item)
        return item, path

    def draft(self, name, project=None):
        folder = support.new_dir("admin-draft")
        path = folder / f"{name}.mp4"
        path.write_bytes(b"clip")
        item = {"id": f"draft-{name}", "mode": "video", "kind": "video", "preview": True,
                "project": project or self.other["id"],
                "file": {"type": "local", "path": str(path), "filename": path.name},
                "local": str(path)}
        self.app.hold_preview(item)
        return item, path

    async def stage(self, name, project=None, mode="video"):
        reply = await self.app.h_stage_save(support.Req({
            "mode": mode, "name": name, "s": {"q": "720p"}, "project": project or self.other["id"],
            "files": ["video/first"], "names": {"video/first": "frame.png"}}))
        stage = json.loads(reply.text)["stage"]
        path = self.app.stages_repo.file_path(stage["id"], "video/first")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"frame")
        return stage, path

    def film(self, run, project=None, scenes=None, empty=False):
        folder = LONG_DIR / run
        folder.mkdir(parents=True, exist_ok=True)
        if isinstance(scenes, list):
            made = scenes
        else:
            made = [{"prompt": "" if empty else f"сцена {i}", "sec": 10} for i in range(scenes or 2)]
        record = {"run": run, "project": project or self.other["id"], "t": 1, "scenes": made}
        (folder / "project.json").write_text(json.dumps(record), encoding="utf-8")
        return record, folder

    @staticmethod
    def recycle(paths):
        """The Recycle Bin helper as the test wants it: the files are gone and it says so."""
        for path in paths:
            Path(path).unlink(missing_ok=True)
        return list(paths)


class RenameTest(AdminCase):
    def test_a_project_is_renamed_by_its_id_and_keeps_everything_else(self):
        library, path = self.library_item("a")
        renamed = self.app.rename_project(self.other["id"], "  Новое   имя  ")
        self.assertEqual(renamed["id"], self.other["id"])
        self.assertEqual(renamed["name"], "Новое имя")
        self.assertEqual([p["name"] for p in self.app.projects()], [DEFAULT_PROJECT_NAME, "Новое имя"])
        self.assertEqual(self.app.active_project(), self.other["id"], "переименование не меняет активный проект")
        self.assertEqual(self.app.library.get(library["id"])["project"], self.other["id"])
        self.assertTrue(path.exists(), "переименование не трогает файлы")
        self.app.db.close()
        self.assertEqual([p["name"] for p in support.new_app().projects()], [DEFAULT_PROJECT_NAME, "Новое имя"])

    def test_a_name_cannot_be_empty_and_the_project_must_exist(self):
        with self.assertRaises(ValueError):
            self.app.rename_project(self.other["id"], "   ")
        with self.assertRaises(KeyError):
            self.app.rename_project("gone", "Имя")

    async def test_the_window_renames_through_the_route(self):
        reply = await self.app.h_project_rename(
            support.Req({"name": "Из окна"}, match_info={"id": self.other["id"]}))
        body = json.loads(reply.text)
        self.assertEqual(body["project"]["name"], "Из окна")
        self.assertEqual(body["active"], self.other["id"])
        with self.assertRaises(web.HTTPBadRequest):
            await self.app.h_project_rename(support.Req({"name": " "}, match_info={"id": self.other["id"]}))
        with self.assertRaises(web.HTTPNotFound):
            await self.app.h_project_rename(support.Req({"name": "x"}, match_info={"id": "gone"}))


class AssetsTest(AdminCase):
    async def test_the_dialog_sees_everything_the_project_holds(self):
        self.library_item("a")
        self.library_item("b")
        self.draft("c")
        await self.stage("этап")
        self.film("run-one")
        assets = self.app.project_assets(self.other["id"])
        self.assertEqual(sorted(x["id"] for x in assets["library"]), ["lib-a", "lib-b"])
        self.assertEqual([x["id"] for x in assets["drafts"]], ["draft-c"])
        self.assertEqual([x["mode"] for x in assets["stages"]], ["video"])
        self.assertEqual([x["run"] for x in assets["films"]], ["run-one"])
        self.assertEqual(assets["films"][0]["scenes"], 2)

    async def test_another_project_shows_only_its_own_work(self):
        self.library_item("mine")
        self.library_item("theirs", project=DEFAULT_PROJECT_ID)
        assets = self.app.project_assets(DEFAULT_PROJECT_ID)
        self.assertEqual([x["id"] for x in assets["library"]], ["lib-theirs"])
        with self.assertRaises(KeyError):
            self.app.project_assets("gone")

    async def test_the_route_answers_and_refuses_an_unknown_project(self):
        self.library_item("a")
        reply = await self.app.h_project_assets(support.Req(match_info={"id": self.other["id"]}))
        self.assertEqual([x["id"] for x in json.loads(reply.text)["library"]], ["lib-a"])
        with self.assertRaises(web.HTTPNotFound):
            await self.app.h_project_assets(support.Req(match_info={"id": "gone"}))

    async def test_a_film_with_nothing_in_it_is_not_offered_to_keep(self):
        """Opening the studio puts two empty scenes on the screen; that is not a film of the project."""
        self.film("opened", empty=True)
        self.film("real")
        listed = sorted(x["run"] for x in self.app.project_assets(self.other["id"])["films"])
        self.assertEqual(listed, ["real"])

    async def test_a_film_counts_as_a_work_when_a_scene_was_made(self):
        self.film("made", scenes=[{"prompt": "", "sec": 10, "done": {"id": "m1", "url": "/x"}}])
        self.film("typed", scenes=[{"prompt": "  ", "sec": 10}, {"prompt": "герой идёт", "sec": 10}])
        listed = sorted(x["run"] for x in self.app.project_assets(self.other["id"])["films"])
        self.assertEqual(listed, ["made", "typed"])


class DeleteTest(AdminCase):
    async def test_what_the_user_kept_moves_to_the_default_project(self):
        library, path = self.library_item("keep")
        draft, draft_path = self.draft("keep")
        stage, stage_path = await self.stage("keep")
        film, film_folder = self.film("keep", scenes=3)
        reply = await self.app.h_project_delete(support.Req(
            {"keep": {"library": [library["id"]], "drafts": [draft["id"]],
                      "stages": [stage["id"]], "films": [film["run"]]}},
            match_info={"id": self.other["id"]}))
        body = json.loads(reply.text)
        self.assertEqual(body["moved"]["library"], [library["id"]])
        self.assertEqual(body["moved"]["drafts"], [draft["id"]])
        self.assertEqual(body["moved"]["stages"], [stage["id"]])
        self.assertEqual(body["moved"]["films"], [film["run"]])
        self.assertEqual(body["deleted"], {"library": [], "drafts": [], "stages": [], "films": []})
        self.assertEqual(body["active"], DEFAULT_PROJECT_ID)
        self.assertEqual([p["id"] for p in body["projects"]], [DEFAULT_PROJECT_ID])
        # nothing was copied or lost: the same records with a new label
        self.assertEqual(self.app.library.get(library["id"])["project"], DEFAULT_PROJECT_ID)
        self.assertEqual(self.app.pending[draft["id"]]["project"], DEFAULT_PROJECT_ID)
        self.assertEqual(self.app.stages_repo.get(stage["id"])["project"], DEFAULT_PROJECT_ID)
        stored = json.loads((film_folder / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["project"], DEFAULT_PROJECT_ID)
        for kept in (path, draft_path, stage_path):
            self.assertTrue(kept.exists(), "перенос — это новая метка, а не перемещение файла")
        self.assertEqual(self.app.active_project(), DEFAULT_PROJECT_ID)
        with self.assertRaises(KeyError):
            self.app.rename_project(self.other["id"], "нельзя")

    async def test_everything_else_is_deleted_with_its_files(self):
        library, path = self.library_item("drop")
        draft, draft_path = self.draft("drop")
        stage, stage_path = await self.stage("drop")
        _, film_folder = self.film("drop")
        with mock.patch.object(routes.library, "send_to_recycle_bin", self.recycle):
            reply = await self.app.h_project_delete(support.Req(match_info={"id": self.other["id"]}))
        body = json.loads(reply.text)
        self.assertEqual(body["deleted"]["library"], [library["id"]])
        self.assertEqual(body["deleted"]["drafts"], [draft["id"]])
        self.assertEqual(body["deleted"]["stages"], [stage["id"]])
        self.assertEqual(body["deleted"]["films"], ["drop"])
        self.assertIsNone(self.app.library.get(library["id"]))
        self.assertNotIn(draft["id"], self.app.pending)
        self.assertIsNone(self.app.stages_repo.get(stage["id"]))
        for gone in (path, draft_path, stage_path):
            self.assertFalse(gone.exists())
        self.assertFalse(film_folder.exists())
        self.assertEqual([e for e in self.events if e.get("type") == "draft_removed"],
                         [{"type": "draft_removed", "id": draft["id"]}])
        self.app.db.close()
        restarted = support.new_app()
        self.assertEqual([p["id"] for p in restarted.projects()], [DEFAULT_PROJECT_ID])

    async def test_a_file_that_would_not_be_recycled_keeps_its_record_in_the_default_project(self):
        library, path = self.library_item("stuck")
        draft, draft_path = self.draft("stuck")
        with mock.patch.object(routes.library, "send_to_recycle_bin", lambda paths: []):
            reply = await self.app.h_project_delete(support.Req(match_info={"id": self.other["id"]}))
        body = json.loads(reply.text)
        self.assertEqual(body["deleted"]["library"], [])
        self.assertEqual(body["deleted"]["drafts"], [])
        self.assertEqual(sorted(body["kept_back"]["library"]), [library["id"]])
        self.assertEqual(body["kept_back"]["drafts"], [draft["id"]])
        self.assertEqual(self.app.library.get(library["id"])["project"], DEFAULT_PROJECT_ID)
        self.assertEqual(self.app.pending[draft["id"]]["project"], DEFAULT_PROJECT_ID)
        self.assertTrue(path.exists() and draft_path.exists(), "файл остался — запись тоже")

    async def test_the_default_project_cannot_be_deleted(self):
        with self.assertRaises(ValueError):
            await self.app.delete_project(DEFAULT_PROJECT_ID)
        with self.assertRaises(web.HTTPBadRequest):
            await self.app.h_project_delete(support.Req(match_info={"id": DEFAULT_PROJECT_ID}))
        self.assertEqual(len(self.app.projects()), 2)

    async def test_an_unknown_project_is_not_found(self):
        with self.assertRaises(KeyError):
            await self.app.delete_project("gone")
        with self.assertRaises(web.HTTPNotFound):
            await self.app.h_project_delete(support.Req(match_info={"id": "gone"}))

    async def test_another_projects_work_is_left_alone(self):
        mine, _ = self.library_item("mine", project=DEFAULT_PROJECT_ID)
        theirs, path = self.library_item("theirs")
        with mock.patch.object(routes.library, "send_to_recycle_bin", self.recycle):
            await self.app.h_project_delete(support.Req(match_info={"id": self.other["id"]}))
        self.assertEqual(self.app.library.get(mine["id"])["project"], DEFAULT_PROJECT_ID)
        self.assertIsNone(self.app.library.get(theirs["id"]))
        self.assertFalse(path.exists())

    async def test_a_delete_without_a_body_takes_everything(self):
        self.library_item("a")
        reply = await self.app.h_project_delete(support.Req({}, match_info={"id": self.other["id"]}))
        self.assertEqual(json.loads(reply.text)["deleted"]["library"], ["lib-a"])

    async def test_an_untouched_film_goes_with_the_project_without_being_offered(self):
        _, folder = self.film("opened", empty=True)
        reply = await self.app.h_project_delete(support.Req(match_info={"id": self.other["id"]}))
        self.assertEqual(json.loads(reply.text)["deleted"]["films"], ["opened"])
        self.assertFalse(folder.exists())


if __name__ == "__main__":
    unittest.main()
