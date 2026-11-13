"""The one place the program keeps its state in SQLite.

Concurrency is the interesting part, so it is decided here and not left to the callers:

- There is exactly one connection, and it belongs to the thread that opened it (the event loop
  thread of the app). Every write goes through this connection, so SQLite never waits on a lock
  taken by the same program, and a write of one row costs microseconds.
- Short transactions only: `transaction()` opens `BEGIN IMMEDIATE`, and nothing that talks to the
  network or copies a file may run inside it. A nested call joins the outer transaction.
- WAL with `synchronous=NORMAL`: a reader does not block a writer, and a crash loses at most the
  last commits, never the file. The side files (`-wal`, `-shm`) belong to the database: a copy of
  the folder must be made with `backup_to()`, not with a file copy while the app is running.
- A newer schema is refused loudly (`SchemaVersionError`) instead of being reinterpreted: an older
  version of the program must not write into a database it does not understand.

The app runs one copy per data folder (see `instance.py`), so a second process is not a case this
module has to survive; it still sets `busy_timeout` so that a stray reader cannot hang a write.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from domain.errors import SchemaVersionError

SCHEMA_VERSION = 3   # 2: durable drafts, 3: projects


class Database:
    """A SQLite file with one connection, short transactions and a version marker."""

    def __init__(self, path, *, timeout=5.0):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=timeout, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        try:
            self.check_version()
        except BaseException:
            self.conn.close()
            raise
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
        self._depth = 0

    # ---------- transactions ----------

    @contextmanager
    def transaction(self):
        """All or nothing. A nested block joins the outer transaction instead of nesting one."""
        if self._depth:
            self._depth += 1
            try:
                yield self
            finally:
                self._depth -= 1
            return
        self.conn.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield self
        except BaseException:
            self._depth = 0
            self.conn.execute("ROLLBACK")
            raise
        self._depth = 0
        self.conn.execute("COMMIT")

    # ---------- queries ----------

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)

    def executemany(self, sql, rows):
        return self.conn.executemany(sql, rows)

    def query(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()

    def one(self, sql, params=()):
        return self.conn.execute(sql, params).fetchone()

    def close(self):
        try:
            self.conn.close()
        finally:
            self._depth = 0

    def __del__(self):
        # a check that forgot to close its database must not leave the connection to the garbage collector
        try:
            self.conn.close()
        except Exception:
            pass

    # ---------- version ----------

    def applied(self):
        """The names of the migration steps that already ran, in order."""
        if not self._table_exists("schema_migrations"):
            return set()
        return {row["name"] for row in self.query("SELECT name FROM schema_migrations")}

    def _table_exists(self, name):
        return self.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)) is not None

    def check_version(self):
        """Refuse a database written by a newer version instead of writing into it blindly."""
        if not self._table_exists("schema_migrations"):
            return
        row = self.one("SELECT MAX(version) AS v FROM schema_migrations")
        version = (row["v"] if row else 0) or 0
        if version > SCHEMA_VERSION:
            raise SchemaVersionError(
                f"База данных создана более новой версией программы (схема {version} > {SCHEMA_VERSION}). "
                "Обновите программу: писать в такую базу нельзя.")

    def schema_version(self):
        if not self._table_exists("schema_migrations"):
            return 0
        row = self.one("SELECT MAX(version) AS v FROM schema_migrations")
        return (row["v"] if row else 0) or 0

    # ---------- copies ----------

    def backup_to(self, target):
        """A consistent copy of the database, WAL included. Safe while the app is running."""
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        side = sqlite3.connect(str(target))
        try:
            self.conn.backup(side)
        finally:
            side.close()
        return target
