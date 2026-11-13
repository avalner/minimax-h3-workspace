"""Typed access to the tables. All SQL of the state lives here, not in the server.

What is stored and why:

- `library` — one row per Library item; the whole item (with fields this version does not know) is
  kept in `payload`, while the columns the window sorts and filters by are indexed copies.
- `jobs` — one row per job; the typed `JobRecord` is kept as JSON (schema versioned) and its results
  are separate rows, so a result can be found and made unique on its own.
- `media` / `job_results` — the files a job produced. `job_results` is unique by the engine's own
  identity of an output (job + node + subfolder + type + filename), which is what makes registering
  a result twice harmless.
- `idempotency_keys` — reserved before the first remote call (used by the agent API).
"""

from __future__ import annotations

import json

from domain.jobs import JobRecord
from domain.media import MediaRecord
from domain.projects import DEFAULT_PROJECT_ID, new_project_id


class LibraryRepository:
    """The Library list, in the order the window shows it (the newest first)."""

    def __init__(self, db):
        self.db = db

    def load(self):
        """Items in the order the window shows them; `project` comes from the row when the payload
        of an older version does not have it yet (the migration writes both, this is the safety net)."""
        items = []
        for row in self.db.query("SELECT payload, project FROM library ORDER BY position ASC, rowid ASC"):
            item = json.loads(row["payload"])
            if not item.get("project"):
                item["project"] = row["project"] or DEFAULT_PROJECT_ID
            items.append(item)
        return items

    def count(self):
        return self.db.one("SELECT COUNT(*) AS n FROM library")["n"]

    def add(self, item):
        """Insert a new item on top, or update the existing one in place (its position is kept).

        The project is a label on the record: a column for filtering and the same value inside the
        payload, so the window receives one item and does not join anything.
        """
        project = str(item.get("project") or DEFAULT_PROJECT_ID)
        item = {**item, "project": project}
        payload = json.dumps(item, ensure_ascii=False)
        columns = (item.get("date"), int(bool(item.get("fav"))), item.get("kind"), item.get("mode"),
                   item.get("prompt_id"), project)
        if self.db.one("SELECT 1 FROM library WHERE id=?", (item["id"],)):
            self.db.execute("UPDATE library SET date=?, fav=?, kind=?, mode=?, prompt_id=?, project=?, "
                            "payload=? WHERE id=?", (*columns, payload, item["id"]))
            return
        lowest = self.db.one("SELECT MIN(position) AS p FROM library")["p"]
        position = (lowest - 1) if lowest is not None else 0
        self.db.execute("INSERT INTO library(id, position, date, fav, kind, mode, prompt_id, project, payload) "
                        "VALUES(?,?,?,?,?,?,?,?,?)", (item["id"], position, *columns, payload))

    def delete(self, item_id):
        self.db.execute("DELETE FROM library WHERE id=?", (item_id,))

    def replace_all(self, items):
        """Used by the import only: the file is the truth at that moment."""
        self.db.execute("DELETE FROM library")
        for position, item in enumerate(items):
            self.db.execute("INSERT OR IGNORE INTO library(id, position, date, fav, kind, mode, prompt_id, payload) "
                            "VALUES(?,?,?,?,?,?,?,?)",
                            (item["id"], position, item.get("date"), int(bool(item.get("fav"))), item.get("kind"),
                             item.get("mode"), item.get("prompt_id"), json.dumps(item, ensure_ascii=False)))


class ProjectRepository:
    """The workspaces the user chooses from. The default one is created by the migration."""

    def __init__(self, db):
        self.db = db

    def all(self):
        return [dict(row) for row in self.db.query(
            "SELECT id, name, created_at FROM projects ORDER BY created_at ASC, rowid ASC")]

    def get(self, project_id):
        row = self.db.one("SELECT id, name, created_at FROM projects WHERE id=?", (project_id,))
        return dict(row) if row else None

    def exists(self, project_id) -> bool:
        return bool(project_id) and self.get(str(project_id)) is not None

    def create(self, name, *, now=0.0):
        """A new project with its own id: the name may be edited later, the id never changes."""
        project_id = new_project_id()
        self.db.execute("INSERT INTO projects(id, name, created_at) VALUES(?,?,?)",
                        (project_id, str(name), float(now)))
        return self.get(project_id)


