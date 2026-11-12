"""One way for a finished generation to become a file the user owns.

The entities are deliberately kept apart, because they fail separately:

    engine output   the file in the engine's output folder (ComfyUI may clean it up)
    our copy        the program's own file in the results folder, written through a `.part`
    draft           a finished picture that waits for «Добавить в библиотеку»
    Library record  the row the window shows
    archive copy    *another* copy, and the one that may be missing while everything else is fine

A successful generation is not a saved result, and a failed copy is not a failed generation: the
`archive_failed` state says exactly that, and a repeated finalization copies again instead of asking
the engine for a second render.

The finalization runs in a fixed order, and that order is the recovery rule:

1. find the engine's outputs and check that there is something to keep;
2. build the Library item and its media id from the engine's identity of the file — the same id on
   every attempt, so a retry is an update, not a second item;
3. prepare the copy next to its destination under a unique `.part` name, check the size and rename.
   Until the rename the real name is free: a broken transfer never leaves a finished-looking mp4;
4. write the media rows, the `job_results` rows and the `succeeded` state in **one** transaction.
   The file system and SQLite are not one transaction, and pretending otherwise would be a lie;

so a crash can leave a complete copy without a Library row (the next attempt finds the copy by its
stable name and only writes the rows), never a Library row without a complete file.

`MediaFiles` is where the bytes move. A copy of a big local file runs in a worker thread and checks a
`CancelToken` between chunks: cancelling an `asyncio.to_thread` does not stop the thread, so the
caller waits for the writer to remove its own `.part` before it reports anything.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from domain.jobs import ResultPolicy
from domain.media import MediaKind, MediaRecord, MediaState, media_id_for

CHUNK = 1 << 20   # 1 MiB: big enough for throughput, small enough never to hold a clip in memory
VIDEO_SUFFIXES = (".mp4", ".webm", ".mkv", ".mov")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


class MediaError(Exception):
    """A file could not be prepared. The generation itself is not in question."""


class MediaUnavailable(MediaError):
    """The engine did not give the file (no answer, an HTTP error). Trying again later may work."""


class IncompleteFile(MediaError):
    """The transfer ended earlier than its declared size: nothing is published."""


class WriterCancelled(MediaError):
    """The writer was asked to stop: the `.part` is removed and nothing is published."""


class EmptyResult(MediaError):
    """The engine finished, said nothing about files, and there is nothing to register."""


# ---------- the stop flag of a writer ----------


class CancelToken:
    """A cooperative stop for a worker thread.

    `asyncio.to_thread` cannot be interrupted: cancelling the task leaves the thread copying the file
    and then removing the file under the writer's feet. So the copy checks this flag between chunks,
    and the awaiting side waits for the thread to finish before it reports the stop.
    """

    def __init__(self):
        self._flag = threading.Event()

    def cancel(self):
        self._flag.set()

    @property
    def cancelled(self) -> bool:
        return self._flag.is_set()

    def check(self):
        if self._flag.is_set():
            raise WriterCancelled("запись файла остановлена")


# ---------- the bytes ----------


def part_path(dest) -> Path:
    """A unique name in the destination folder: the same volume, so the rename is atomic.

    The random part is what makes two attempts (or two jobs writing the same destination) never use
    one temporary file, and what lets the cleanup tell our `.part` files from anything else.
    """
    dest = Path(dest)
    return dest.with_name(f".{dest.name}.{os.getpid():x}.{secrets.token_hex(4)}.part")


def _remove(path):
    try:
        Path(path).unlink()
    except OSError:
        pass   # an open handle of a cancelled writer: the cleanup of the next start removes it


def _replace(temp, dest, retries=5, delay=0.02):
    """Publish the complete file. Windows keeps a target locked for a moment (indexer, scanner)."""
    for attempt in range(retries):
        try:
            os.replace(temp, dest)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(delay * (attempt + 1))


def _fsync(path):
    """Flush a finished `.part` before it becomes the real file. Runs in a worker thread."""
    fd = os.open(path, os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class MediaFiles:
    """Moves bytes into the program's own copy: a unique `.part`, a size check, an atomic rename.

    A file is published only when it is complete. A failed or interrupted transfer leaves a `.part`
    at worst (removed at once, or by the cleanup of the next start); the real name is either the
    previous file or the complete new one.
    """

    def __init__(self, *, chunk=CHUNK):
        self.chunk = chunk
        self._writers = {}   # part path -> CancelToken of the writer that owns it
        self._lock = threading.Lock()

    # ---------- who is writing what ----------

    def active_parts(self) -> set:
        with self._lock:
            return set(self._writers)

    def _begin(self, part, token):
        with self._lock:
            self._writers[part] = token

    def _end(self, part):
        with self._lock:
            self._writers.pop(part, None)

    async def wait_idle(self, timeout=10.0) -> bool:
        """Wait until no writer owns a `.part` any more (the shutdown asks before it deletes nothing)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not self.active_parts():
                return True
            await asyncio.sleep(0.02)
        return not self.active_parts()

    # ---------- writing ----------

    @staticmethod
    def _check_size(expected, written, dest):
        if expected is not None and int(expected) != int(written):
            raise IncompleteFile(f"{Path(dest).name}: получено {written} байт из {expected}")

    async def save_stream(self, dest, chunks, *, expected_bytes=None, token=None, on_chunk=None) -> int:
        """Write an async stream of chunks to `dest` through a `.part`; returns the bytes written.

        Nothing is ever read into memory as a whole: the stream is consumed chunk by chunk, so a long
        clip costs the same as a short one.
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = part_path(dest)
        self._begin(part, token)
        written = 0
        try:
            with open(part, "wb") as out:
                async for chunk in chunks:
                    if token is not None:
                        token.check()
                    out.write(chunk)
                    written += len(chunk)
                    if on_chunk is not None:
                        on_chunk(written)
                out.flush()
            await asyncio.to_thread(_fsync, part)
            self._check_size(expected_bytes, written, dest)
            await asyncio.to_thread(_replace, part, dest)
            return written
        except BaseException:
            _remove(part)
            raise
        finally:
            self._end(part)

    async def copy_file(self, src, dest, *, expected_bytes=None, token=None, on_chunk=None) -> int:
        """Copy a big local file in a worker thread, through a `.part`, and publish it when complete.

        A cancelled caller does not lose the writer: the token is set, the thread stops at the next
        chunk and removes its own `.part`, and only then is the cancellation raised upwards.
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = part_path(dest)
        self._begin(part, token)
        worker = asyncio.ensure_future(asyncio.to_thread(
            self._copy_sync, Path(src), dest, part, expected_bytes, token, on_chunk))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            if token is not None:
                token.cancel()
            with contextlib.suppress(BaseException):
                await worker   # the thread returns by itself; it removes the `.part` it opened
            raise
        finally:
            self._end(part)

    def _copy_sync(self, src, dest, part, expected_bytes, token, on_chunk):
        written = 0
        try:
            with open(src, "rb") as fin, open(part, "wb") as out:
                while True:
                    if token is not None:
                        token.check()
                    chunk = fin.read(self.chunk)
                    if not chunk:
                        break
                    out.write(chunk)
                    written += len(chunk)
                    if on_chunk is not None:
                        on_chunk(written)
                out.flush()
                os.fsync(out.fileno())
            self._check_size(expected_bytes if expected_bytes is not None else src.stat().st_size,
                             written, dest)
            _replace(part, dest)
            return written
        except BaseException:
            _remove(part)
            raise


