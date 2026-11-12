"""One place where a job is created, sent, moved and finished.

The service owns the durable side of a submission, and that is its main reason to exist:

1. the job is written down as `submitting` **before** the POST leaves for the engine;
2. when the engine answers, its id is written down and the job becomes `queued`;
3. if the engine does not answer, the job becomes `uncertain` — it may or may not have been sent, so
   repeating the POST automatically could render the same clip twice. Only an explicit retry (a new
   job) or a reconciliation can settle it.

The `IdempotencyRepository` makes the same request under the same key return the same job, across
restarts and for two requests that arrive at the same moment (the second waits for the first).
All errors are service errors (`domain.errors`), never HTTP responses: the HTTP layer decides statuses.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time

from backends.base import BackendUnavailable, SubmitRejected
from domain.errors import JobConflict, JobNotFound
from domain.jobs import JobRecord, JobState, new_job_id


def spec_fingerprint(spec) -> str:
    """What makes two requests "the same": everything the job is made of, and nothing display-only."""
    payload = json.dumps(spec.params(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class JobService:
    """The lifecycle of a generation job, in terms of the domain and the repositories."""

    def __init__(self, repo, backend, *, keys=None, ids=new_job_id, clock=time.time, wait_seconds=30.0):
        self.repo = repo
        self.backend = backend
        self.keys = keys
        self._ids = ids
        self._clock = clock
        self._wait_seconds = wait_seconds
        self._in_flight = {}   # idempotency key -> Event: a second request with the same key waits

    # ---------- reading ----------

    def get(self, job_id):
        record = self.repo.get(job_id)
        if record is None:
            raise JobNotFound(f"Нет задания {job_id}")
        return record

    def by_prompt(self, prompt_id):
        return self.repo.by_prompt(prompt_id)

    def active(self):
        return self.repo.active()

    # ---------- creating and sending ----------

    def begin(self, spec, *, backend_id):
        """A job that is not sent yet: the state before the dangerous step."""
        now = self._clock()
        return JobRecord(job_id=self._ids(), spec=spec, backend=self.backend.name, backend_id=backend_id,
                         created_at=now, updated_at=now)

    async def submit(self, record):
        """Send one job, durably. Raises SubmitRejected (nothing was created) or BackendUnavailable."""
        if record.state is not JobState.CREATED:
            raise JobConflict(f"Задание уже отправлялось: {record.state.value}")
        record.apply(JobState.SUBMITTING, now=self._clock())
        self.repo.save(record)   # the dangerous moment is on disk before it happens
        try:
            result = await self.backend.submit(record.spec)
        except SubmitRejected as e:
            record.fail(str(e), now=self._clock())
            self.repo.save(record)
            raise
        except BackendUnavailable:
            record.apply(JobState.UNCERTAIN, now=self._clock())
            self.repo.save(record)
            raise
        except Exception:
            # an unexpected failure is still "we do not know whether the engine has it"
            record.apply(JobState.UNCERTAIN, now=self._clock())
            self.repo.save(record)
            raise
        record.backend_job_id = result.backend_job_id
        record.prompt_id = record.prompt_id or result.backend_job_id
        record.graph = result.graph
        record.legacy = {**(record.legacy or {}), "stages": dict(result.stages or {})}
        record.apply(JobState.QUEUED, now=self._clock())
        self.repo.save(record)
        return record

    async def submit_idempotent(self, spec, *, backend_id, key=None, fingerprint=None):
        """Send a job at most once per key. Returns (record, deduplicated).

        The key is reserved in the database before the first remote call, so a retry after a crash
        returns the job that was already sent instead of rendering it again. Two requests with one key
        that arrive together: the second waits for the first and gets its job.
        """
        if not key:
            return await self.submit(self.begin(spec, backend_id=backend_id)), False
        if self.keys is None:
            raise JobConflict("Это хранилище не умеет ключи повторных запросов")
        fingerprint = fingerprint or spec_fingerprint(spec)
        existing = self.keys.get(key)
        if existing:
            return await self._deduplicated(key, existing, fingerprint)
        record = self.begin(spec, backend_id=backend_id)
        if not self.keys.reserve(key, fingerprint, record.job_id, created_at=self._clock()):
            existing = self.keys.get(key) or {}
            return await self._deduplicated(key, existing, fingerprint)
        event = self._in_flight[key] = asyncio.Event()
        try:
            await self.submit(record)
        finally:
            event.set()
            self._in_flight.pop(key, None)
        return record, False

    async def _deduplicated(self, key, existing, fingerprint):
        if str(existing.get("fingerprint") or "") != str(fingerprint):
            raise JobConflict("Этот idempotency_key уже использован для другого запроса",
                              code="IDEMPOTENCY_CONFLICT",
                              hint="Новый запрос — новый ключ. Ключ нужен только для повтора того же самого.")
        record = self.repo.get(str(existing.get("job_id") or ""))
        if record is None:
            raise JobConflict("Задание по этому ключу не найдено: ключ остался, записи нет",
                              code="IDEMPOTENCY_LOST")
        event = self._in_flight.get(key)
        if event is not None and record.state in (JobState.CREATED, JobState.SUBMITTING):
            try:   # the first request is still sending it: wait for its answer instead of guessing
                await asyncio.wait_for(event.wait(), timeout=self._wait_seconds)
            except asyncio.TimeoutError:
                pass
            record = self.repo.get(record.job_id) or record
        return record, True

    # ---------- what the engine says ----------

    async def inspect(self, record):
        """Ask the engine about this job. Raises BackendUnavailable when it cannot answer."""
        return await self.backend.inspect(record)

    def apply_inspection(self, record, result):
        """Move the record by what the engine said; returns what the caller has to do next.

        "finalize" — the job is done on the engine's side; "failed" — it ended with an error;
        "attached" — it is alive there; "unknown" — the engine does not remember it (no resend!).
        """
        if result.state == "finished":
            self.finalize(record)
            return "finalize"
        if result.state == "failed":
            self.failed(record, result.error or "ComfyUI закончил задание с ошибкой")
            return "failed"
        target = {"queued": JobState.QUEUED, "running": JobState.RUNNING}.get(result.state)
        if target is not None and record.can(target):
            record.apply(target, now=self._clock())
            self.repo.save(record)
            return "attached"
        return "unknown"

    # ---------- transitions the engine confirms ----------

    def finalize(self, record):
        """The generation is over and the result is being registered. False when it already was."""
        if record.state is JobState.FINALIZING:
            return False
        record.apply(JobState.FINALIZING, now=self._clock())
        self.repo.save(record)
        return True

    def succeeded(self, record):
        if record.state is JobState.SUCCEEDED:
            return False
        record.succeed(now=self._clock())
        self.repo.save(record)
        return True

    def failed(self, record, message):
        if record.state.terminal:
            return False
        record.fail(message, now=self._clock())
        self.repo.save(record)
        return True

    def canceled(self, record):
        """The engine confirmed the stop (or the job was removed from its queue)."""
        if record.state is JobState.CANCELED:
            return False
        record.cancel_confirmed(now=self._clock())
        self.repo.save(record)
        return True

    # ---------- cancellation ----------

    async def request_cancel(self, record):
        """Ask a backend to stop the job. Cancelling is a request until the engine confirms it."""
        if record.state.terminal:
            raise JobConflict("Задание уже завершено: отменять нечего", code="ALREADY_FINISHED")
        if record.state is JobState.FINALIZING:
            raise JobConflict("Генерация уже закончилась: результат сохраняется")
        record.request_cancel(now=self._clock())
        accepted = await self.backend.cancel(record)
        if accepted and record.state is JobState.QUEUED:
            record.cancel_confirmed(now=self._clock())   # a queued job is removed at once
        self.repo.save(record)
        return accepted

    # ---------- an explicit retry ----------

    def retry(self, record, *, backend_id=None):
        """A new job for the same request. Never automatic: a retry is the user's or the agent's call."""
        if record.spec is None:
            raise JobConflict("У этого задания нет описания, по которому его можно повторить",
                              code="NO_SPEC")
        return self.begin(record.spec, backend_id=backend_id or record.backend_id)