class DraftRepository:
    """Unkept results survive restarts independently of the Library and job retention."""

    def __init__(self, db):
        self.db = db

    def load(self):
        return [json.loads(row["payload"]) for row in self.db.query(
            "SELECT payload FROM drafts ORDER BY rowid ASC")]

    def save(self, item):
        self.db.execute("INSERT INTO drafts(id,payload) VALUES(?,?) "
                        "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                        (item["id"], json.dumps({**item, "preview": True}, ensure_ascii=False)))

    def delete(self, item_id):
        self.db.execute("DELETE FROM drafts WHERE id=?", (item_id,))


class MediaRepository:
    """The files a job produced, independent of whether they are in the Library yet."""

    def __init__(self, db):
        self.db = db

    def save(self, media, job_id=None):
        record = media.to_dict()
        self.db.execute(
            "INSERT INTO media(media_id, job_id, kind, state, path, library_id, bytes, created_at, error, payload) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(media_id) DO UPDATE SET job_id=COALESCE(excluded.job_id, media.job_id), kind=excluded.kind, "
            "state=excluded.state, path=excluded.path, library_id=excluded.library_id, bytes=excluded.bytes, "
            "error=excluded.error, payload=excluded.payload",
            (record["media_id"], job_id, record["kind"], record["state"], record["path"], record["library_id"],
             record["bytes"], record["created_at"], record["error"], json.dumps(record, ensure_ascii=False)))

    def by_job(self, job_id):
        rows = self.db.query("SELECT payload FROM media WHERE job_id=? ORDER BY rowid ASC", (job_id,))
        return [MediaRecord.from_dict(json.loads(row["payload"])) for row in rows]

    def get(self, media_id):
        row = self.db.one("SELECT payload FROM media WHERE media_id=?", (media_id,))
        return MediaRecord.from_dict(json.loads(row["payload"])) if row else None

    def delete(self, media_id):
        """Forget a file record (a deleted draft): `job_results` rows point at it with SET NULL."""
        self.db.execute("DELETE FROM media WHERE media_id=?", (media_id,))


class ResultRepository:
    """Which engine output a job produced — unique, so registering it twice is harmless."""

    def __init__(self, db):
        self.db = db

    def register(self, job_id, media):
        """True when the row is new (the first time this output of this job is seen)."""
        ref = media.backend_file or {}
        filename = str(ref.get("filename") or "")
        if not filename:
            return False
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO job_results(job_id, node, subfolder, type, filename, media_id) "
            "VALUES(?,?,?,?,?,?)",
            (job_id, str(ref.get("node") or ""), str(ref.get("subfolder") or ""),
             str(ref.get("type") or "output"), filename, media.media_id))
        return cursor.rowcount > 0

    def by_job(self, job_id):
        return self.db.query("SELECT * FROM job_results WHERE job_id=? ORDER BY rowid ASC", (job_id,))


