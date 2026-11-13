"""Deleting a draft from «Прошлые клипы»: the record, the media row and the files.

A draft is not a Library item: deleting it must free the disk and leave the Library alone, and a
file that could not be recycled must leave the draft visible instead of pretending it is gone.
"""

import unittest
from pathlib import Path
from unittest import mock

from aiohttp import web

import support
import routes.library
from domain.media import MediaKind, MediaRecord, MediaState


class DraftDeleteTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = support.fresh_app()
        self.events = support.capture_broadcast(self.app)

    def draft(self, item_id="d1"):
        folder = support.new_dir("draft")
        path = folder / "clip.mp4"
        path.write_bytes(b"clip")
        item = {"id": item_id, "mode": "video", "kind": "video", "preview": True,
                "file": {"type": "local", "path": str(path), "filename": path.name},
                "local": str(path)}
        self.app.hold_preview(item)
        self.app.media_repo.save(MediaRecord(media_id=item_id, kind=MediaKind.VIDEO,
                                             state=MediaState.DRAFT, path=str(path)))
        return item, path

    @staticmethod
    def recycle(paths):
        """What the Recycle Bin helper does for the test: the files are gone and it says so."""
        for path in paths:
            Path(path).unlink(missing_ok=True)
        return list(paths)

    async def test_a_deleted_draft_leaves_no_record_and_no_file(self):
        item, path = self.draft()
        with mock.patch.object(routes.library, "send_to_recycle_bin", self.recycle):
            reply = await self.app.h_preview_delete(support.Req(match_info={"id": item["id"]}))
        self.assertEqual(reply.status, 200)
        self.assertNotIn(item["id"], self.app.pending)
        self.assertEqual(self.app.drafts_repo.load(), [])
        self.assertIsNone(self.app.media_repo.get(item["id"]))
        self.assertFalse(path.exists())
        self.assertEqual(self.events[-1], {"type": "draft_removed", "id": item["id"]})
        self.app.db.close()
        self.assertEqual(support.new_app().pending, {}, "удалённый черновик не вернулся после перезапуска")

    async def test_a_draft_that_reached_the_library_is_deleted_there(self):
        item, _ = self.draft()
        await self.app.h_preview_keep(support.Req({"id": item["id"]}))
        with self.assertRaises(web.HTTPConflict):
            await self.app.h_preview_delete(support.Req(match_info={"id": item["id"]}))
        self.assertIsNotNone(self.app.library.get(item["id"]))

    async def test_an_unknown_draft_is_not_found(self):
        with self.assertRaises(web.HTTPNotFound):
            await self.app.h_preview_delete(support.Req(match_info={"id": "nope"}))

    async def test_a_file_that_could_not_be_recycled_keeps_the_draft(self):
        item, path = self.draft()
        with mock.patch.object(routes.library, "send_to_recycle_bin", lambda paths: []):
            with self.assertRaises(web.HTTPInternalServerError):
                await self.app.h_preview_delete(support.Req(match_info={"id": item["id"]}))
        self.assertIn(item["id"], self.app.pending)
        self.assertTrue(path.exists())
