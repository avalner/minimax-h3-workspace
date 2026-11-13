"""MediaService: one finalization, a `.part` copy, and a status for every stage of a result file.

The checks run without a server, without a fake ComfyUI and without a window: a real SQLite
database in a temporary folder, a Library double, and a source that hands out chunks (or fails).

    python tests/unit/test_media_service.py
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import support

from backends.base import OutputStream
from domain.jobs import JobRecord, JobSpec, JobState
from domain.media import MediaRecord, MediaState, media_id_for
from services.media import EmptyResult, IncompleteFile, MediaFiles, MediaService
from storage.database import Database
from storage.migrations import migrate
from storage.repositories import (JobRepository, LibraryRepository, MediaRepository, ResultRepository)

VIDEO = {"9": {"images": [{"filename": "clip.mp4", "subfolder": "", "type": "output",
                           "metadata": {"width": 1280, "height": 720}}]}}
CLIP = b"not really a video, but exactly the bytes the engine reported" * 3


class FakeLibrary:
    """The Library list as the service uses it: a repository for rows, a list for the window."""

    def __init__(self, db):
        self.repo = LibraryRepository(db)
        self.items = self.repo.load()

    def get(self, item_id):
        return next((x for x in self.items if x["id"] == item_id), None)

    def add(self, item):
        self.repo.add(item)
        self.items.insert(0, item)

    def save(self, item):
        self.repo.add(item)

    def note_committed(self, item):
        if self.get(item["id"]) is None:   # the row is already committed: only the list changes
            self.items.insert(0, item)


class FakeSource:
    """The engine's files as chunks: it can fail in the middle, declare a wrong size, or count opens."""

    def __init__(self, data=CLIP, *, expected=None, fail_after=None, pieces=3):
        self.data = data
        self.expected = expected      # None: declare the real length
        self.fail_after = fail_after  # raise instead of this chunk
        self.pieces = pieces
        self.opens = 0

    def declared_size(self):
        return len(self.data) if self.expected is None else self.expected

    @contextlib.asynccontextmanager
    async def open_output(self, file_ref):
        self.opens += 1
        data, fail_after = self.data, self.fail_after

        async def chunks():
            step = max(1, len(data) // self.pieces + 1)
            for number, start in enumerate(range(0, len(data), step)):
                if fail_after is not None and number >= fail_after:
                    raise ConnectionResetError("связь с движком оборвалась")
                yield data[start:start + step]

        yield OutputStream(chunks=chunks(), expected_bytes=self.declared_size())


class MediaCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = support.new_dir("media")
        self.db = Database(self.folder / "h3studio.db")
        self.addCleanup(self.db.close)
        migrate(self.db, self.folder)
        self.jobs = JobRepository(self.db)
        self.media_repo = MediaRepository(self.db)
        self.results = ResultRepository(self.db)
        self.library = FakeLibrary(self.db)
        self.engine = self.folder / "engine"
        self.engine.mkdir()
        self.copies = self.folder / "results"
        self.copies.mkdir()
        self.output_map = dict(VIDEO)
        self.source = FakeSource()
        self.drafts = []
        self.errors = []
        self.notified = []
        self.service = self.make_service()

    def make_service(self, **overrides):
        kwargs = dict(db=self.db, jobs=self.jobs, media=self.media_repo, results=self.results,
                      library=self.library, source=self.source, outputs_of=self.outputs,
                      output_dir=lambda: self.engine, results_dir=lambda: self.copies,
                      keep_copies=lambda: True,
                      present=lambda item: {**item, "url": "/api/file/" + item["id"]},
                      hold_draft=self.drafts.append, note_error=self.errors.append)
        kwargs.update(overrides)
        return MediaService(**kwargs)

    async def outputs(self, pid):
        return self.output_map

    async def notify(self, payload):
        self.notified.append(payload)

    def put_engine_file(self, name="clip.mp4", data=CLIP):
        (self.engine / name).write_bytes(data)

    def job(self, pid="p1", *, policy="library", last_output=False, record=True):
        """A job that has just finished on the engine, written down as `finalizing`."""
        spec = JobSpec.video(prompt="кадр", aspect="16:9", quality="720p", seconds=5, seed=1,
                             width=1280, height=720, result_policy=policy)
        rec = JobRecord(job_id="j-" + pid, prompt_id=pid, backend="comfy", backend_id="comfy:test",
                        backend_job_id=pid, state=JobState.FINALIZING, created_at=time.time(),
                        updated_at=time.time(), spec=spec) if record else None
        if rec is not None:
            self.jobs.save(rec)
        return {"prompt_id": pid, "state": "running", "stages": {}, "stage": "save",
                "meta": {"mode": "video", "prompt": "кадр", "seed": 1}, "last_output": last_output,
                "titles": {}, "graph": {"1": {"class_type": "Test", "inputs": {}}},
                "t0": rec.created_at if rec is not None else time.time(), "preview": False,
                "record": rec, "finalizing": True}

    async def finalize(self, job, **kwargs):
        kwargs.setdefault("notify", self.notify)
        return await self.service.finalize(job, **kwargs)

    def item(self):
        return self.library.items[0]

    def parts(self, folder=None):
        return sorted((folder or self.copies).glob(".*.part"))


class FinalizeTest(MediaCase):
    async def test_the_result_becomes_a_library_item_and_the_job_succeeds(self):
        self.put_engine_file()
        job = self.job()
        await self.finalize(job)
        self.assertEqual(len(self.library.items), 1)
        self.assertEqual(self.item()["file"]["filename"], "clip.mp4")
        self.assertEqual(self.item()["res"], "1280×720")
        self.assertIs(job["record"].state, JobState.SUCCEEDED)
        self.assertEqual(len(job["record"].results), 1)
        self.assertEqual([event["type"] for event in self.notified], ["done"])
        self.assertEqual(self.notified[0]["items"], [self.item() | {"url": "/api/file/" + self.item()["id"]}])

    async def test_the_independent_copy_is_made_and_recorded(self):
        self.put_engine_file()
        await self.finalize(self.job())
        item = self.item()
        self.assertIn("local", item)
        self.assertEqual(Path(item["local"]).read_bytes(), CLIP)
        media = self.media_repo.get(item["id"])
        self.assertIs(media.state, MediaState.ARCHIVED)
        self.assertEqual(media.path, item["local"])
        self.assertEqual(media.bytes, len(CLIP))
        self.assertEqual(self.db.one("SELECT job_id FROM media WHERE media_id=?", (item["id"],))["job_id"], "j-p1")

    async def test_the_copy_is_written_beside_its_destination_and_renamed(self):
        self.put_engine_file()
        await self.finalize(self.job())
        files = [p.name for p in self.copies.iterdir()]
        self.assertEqual(len(files), 1, f"в папке результатов не один файл: {files}")
        self.assertTrue(files[0].endswith("_clip.mp4"), "имя копии не узнаваемо")
        self.assertNotIn(".part", files[0], "в библиотеку попал недописанный файл")

    async def test_a_new_service_with_the_same_state_finds_the_same_item(self):
        job = self.job()
        self.service = self.make_service(output_dir=lambda: None)   # the file has to be downloaded
        await self.finalize(job)
        self.assertEqual(self.source.opens, 1)
        second = self.make_service(output_dir=lambda: None)   # as if the program had been started again
        await second.finalize(job, notify=self.notify)
        self.assertEqual(len(self.library.items), 1, "повторная финализация создала второй item")
        self.assertEqual(self.source.opens, 1, "копию скачали заново, хотя она уже есть")

    async def test_an_empty_answer_is_not_a_media_failure(self):
        self.output_map = {}
        job = self.job()
        with self.assertRaises(EmptyResult):
            await self.finalize(job)
        self.assertEqual(self.library.items, [])
        self.assertIs(job["record"].state, JobState.FINALIZING, "задание не должно молча стать ничем")

    async def test_a_temporary_preview_is_not_a_result(self):
        self.output_map = {"3": {"images": [{"filename": "preview.png", "subfolder": "", "type": "temp"}]}}
        with self.assertRaises(EmptyResult):
            await self.finalize(self.job())

    async def test_the_last_clip_is_taken_when_the_job_asked_for_it(self):
        self.output_map = {"9": {"images": [{"filename": "first.mp4", "subfolder": "", "type": "output"},
                                            {"filename": "last.mp4", "subfolder": "", "type": "output"}]}}
        await self.finalize(self.job(last_output=True))
        self.assertEqual(self.item()["file"]["filename"], "last.mp4")

    async def test_a_draft_waits_and_does_not_enter_the_library(self):
        self.put_engine_file()
        job = self.job(policy="draft")
        await self.finalize(job)
        self.assertEqual(self.library.items, [], "черновик попал в Библиотеку сам")
        self.assertEqual(len(self.drafts), 1)
        self.assertIs(job["record"].results[0].state, MediaState.DRAFT)
        self.assertIs(job["record"].state, JobState.SUCCEEDED)
        self.assertTrue(Path(self.drafts[0]["local"]).exists(), "файл черновика должен переживать очистку output")
        self.assertEqual(self.library.repo.load(), [], "черновик записан как библиотечный результат")

    async def test_a_job_without_a_record_still_gets_its_item(self):
        self.put_engine_file()
        await self.finalize(self.job(record=False))
        self.assertEqual(len(self.library.items), 1)
        media = self.media_repo.get(self.item()["id"])
        self.assertIsNotNone(media, "у файла нет записи в media")
        self.assertIs(media.state, MediaState.ARCHIVED)

    async def test_the_copy_can_be_turned_off_without_losing_the_item(self):
        self.put_engine_file()
        service = self.make_service(keep_copies=lambda: False)
        await service.finalize(self.job(), notify=self.notify)
        self.assertEqual(len(self.library.items), 1)
        self.assertNotIn("local", self.item())
        self.assertIs(self.media_repo.get(self.item()["id"]).state, MediaState.LIBRARY)


class CopyFailureTest(MediaCase):
    def offline(self, source=None):
        """The engine's output folder is unknown: the file has to be downloaded."""
        self.source = source or FakeSource()
        return self.make_service(output_dir=lambda: None)

    async def test_a_broken_download_leaves_no_finished_file(self):
        self.service = self.offline(FakeSource(fail_after=1))
        job = self.job()
        result = await self.finalize(job)
        self.assertEqual(len(result.items), 1, "из-за неудачной копии пропал сам результат")
        self.assertIs(job["record"].state, JobState.SUCCEEDED)
        self.assertEqual(list(self.copies.iterdir()), [], "недописанный файл остался под настоящим именем")
        self.assertNotIn("local", self.item())
        media = self.media_repo.get(self.item()["id"])
        self.assertIs(media.state, MediaState.ARCHIVE_FAILED)
        self.assertIn("ConnectionResetError", media.error or "")
        self.assertTrue(any("копию результата" in text for text in self.errors))

    async def test_a_short_transfer_is_not_published(self):
        self.service = self.offline(FakeSource(data=b"0123456789", expected=100))
        await self.finalize(self.job())
        self.assertEqual(list(self.copies.iterdir()), [])
        self.assertIs(self.media_repo.get(self.item()["id"]).state, MediaState.ARCHIVE_FAILED)

    async def test_a_broken_download_does_not_touch_an_existing_copy(self):
        item = {"id": "x1", "file": {"filename": "clip.mp4", "subfolder": "", "type": "output"},
                "date": "2026-11-12T10:00:00"}
        dest = self.service._archive_path(item, self.copies)
        dest.write_bytes(b"the previous, complete copy")
        self.service = self.offline(FakeSource(fail_after=0))
        copy = await self.service.archive(item)
        self.assertEqual(dest.read_bytes(), b"the previous, complete copy")
        self.assertIs(copy.state, MediaState.ARCHIVED, "уже сделанная копия должна быть принята")
        self.assertEqual(self.source.opens, 0, "скачали файл, который уже лежал на месте")

    async def test_the_copy_can_be_repeated_without_a_new_generation(self):
        self.service = self.offline(FakeSource(fail_after=1))
        await self.finalize(self.job())
        self.assertEqual(self.media_repo.get(self.item()["id"]).state, MediaState.ARCHIVE_FAILED)
        self.service = self.offline()            # the engine answers again
        self.assertTrue(await self.service.archive_item(self.item()))
        self.assertEqual(Path(self.item()["local"]).read_bytes(), CLIP)
        self.assertIs(self.media_repo.get(self.item()["id"]).state, MediaState.ARCHIVED)
        self.assertEqual(len(self.library.items), 1, "повтор копии создал второй item")

    async def test_a_copy_made_before_a_failed_commit_is_reused(self):
        self.service = self.offline()
        job = self.job()
        with mock.patch.object(self.jobs, "save", side_effect=sqlite3.OperationalError("disk I/O error")):
            with self.assertRaises(sqlite3.OperationalError):
                await self.finalize(job)
        self.assertEqual(self.library.items, [], "row записана вопреки сбою")
        made = list(self.copies.iterdir())
        self.assertEqual(len(made), 1, "копия не была сделана до транзакции")
        self.assertIs(job["record"].state, JobState.FINALIZING, "состояние в памяти не откатилось")
        self.assertEqual(job["record"].results, [])

        retry = self.offline()   # the next attempt, e.g. after a restart
        await retry.finalize(job, notify=self.notify)
        self.assertEqual(len(self.library.items), 1)
        self.assertEqual(retry.source.opens, 0, "файл скачали второй раз")
        self.assertEqual(sorted(self.copies.iterdir()), made)

    async def test_a_failed_commit_leaves_no_half_written_rows(self):
        self.put_engine_file()
        job = self.job()
        with mock.patch.object(self.library.repo, "add", side_effect=sqlite3.OperationalError("disk I/O error")):
            with self.assertRaises(sqlite3.OperationalError):
                await self.finalize(job)
        self.assertEqual(self.library.repo.load(), [])
        self.assertIsNone(self.media_repo.get(media_id_for("j-p1", "9", VIDEO["9"]["images"][0])))
        self.assertEqual(self.jobs.results.by_job("j-p1"), [])
        self.assertEqual(self.jobs.get("j-p1").state, JobState.FINALIZING)
        await self.finalize(job)   # the same job can still be registered
        self.assertEqual(len(self.library.items), 1)


class WriterTest(MediaCase):
    async def test_a_copy_goes_through_a_part_and_publishes_the_whole_file(self):
        src = self.folder / "source.bin"
        src.write_bytes(CLIP)
        dest = self.copies / "copy.bin"
        seen = []
        files = MediaFiles(chunk=4)
        written = await files.copy_file(src, dest, token=self.service.token(),
                                        on_chunk=lambda n: seen.append((dest.exists(), len(files.active_parts()))))
        self.assertEqual(written, len(CLIP))
        self.assertEqual(dest.read_bytes(), CLIP)
        self.assertTrue(all(not made for made, _ in seen))
        self.assertTrue(all(active == 1 for _, active in seen), "запись не была зарегистрирована как активная")
        self.assertEqual(self.parts(), [])

    async def test_a_cancelled_copy_waits_for_its_writer_and_removes_the_part(self):
        src = self.folder / "source.bin"
        src.write_bytes(CLIP)
        dest = self.copies / "copy.bin"
        started, release = threading.Event(), threading.Event()

        def slow(_written):
            started.set()
            release.wait(5)

        files = MediaFiles(chunk=4)
        token = self.service.token()
        task = asyncio.create_task(files.copy_file(src, dest, token=token, on_chunk=slow))
        await asyncio.to_thread(started.wait, 5)
        self.assertEqual(len(files.active_parts()), 1)
        task.cancel()
        await asyncio.sleep(0.05)
        self.assertEqual(len(files.active_parts()), 1, "запись брошена, а поток всё ещё пишет файл")
        self.assertFalse(dest.exists())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.parts(), [], "поток не убрал свой .part")
        self.assertFalse(dest.exists())
        await self.service.shutdown()

    async def test_a_short_local_file_is_not_published(self):
        src = self.folder / "source.bin"
        src.write_bytes(CLIP)
        files = MediaFiles()
        with self.assertRaises(IncompleteFile):
            await files.copy_file(src, self.copies / "copy.bin", expected_bytes=len(CLIP) + 10)
        self.assertEqual(list(self.copies.iterdir()), [])