class JobRepository:
    """Jobs: the typed record, its state, and its results in separate rows."""

    def __init__(self, db, media=None, results=None):
        self.db = db
        self.media = media or MediaRepository(db)
        self.results = results or ResultRepository(db)

    def save(self, record):
        data = record.to_dict()
        data.pop("results", None)   # results live in their own rows
        self.db.execute(
            "INSERT INTO jobs(job_id, prompt_id, backend, backend_id, backend_job_id, mode, state, version, "
            "created_at, updated_at, record_json) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(job_id) DO UPDATE SET prompt_id=excluded.prompt_id, backend=excluded.backend, "
            "backend_id=excluded.backend_id, backend_job_id=excluded.backend_job_id, mode=excluded.mode, "
            "state=excluded.state, version=excluded.version, updated_at=excluded.updated_at, "
            "record_json=excluded.record_json",
            (record.job_id, record.prompt_id, record.backend, record.backend_id, record.backend_job_id,
             (record.spec.mode if record.spec else ""), record.state.value, record.version, record.created_at,
             record.updated_at, json.dumps(data, ensure_ascii=False)))
        for media in list(record.results):
            self.media.save(media, job_id=record.job_id)
            self.results.register(record.job_id, media)

    def get(self, job_id):
        row = self.db.one("SELECT record_json FROM jobs WHERE job_id=?", (job_id,))
        return self._record(row, job_id) if row else None

    def by_prompt(self, prompt_id):
        row = self.db.one("SELECT job_id, record_json FROM jobs WHERE prompt_id=?", (prompt_id,))
        return self._record(row, row["job_id"]) if row else None

    def _record(self, row, job_id):
        record = JobRecord.from_dict(json.loads(row["record_json"]))
        record.results = self.media.by_job(job_id)
        return record

    def active(self):
        """Jobs that are not finished, oldest first (the order the engine queues them in)."""
        rows = self.db.query("SELECT job_id, record_json FROM jobs "
                             "WHERE state NOT IN ('succeeded','failed','canceled') ORDER BY created_at ASC")
        return [self._record(row, row["job_id"]) for row in rows]

    def delete(self, job_id):
        self.db.execute("DELETE FROM jobs WHERE job_id=?", (job_id,))

    def count(self):
        return self.db.one("SELECT COUNT(*) AS n FROM jobs")["n"]

    # -- how a job ended: the row stays after the job leaves the queue, so the details window and
    # -- a reconnecting window can still answer. The queue is `active()`; these are the outcomes.

    FINAL = ("succeeded", "failed", "canceled")
    OUTCOME_KEEP = 300
    OUTCOME_DAYS = 7.0

    def outcomes(self, limit=100):
        """The latest finished jobs, newest first: what a window may have missed while it was gone."""
        rows = self.db.query("SELECT job_id, record_json FROM jobs "
                             "WHERE state IN ('succeeded','failed','canceled') "
                             "ORDER BY updated_at DESC LIMIT ?", (int(limit),))
        return [self._record(row, row["job_id"]) for row in rows]

    def note_message(self, prompt_id, message=""):
        """Write how a job ended into its record, before the event is delivered. None when unknown."""
        record = self.by_prompt(prompt_id) if prompt_id else None
        if record is None:
            return None
        record.legacy = {**(record.legacy or {}), "message": message or ""}
        self.save(record)
        return record

    def prune_outcomes(self, keep=None, days=None, now=None):
        """Forget old outcomes: a week and a few hundred rows are enough for the details window."""
        import time as _time
        keep = self.OUTCOME_KEEP if keep is None else int(keep)
        days = self.OUTCOME_DAYS if days is None else float(days)
        cutoff = (now if now is not None else _time.time()) - days * 86400
        self.db.execute("DELETE FROM jobs WHERE state IN ('succeeded','failed','canceled') "
                        "AND updated_at < ?", (cutoff,))
        self.db.execute(
            "DELETE FROM jobs WHERE state IN ('succeeded','failed','canceled') AND job_id NOT IN ("
            " SELECT job_id FROM jobs WHERE state IN ('succeeded','failed','canceled')"
            " ORDER BY updated_at DESC LIMIT ?)", (keep,))


class IdempotencyRepository:
    """A key is reserved before the remote call, so a retry after a crash does not render twice."""

    def __init__(self, db):
        self.db = db

    def get(self, key):
        row = self.db.one("SELECT * FROM idempotency_keys WHERE key=?", (key,))
        return dict(row) if row else None

    def reserve(self, key, fingerprint, job_id, created_at=0.0):
        """True when the key was free; False when it is already taken."""
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO idempotency_keys(key, fingerprint, job_id, created_at) VALUES(?,?,?,?)",
            (key, fingerprint, job_id, created_at))
        return cursor.rowcount > 0

    def drop(self, key):
        self.db.execute("DELETE FROM idempotency_keys WHERE key=?", (key,))
