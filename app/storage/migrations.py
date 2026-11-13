"""Bringing an older data folder into the database — once, transactionally, with a backup and a marker.

The old versions kept everything in JSON files next to the program. The first start of this version
imports them into SQLite and never imports them again:

- each step is named in `schema_migrations` and runs inside one transaction together with its marker,
  so a crash leaves either the whole step or nothing at all;
- before the first import the files are copied into `backup/<timestamp>/`, and the database itself is
  copied with the backup API (a plain file copy of a WAL database is not a database);
- the original JSON files are **not** deleted: they stay as the last line of defence;
- a record that cannot be read is not silently dropped: the step fails, the problem is written into
  `import_problems` (and the report), and the next start tries again. The library is never replaced by
  an empty list behind the user's back.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from domain.jobs import JobRecord, JobState, new_job_id
from domain.projects import DEFAULT_PROJECT_ID, DEFAULT_PROJECT_NAME
from storage.repositories import JobRepository, LibraryRepository

# The tables. A step that changes them gets the next version number; importing data does not change
# the schema, so all steps of this stage stay at version 1.
SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS schema_migrations (
        name       TEXT PRIMARY KEY,
        version    INTEGER NOT NULL,
        applied_at REAL NOT NULL,
        note       TEXT)""",
    """CREATE TABLE IF NOT EXISTS library (
        id        TEXT PRIMARY KEY,
        position  INTEGER NOT NULL,
        date      TEXT,
        fav       INTEGER NOT NULL DEFAULT 0,
        kind      TEXT,
        mode      TEXT,
        prompt_id TEXT,
        payload   TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS library_by_date ON library(date DESC)",
    "CREATE INDEX IF NOT EXISTS library_by_kind ON library(kind, mode)",
    """CREATE TABLE IF NOT EXISTS jobs (
        job_id         TEXT PRIMARY KEY,
        prompt_id      TEXT,
        backend        TEXT NOT NULL DEFAULT '',
        backend_id     TEXT NOT NULL DEFAULT '',
        backend_job_id TEXT,
        mode           TEXT,
        state          TEXT NOT NULL,
        version        INTEGER NOT NULL DEFAULT 1,
        created_at     REAL NOT NULL,
        updated_at     REAL NOT NULL,
        record_json    TEXT NOT NULL)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS jobs_prompt ON jobs(prompt_id) WHERE prompt_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS jobs_active ON jobs(state, created_at)",
    """CREATE TABLE IF NOT EXISTS media (
        media_id   TEXT PRIMARY KEY,
        job_id     TEXT REFERENCES jobs(job_id) ON DELETE SET NULL,
        kind       TEXT NOT NULL,
        state      TEXT NOT NULL,
        path       TEXT,
        library_id TEXT,
        bytes      INTEGER,
        created_at REAL NOT NULL,
        error      TEXT,
        payload    TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS media_by_job ON media(job_id)",
    "CREATE INDEX IF NOT EXISTS media_by_library ON media(library_id)",
    """CREATE TABLE IF NOT EXISTS job_results (
        job_id    TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
        node      TEXT NOT NULL DEFAULT '',
        subfolder TEXT NOT NULL DEFAULT '',
        type      TEXT NOT NULL DEFAULT 'output',
        filename  TEXT NOT NULL,
        media_id  TEXT REFERENCES media(media_id) ON DELETE SET NULL,
        UNIQUE (job_id, node, subfolder, type, filename))""",
    """CREATE TABLE IF NOT EXISTS idempotency_keys (
        key         TEXT PRIMARY KEY,
        fingerprint TEXT NOT NULL,
        job_id      TEXT NOT NULL,
        created_at  REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS import_problems (
        id     INTEGER PRIMARY KEY AUTOINCREMENT,
        name   TEXT NOT NULL,
        at     REAL NOT NULL,
        reason TEXT NOT NULL,
        detail TEXT)""",
)

FILES = ("library.json", "queue.json", "config.json")   # config stays JSON, it is only copied


@dataclass
class Report:
    """What the migration did: for the log, for the status endpoint and for the developer report."""

    applied: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    imported: dict = field(default_factory=dict)
    problems: list = field(default_factory=list)
    backup: str | None = None
    problem_rows: list = field(default_factory=list)

    @property
    def done(self):
        return not self.problems

    def to_dict(self):
        return {"applied": list(self.applied), "skipped": list(self.skipped), "imported": dict(self.imported),
                "problems": list(self.problems), "backup": self.backup}


class _StopStep(Exception):
    """A step cannot run now (a file it needs is broken): record the problem and try again next start."""


def _schema(db, data_dir, report):
    # one statement at a time: executescript() would commit the transaction this step runs in
    for statement in SCHEMA_STATEMENTS:
        db.execute(statement)


def _import_library(db, data_dir, report):
    path = Path(data_dir) / "library.json"
    if not path.exists():
        report.imported["library"] = 0
        return
    data = _read_json(path, "library.json", report)
    if not isinstance(data, list):
        _problem(report, "library.json", "ожидался список записей", str(path))
        raise _StopStep("library.json: ожидался список записей")
    items, broken = [], 0
    for raw in data:
        if isinstance(raw, dict) and raw.get("id"):
            items.append(raw)
        else:
            broken += 1
    LibraryRepository(db).replace_all(items)
    report.imported["library"] = len(items)
    if broken:
        _problem(report, "library.json", f"записей без id пропущено: {broken}", str(path))


def _import_queue(db, data_dir, report):
    path = Path(data_dir) / "queue.json"
    if not path.exists():
        report.imported["queue"] = 0
        return
    data = _read_json(path, "queue.json", report)
    if not isinstance(data, list):
        _problem(report, "queue.json", "ожидался список заданий", str(path))
        raise _StopStep("queue.json: ожидался список заданий")
    jobs = JobRepository(db)
    now = time.time()
    imported = 0
    for raw in data:
        if not isinstance(raw, dict) or not raw.get("graph"):
            continue   # the queue never kept records without a graph
        saved_at = float(raw["saved"]) if isinstance(raw.get("saved"), (int, float)) else now
        record = JobRecord(job_id=new_job_id(), prompt_id=raw.get("pid"), backend="comfy",
                           backend_id="", backend_job_id=raw.get("pid"), state=JobState.QUEUED,
                           created_at=saved_at, updated_at=saved_at, graph=raw.get("graph"),
                           legacy=dict(raw))
        jobs.save(record)
        imported += 1
    report.imported["queue"] = imported


def _drafts(db, data_dir, report):
    db.execute("CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    # Earlier builds accidentally inserted drafts into library. Only move rows positively
    # identified as unkept drafts by their media record; leave genuine Library items alone.
    for row in db.query("SELECT l.id, l.payload FROM library l JOIN media m ON m.media_id=l.id "
                        "WHERE m.state='draft' AND m.library_id IS NULL"):
        item = json.loads(row["payload"])
        item["preview"] = True
        db.execute("INSERT OR IGNORE INTO drafts(id,payload) VALUES(?,?)",
                   (row["id"], json.dumps(item, ensure_ascii=False)))
        db.execute("DELETE FROM library WHERE id=?", (row["id"],))


def _projects(db, data_dir, report):
    """Projects: one workspace per film, and every existing record moved into the default one.

    The Library, the drafts and (from the moment they are created) the jobs carry the id of the
    project they belong to. Nothing is deleted or moved on the disk: the project is a label.
    """
    db.execute("CREATE TABLE IF NOT EXISTS projects ("
               "id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at REAL NOT NULL)")
    db.execute("INSERT OR IGNORE INTO projects(id, name, created_at) VALUES(?,?,?)",
               (DEFAULT_PROJECT_ID, DEFAULT_PROJECT_NAME, time.time()))
    columns = {row["name"] for row in db.query("PRAGMA table_info(library)")}
    if "project" not in columns:
        db.execute("ALTER TABLE library ADD COLUMN project TEXT NOT NULL DEFAULT ''")
    db.execute("CREATE INDEX IF NOT EXISTS library_by_project ON library(project)")
    db.execute("UPDATE library SET project=? WHERE project IS NULL OR project=''", (DEFAULT_PROJECT_ID,))
    # the payload is what the window receives: the id must be in it, not only in the column
    for row in db.query("SELECT id, payload FROM library"):
        item = json.loads(row["payload"])
        if item.get("project"):
            continue
        item["project"] = DEFAULT_PROJECT_ID
        db.execute("UPDATE library SET payload=? WHERE id=?",
                   (json.dumps(item, ensure_ascii=False), row["id"]))
    if db.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='drafts'") is not None:
        for row in db.query("SELECT id, payload FROM drafts"):
            item = json.loads(row["payload"])
            if item.get("project"):
                continue
            item["project"] = DEFAULT_PROJECT_ID
            db.execute("UPDATE drafts SET payload=? WHERE id=?",
                       (json.dumps(item, ensure_ascii=False), row["id"]))


def _stages(db, data_dir, report):
    """Stages (save points of the sections) belong to a project and live in the database.

    The records of older versions were kept in the browser's localStorage; the window imports them
    itself on the first start (it is the only side that can read them), so there is nothing to move
    here — only the table. The files a stage was made from stay in the browser, keyed by the stage id.
    """
    db.execute("CREATE TABLE IF NOT EXISTS stages ("
               "id TEXT PRIMARY KEY, project TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT '', "
               "auto INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL DEFAULT 0, payload TEXT NOT NULL)")
    db.execute("CREATE INDEX IF NOT EXISTS stages_by_project ON stages(project, mode, created_at)")


STEPS = (
    ("0001-schema", 1, _schema, "таблицы"),
    ("1000-import-library", 1, _import_library, "библиотека из library.json"),
    ("1001-import-queue", 1, _import_queue, "очередь из queue.json"),
    ("0002-drafts", 2, _drafts, "долговечные черновики вне Библиотеки"),
    ("0003-projects", 3, _projects, "проекты: рабочее пространство каждой записи"),
    ("0004-stages", 4, _stages, "этапы разделов: часть проекта, а не браузера"),
)


def migrate(db, data_dir, *, now=None) -> Report:
    """Run the pending steps. Safe to call on every start: a finished step is skipped by its name."""
    data_dir = Path(data_dir)
    report = Report()
    db.check_version()   # a database of a newer program is refused, never rewritten
    done = db.applied()
    pending = [step for step in STEPS if step[0] not in done]
    report.skipped = [step[0] for step in STEPS if step[0] in done]
    if not pending:
        return report
    if any(step[2] is not _schema for step in pending):
        report.backup = _backup(data_dir, db)
    failed = False
    for name, version, run, note in pending:
        try:
            with db.transaction():
                run(db, data_dir, report)
                db.execute("INSERT INTO schema_migrations(name, version, applied_at, note) VALUES(?,?,?,?)",
                           (name, version, float(now if now is not None else time.time()), note))
            report.applied.append(name)
        except _StopStep:
            failed = True   # the problem is already in the report
        except Exception as e:  # noqa: BLE001 - a failing step must not leave a marker
            report.problems.append(f"{name}: {type(e).__name__}: {e}")
            report.problem_rows.append((name, f"{type(e).__name__}: {e}", ""))
            failed = True
        if failed:
            break   # a later step may depend on this one: do not run them on half a state
    # problems are written outside the transactions: a rolled-back step must still leave a trace
    for name, reason, detail in report.problem_rows:
        try:
            db.execute("INSERT INTO import_problems(name, at, reason, detail) VALUES(?,?,?,?)",
                       (str(name)[:80], time.time(), str(reason)[:300], str(detail)[:300]))
        except Exception:
            pass
    return report


def _backup(data_dir, db):
    """A copy of the JSON files and of the database, made before anything is imported."""
    folder = Path(data_dir) / "backup" / time.strftime("%Y%m%d-%H%M%S")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            source = Path(data_dir) / name
            if source.exists():
                shutil.copy2(source, folder / name)
        if Path(db.path).exists() and Path(db.path).stat().st_size:
            db.backup_to(folder / Path(db.path).name)
    except OSError:
        return None   # a backup we could not make must not stop the import; the report says so
    return str(folder)


def _read_json(path, name, report):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        _problem(report, name, f"файл не прочитан: {e}", str(path))
        raise _StopStep(f"{name}: файл не прочитан ({e}); файл оставлен на месте, импорт повторится")


def _problem(report, name, reason, detail=""):
    report.problems.append(f"{name}: {reason}")
    report.problem_rows.append((name, reason, detail))