class CleanupTest(MediaCase):
    async def test_only_our_own_part_files_are_removed(self):
        (self.copies / ".clip.mp4.1a2b.deadbeef.part").write_bytes(b"half")
        (self.copies / "notes.txt").write_text("не наш файл", encoding="utf-8")
        (self.copies / "clip.mp4.part").write_bytes(b"a name we do not use")
        removed = self.service.cleanup()
        self.assertEqual([Path(p).name for p in removed], [".clip.mp4.1a2b.deadbeef.part"])
        self.assertTrue((self.copies / "notes.txt").exists())
        self.assertTrue((self.copies / "clip.mp4.part").exists(), "удалён файл, созданный не нами")

    async def test_a_part_of_an_active_writer_is_kept(self):
        self.put_engine_file()
        started, release = threading.Event(), threading.Event()

        def slow(_written):
            started.set()
            release.wait(5)

        task = asyncio.create_task(self.service.files.copy_file(
            self.engine / "clip.mp4", self.copies / "copy.mp4", token=self.service.token(), on_chunk=slow))
        await asyncio.to_thread(started.wait, 5)
        active = next(iter(self.service.files.active_parts()))
        self.assertEqual(self.service.cleanup(), [], "уборка удалила файл активной записи")
        self.assertTrue(Path(active).exists())
        release.set()
        await task
        self.assertEqual(self.service.cleanup(), [], "успешная запись не оставила .part")

    async def test_a_part_a_record_points_at_is_kept(self):
        part = self.copies / ".clip.mp4.1a2b.cafe.part"
        part.write_bytes(b"half")
        self.media_repo.save(MediaRecord(media_id="m1", path=str(part), state=MediaState.LOCAL,
                                         created_at=time.time()))
        self.assertEqual(self.service.cleanup(), [])
        self.assertTrue(part.exists())

    async def test_the_cleanup_only_looks_at_its_own_folders(self):
        other = self.folder / "elsewhere"
        other.mkdir()
        stray = other / ".x.1.abc.part"
        stray.write_bytes(b"half")
        self.assertEqual(self.service.cleanup(), [])
        self.assertTrue(stray.exists())


class IdentityTest(unittest.TestCase):
    def test_the_same_engine_output_always_gets_the_same_id(self):
        ref = VIDEO["9"]["images"][0]
        self.assertEqual(media_id_for("j1", "9", ref), media_id_for("j1", "9", dict(ref)))
        self.assertNotEqual(media_id_for("j1", "9", ref), media_id_for("j1", "10", ref))
        self.assertNotEqual(media_id_for("j1", "9", ref), media_id_for("j2", "9", ref))
        self.assertEqual(len(media_id_for("j1", "9", ref)), 12)


if __name__ == "__main__":
    unittest.main()