# ---------- the service ----------


@dataclass
class Copy:
    """What happened to the independent copy. A status, not an exception: generation is not in doubt."""

    state: MediaState = MediaState.LIBRARY
    path: str | None = None
    bytes: int | None = None
    error: str | None = None


@dataclass
class Finalized:
    """What the finalization published: the items the window may show, and the drafts left aside."""

    items: list
    draft: bool = False


def output_refs(outputs) -> list:
    """(node, file) pairs of an engine's outputs, in the order the engine reported them."""
    pairs = []
    for node, out in (outputs or {}).items():
        if not isinstance(out, dict):
            continue
        for values in out.values():
            if not isinstance(values, list):
                continue
            for ref in values:
                if isinstance(ref, dict) and ref.get("filename"):
                    pairs.append((str(node), dict(ref)))
    return pairs


def pick_results(job, refs) -> list:
    """Which engine outputs are the result of this job, in the order the user should see them.

    A video job has one clip (the first, or the last when the job asked for the last scene); a
    picture workflow keeps up to twelve saved pictures, the glued sheet first. A file of a loaded
    clip (type "temp") is never a result.
    """
    clips = [(node, ref) for node, ref in refs if str(ref["filename"]).lower().endswith(VIDEO_SUFFIXES)]
    saved = [(node, ref) for node, ref in clips if ref.get("type", "output") == "output"]
    clips = saved or clips
    if clips:
        return [clips[-1] if job.get("last_output") else clips[0]]
    images = [(node, ref) for node, ref in refs
              if str(ref["filename"]).lower().endswith(IMAGE_SUFFIXES) and ref.get("type", "output") == "output"]
    images = images[:12]

    def order(pair):   # the glued sheet first, then the frames 1, 2, 3...
        title = str((job.get("titles") or {}).get(pair[0], ""))
        return (0 if title.startswith("Лист") else 1, title)

    images.sort(key=order)
    return images


def is_draft(job, record) -> bool:
    """The result policy of the request decides; the older `preview` flag is the fallback."""
    policy = record.spec.result_policy if record is not None and record.spec is not None else None
    if policy is not None:
        return policy is ResultPolicy.DRAFT
    return bool(job.get("preview"))


class MediaService:
    """The results of jobs: what the engine produced, what we copied, and what the Library holds."""

    def __init__(self, *, db, jobs, media, results, library, source=None, files=None,
                 outputs_of=None, output_dir=lambda: None, results_dir=lambda: None,
                 keep_copies=lambda: True, present=None, hold_draft=None,
                 note_error=lambda text: None, on_outputs=None, extra_folders=lambda: (),
                 clock=time.time):
        self.db = db
        self.jobs = jobs
        self.media = media
        self.results = results
        self.library = library
        self.source = source          # something with `open_output(file_ref)`; None: no downloads
        self.files = files or MediaFiles()
        self.outputs_of = outputs_of  # async (prompt_id) -> the engine's outputs
        self.output_dir = output_dir  # the engine's output folder, when it is on this machine
        self.results_dir = results_dir
        self.keep_copies = keep_copies
        self.present = present or (lambda item: item)
        self.hold_draft = hold_draft or (lambda item: None)
        self.note_error = note_error
        self.on_outputs = on_outputs  # async (job, refs): a mode's own files (the long video's state)
        self.extra_folders = extra_folders
        self._clock = clock
        self._tokens = set()

    # ---------- the one finalization ----------

    async def finalize(self, job, *, record=None, notify=None) -> Finalized:
        """Turn a finished job into Library records (or drafts), once, in the documented order.

        Raises EmptyResult when the engine reports no files: that is a job failure, not a media one,
        and the caller decides what the window says about it.
        """
        record = record if record is not None else job.get("record")
        pid = job["prompt_id"]
        if self.outputs_of is None:
            raise MediaUnavailable("этот путь не умеет спрашивать движок о файлах задания")
        outputs = await self.outputs_of(pid)
        refs = output_refs(outputs)
        if self.on_outputs is not None:
            await self.on_outputs(job, refs)
        picked = pick_results(job, refs)
        if not picked:
            raise EmptyResult("движок не вернул ни видео, ни изображение")
        draft = is_draft(job, record)

        planned = []
        for node, ref in picked:
            media_id = media_id_for(record.job_id if record is not None else "", node, ref)
            known = self.media.get(media_id)
            created = known.created_at if known is not None and known.created_at else self._clock()
            item = self._item(job, node, ref, media_id, created)
            kind = MediaKind.of_name(ref["filename"])
            backend_file = {**ref, "node": node}
            if draft:
                media = MediaRecord(media_id=media_id, kind=kind, state=MediaState.DRAFT,
                                    backend_file=backend_file, created_at=created)
            else:
                copy = await self.archive(item)
                media = MediaRecord(media_id=media_id, kind=kind, state=copy.state, path=copy.path,
                                    bytes=copy.bytes, backend_file=backend_file, library_id=media_id,
                                    created_at=created, error=copy.error)
            planned.append((item, media))

        with self.db.transaction(), self._restore_record_on_error(record):
            for item, media in planned:
                self.library.repo.add(item)   # the row and the file are both there, or neither is shown
                if record is None:
                    self.media.save(media)
                    continue
                if not any(m.media_id == media.media_id for m in record.results):
                    record.add_result(media)
                # the media row first: `job_results` points at it, and the database checks that
                self.media.save(media, job_id=record.job_id)
                if self.jobs.get(record.job_id) is not None:
                    self.results.register(record.job_id, media)  # unique: a retry adds nothing
            if record is not None:
                record.succeed(now=self._clock())
                self.jobs.save(record)

        for item, media in planned:   # the rows are committed: only now the window may see them
            if draft:
                self.hold_draft(item)
            else:
                self.library.note_committed(item)
        items = [self.present(item) for item, _ in planned]
        if notify is not None:
            await notify({"type": "done", "prompt_id": pid, "item": items[0], "items": items})
        return Finalized(items=items, draft=draft)

    @contextlib.contextmanager
    def _restore_record_on_error(self, record):
        """A rollback of the database must roll back the record in memory too, or the retry lies."""
        if record is None:
            yield
            return
        before = (record.state, record.version, record.updated_at, record.error, record.cancel_requested,
                  list(record.results))
        try:
            yield
        except BaseException:
            (record.state, record.version, record.updated_at, record.error,
             record.cancel_requested) = before[:5]
            record.results = before[5]
            raise

    def _item(self, job, node, ref, media_id, created) -> dict:
        """The Library item of one engine output. The id is fixed by the output, not rolled here."""
        item = dict(job.get("meta") or {}, id=media_id, prompt_id=job["prompt_id"],
                    date=datetime.fromtimestamp(created).isoformat(timespec="seconds"),
                    fav=False, file=dict(ref), ext=Path(ref["filename"]).suffix.lstrip(".").lower() or "mp4")
        if not str(ref["filename"]).lower().endswith(VIDEO_SUFFIXES):   # a picture workflow: every saved picture is a result
            item["kind"] = "image"
            title = (job.get("titles") or {}).get(node, "")   # "Результат: апскейл (после…)" -> "апскейл"
            label = re.split(r"\s*[(\[]", str(title).split(":")[-1].strip())[0].strip()[:24]
            if label:
                item["label"] = label
        meta = ref.get("metadata") or {}
        if meta.get("width") and meta.get("height") and not (item.get("mode") == "upscale" and item.get("res")):
            item["res"] = f'{meta["width"]}×{meta["height"]}'
        return item

    # ---------- the independent copy ----------

    async def archive(self, item, *, token=None) -> Copy:
        """Make (or find) the program's own copy of a finished file. Never raises for a failed copy."""
        ref = item.get("file") or {}
        folder = self.results_dir()
        if not self.keep_copies() or not folder:
            return Copy(MediaState.LIBRARY, path=ref.get("path") if ref.get("type") == "local" else None)
        if ref.get("type") == "local":   # the file is already ours (cloud, FFmpeg): no second copy
            return Copy(MediaState.LIBRARY, path=ref.get("path"))
        local = item.get("local")
        if local and Path(local).exists():
            return Copy(MediaState.ARCHIVED, path=str(local), bytes=Path(local).stat().st_size)
        dest = self._archive_path(item, Path(folder))
        try:
            if dest.exists() and dest.stat().st_size > 0:
                item["local"] = str(dest)   # a copy of an earlier attempt: no second download
                return Copy(MediaState.ARCHIVED, path=str(dest), bytes=dest.stat().st_size)
            source = self._local_source(ref)
            if source is not None:
                written = await self.files.copy_file(source, dest, token=token)
            else:
                written = await self._download(ref, dest, token=token)
            item["local"] = str(dest)
            return Copy(MediaState.ARCHIVED, path=str(dest), bytes=written)
        except Exception as e:  # noqa: BLE001 - a copy that failed must not look like a lost generation
            self.note_error(f"Не удалось сохранить копию результата: {type(e).__name__}: {e}")
            return Copy(MediaState.ARCHIVE_FAILED, error=f"{type(e).__name__}: {e}")

    async def _download(self, ref, dest, *, token=None) -> int:
        if self.source is None:
            raise MediaUnavailable("скачивание файлов с этого движка не настроено")
        async with self.source.open_output(ref) as stream:
            return await self.files.save_stream(dest, stream.chunks, expected_bytes=stream.expected_bytes,
                                                token=token)

    def _local_source(self, ref):
        folder = self.output_dir()
        if not folder or ref.get("type", "output") != "output":
            return None
        src = Path(folder) / ref.get("subfolder", "") / ref["filename"]
        return src if src.exists() else None

    def _archive_path(self, item, folder) -> Path:
        """A stable name: the same file gets the same destination on every attempt.

        The date comes from the media record when one exists, and an already made copy is found by
        its id and filename, so a crash between the rename and the row write does not copy twice.
        """
        filename = item["file"]["filename"]
        name = f"{str(item.get('date') or '')[:10].replace('-', '')}_{item['id']}_{filename}"
        target = folder / name
        if target.exists():
            return target
        suffix = f"_{item['id']}_{filename}"
        if folder.is_dir():
            for candidate in sorted(folder.iterdir()):
                if candidate.is_file() and candidate.name.endswith(suffix):
                    return candidate
        return target

    # ---------- the Library record ----------

    async def keep(self, item) -> dict:
        """«Добавить в библиотеку»: the draft becomes a Library record once, then gets its copy.

        A failed row write raises (the draft stays a draft and the button still works); a failed copy
        only marks the record as `archive_failed` and can be retried without a new generation.
        """
        existing = self.library.get(item["id"])
        if existing is not None:
            return existing
        self.library.add(item)          # the row is written first: it may raise, the draft is kept
        copy = await self.archive(item)
        self._save_media(item, copy, library=True)
        return item

    async def archive_item(self, item) -> bool:
        """Copy a Library item again (the backfill of «Результаты»). True when a copy exists now."""
        copy = await self.archive(item)
        if copy.state is not MediaState.ARCHIVED:
            return False
        try:
            self.library.save(item)     # one changed row: the path of the copy
        except Exception as e:  # noqa: BLE001 - the file is made; a failed row is reported, not hidden
            self.note_error(f"Копия сделана, но путь к ней не записался: {e}")
        self._save_media(item, copy, library=True)
        return True

    def _save_media(self, item, copy, *, library=False):
        """The media row is the audit of the file, not its owner: a failure here is reported only."""
        try:
            filename = (item.get("file") or {}).get("filename")
            kind = MediaKind.of_name(filename)
            media = self.media.get(item["id"]) or MediaRecord(media_id=item["id"], kind=kind,
                                                              created_at=self._clock())
            media.kind = kind
            media.state = copy.state
            media.path = copy.path
            media.bytes = copy.bytes if copy.bytes is not None else media.bytes
            media.error = copy.error
            if library or self.library.get(item["id"]) is not None:
                media.library_id = item["id"]
            media.backend_file = dict(item.get("file") or {})
            self.media.save(media)
        except Exception as e:  # noqa: BLE001 - the Library row is the truth; this is a trace
            self.note_error(f"Состояние файла не записалось: {e}")

    # ---------- temporary files ----------

    def cleanup(self, folders=None) -> list:
        """Remove the `.part` files of earlier runs, and nothing else.

        Only our own naming (`.<name>.<pid>.<random>.part`), only in the folders this program writes
        to, only when no live writer owns the file and no record points at it. Files the program did
        not make, and files of an active writer, are never touched.
        """
        active = self.files.active_parts()
        referenced = self._referenced_paths()
        removed = []
        for folder in [*(folders if folders is not None else self._known_folders())]:
            folder = Path(folder)
            if not folder.is_dir():
                continue
            for part in sorted(folder.glob(".*.part")):
                if part in active or str(part) in referenced:
                    continue
                try:
                    part.unlink()
                    removed.append(str(part))
                except OSError:
                    pass
        return removed

    def _known_folders(self) -> list:
        folders = []
        for value in (self.results_dir(), *self.extra_folders()):
            if value:
                folders.append(Path(value))
        return folders

    def _referenced_paths(self) -> set:
        paths = set()
        for row in self.db.query("SELECT path FROM media WHERE path IS NOT NULL"):
            if row["path"]:
                paths.add(str(row["path"]))
        for item in list(self.library.items):
            for value in (item.get("local"), (item.get("file") or {}).get("path")):
                if value:
                    paths.add(str(value))
        return paths

    # ---------- who stops the writers ----------

    def token(self) -> CancelToken:
        """A token for a writer of this service; `shutdown()` cancels every one of them."""
        token = CancelToken()
        self._tokens.add(token)
        return token

    async def shutdown(self, timeout=10.0) -> bool:
        """Ask the writers to stop and wait for them; never deletes a file a writer is using."""
        for token in list(self._tokens):
            token.cancel()
        self._tokens.clear()
        return await self.files.wait_idle(timeout=timeout)
