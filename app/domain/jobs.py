"""A generation job: what was asked for, and where it is now.

Two things live here, deliberately separated:

- `JobSpec` — the request, typed and validated. The same spec must come out of the window and out of
  the agent API for the same request, and it must be serializable with a schema version, so a job can
  be stored, restored and compared later.
- `JobRecord` — the progress of that request through the engine. It has one method that changes the
  state (`apply`), which checks the transition, bumps the record version and never lets a late event
  overwrite a final outcome.

The identifiers are not the same thing and are kept apart:

    job_id          stable internal identifier, ours (a job keeps it even if the engine is restarted)
    prompt_id       the public identifier the window and the agent API have always used
    backend_job_id  what the engine itself calls the job (ComfyUI prompt id, cloud task id)

For ComfyUI today `prompt_id` and `backend_job_id` are the same string; for the cloud they are not
(`rh-<task>` against `<task>`). Nothing here assumes they are equal.

Progress is not part of the state: `set_progress` does not change the record version, because a step
of a sampler is not a business event and must not be written to a database on every tick.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from domain.errors import InvalidSpec, InvalidTransition, JobConflict, SchemaVersionError

SCHEMA_VERSION = 1
MIN_SECONDS = 3.0
MAX_SECONDS = 25.0   # H3 itself is made for up to 15 s; longer clips are allowed, quality is the user's call
MAX_SEED = 2 ** 50


class JobState(str, Enum):
    """Where a job is. Only the transitions in `TRANSITIONS` are possible."""

    CREATED = "created"        # the record exists, nothing was sent anywhere
    SUBMITTING = "submitting"  # a POST to the engine is in flight: this is the dangerous moment
    UNCERTAIN = "uncertain"    # the POST may or may not have reached the engine: reconcile before retry
    QUEUED = "queued"          # the engine accepted it and has not started it
    RUNNING = "running"        # the engine is generating
    FINALIZING = "finalizing"  # generation finished; the result is being found, checked and registered
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"

    @property
    def terminal(self) -> bool:
        return self in (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELED)


# A finalizing job is not a running GPU job any more: the generation has already happened, so
# cancelling it would throw the result away. That is why FINALIZING has no CANCELED target.
TRANSITIONS = {
    JobState.CREATED: {JobState.SUBMITTING, JobState.FAILED, JobState.CANCELED},
    JobState.SUBMITTING: {JobState.QUEUED, JobState.UNCERTAIN, JobState.FAILED, JobState.CANCELED},
    JobState.UNCERTAIN: {JobState.QUEUED, JobState.RUNNING, JobState.FINALIZING, JobState.FAILED, JobState.CANCELED},
    # a queued job can already be finished in the engine's history (the watch finds it there), so it
    # may go straight to finalizing without a "running" event that we never received
    JobState.QUEUED: {JobState.RUNNING, JobState.FINALIZING, JobState.CANCELED, JobState.FAILED, JobState.UNCERTAIN},
    JobState.RUNNING: {JobState.FINALIZING, JobState.FAILED, JobState.CANCELED, JobState.UNCERTAIN},
    JobState.FINALIZING: {JobState.SUCCEEDED, JobState.FAILED},
    JobState.SUCCEEDED: set(),
    JobState.FAILED: set(),
    JobState.CANCELED: set(),
}

# What the window and the agent API have always seen. `finalizing` is shown as running because the
# result is not in the Library yet and the job is not finished — it is only never cancelled.
PUBLIC_STATE = {
    JobState.CREATED: "queued",
    JobState.SUBMITTING: "queued",
    JobState.UNCERTAIN: "queued",
    JobState.QUEUED: "queued",
    JobState.RUNNING: "running",
    JobState.FINALIZING: "running",
    JobState.SUCCEEDED: "done",
    JobState.FAILED: "error",
    JobState.CANCELED: "interrupted",
}


class ResultPolicy(str, Enum):
    """How the result reaches the user. The difference is a decision, not an accident."""

    LIBRARY = "library"  # the finished file goes to the Library by itself
    DRAFT = "draft"      # it waits as a draft until «Добавить в библиотеку»

    @classmethod
    def of(cls, value) -> "ResultPolicy":
        try:
            return cls(value)
        except ValueError:
            raise InvalidSpec(f"Неизвестная политика результата: {value}")


def public_state(state) -> str:
    """The state string the window and the agent API have always received."""
    return PUBLIC_STATE[JobState(state)]


def parse_state(value) -> JobState:
    try:
        return JobState(value)
    except ValueError:
        raise InvalidSpec(f"Неизвестное состояние задания: {value}")


def new_job_id() -> str:
    """An internal id: ours, and not the engine's."""
    import uuid

    return uuid.uuid4().hex


def new_seed() -> int:
    return random.randrange(MAX_SEED)


@dataclass(frozen=True)
class InputRef:
    """A file the job is made from: a role, a kind and where it can be taken from."""

    role: str
    kind: str
    comfy: str | None = None    # a name inside ComfyUI's input folder
    asset: str | None = None    # an asset of the agent API
    url: str | None = None      # a ready address

    def to_dict(self) -> dict:
        return {k: v for k, v in (("role", self.role), ("kind", self.kind), ("comfy", self.comfy),
                                  ("asset", self.asset), ("url", self.url)) if v}

    @classmethod
    def from_dict(cls, data: Mapping) -> "InputRef":
        return cls(role=str(data.get("role") or ""), kind=str(data.get("kind") or "file"),
                   comfy=data.get("comfy"), asset=data.get("asset"), url=data.get("url"))


@dataclass(frozen=True)
class JobSpec:
    """What to generate, in full: the same request must produce the same spec from any entry point."""

    mode: str
    prompt: str
    seed: int
    seconds: float
    width: int
    height: int
    aspect: str = ""
    quality: str = ""
    model: str = "fast"             # "fast" (FastH3) or "fused" (Fused Turbo, its own file)
    acceleration: str = "normal"    # "normal" or "sla"
    models: Mapping[str, str] = field(default_factory=dict)   # role -> file name: a snapshot, not a lookup
    loras: tuple = ()                                          # ((name, weight), ...)
    inputs: tuple = ()                                         # (InputRef, ...)
    result_policy: ResultPolicy = ResultPolicy.LIBRARY
    source: str = "ui"                                         # "ui" | "agent" | ...
    sub: str = ""                                              # the short label the window shows
    extras: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not str(self.mode or "").strip():
            raise InvalidSpec("Не указан режим задания")
        if not str(self.prompt or "").strip():
            raise InvalidSpec("Пустой промпт")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise InvalidSpec("seed должен быть целым числом")
        if not 0 <= self.seed < MAX_SEED:
            raise InvalidSpec("seed вне допустимого диапазона")
        if not isinstance(self.seconds, (int, float)) or not MIN_SECONDS - 1e-9 <= self.seconds <= MAX_SECONDS + 1e-9:
            raise InvalidSpec(f"Длительность должна быть от {MIN_SECONDS:g} до {MAX_SECONDS:g} секунд")
        for name, value in (("ширина", self.width), ("высота", self.height)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise InvalidSpec(f"Неверная {name} кадра: {value!r}")
        if self.model not in ("fast", "fused"):
            raise InvalidSpec(f"Неизвестная модель: {self.model}")
        if self.acceleration not in ("normal", "sla"):
            raise InvalidSpec(f"Неизвестное ускорение: {self.acceleration}")
        if not isinstance(self.result_policy, ResultPolicy):
            raise InvalidSpec("Политика результата должна быть ResultPolicy")
        for ref in self.inputs:
            if not isinstance(ref, InputRef):
                raise InvalidSpec("Входные файлы должны быть InputRef")

    # ---------- the one place the video request is turned into a spec ----------

    @classmethod
    def video(cls, *, prompt, aspect, quality, seconds, seed, width, height, models=None, loras=(),
              inputs=(), model="fast", acceleration="normal", result_policy=ResultPolicy.LIBRARY,
              source="ui", sub="", max_seconds=MAX_SECONDS, extras=None):
        """A video job. The duration is clamped here once, for the window and for the agent alike."""
        try:
            length = float(seconds)
        except (TypeError, ValueError):
            raise InvalidSpec("Длительность должна быть числом")
        if length != length:   # NaN
            raise InvalidSpec("Длительность должна быть числом")
        length = round(min(max_seconds, max(MIN_SECONDS, length)), 2)
        return cls(mode="video", prompt=str(prompt or "").strip(), seed=new_seed() if seed is None else int(seed),
                   seconds=length, width=int(width), height=int(height), aspect=str(aspect or ""),
                   quality=str(quality or ""), model=model, acceleration=acceleration,
                   models=dict(models or {}), loras=tuple((str(n), float(w)) for n, w in loras),
                   inputs=tuple(inputs), result_policy=ResultPolicy.of(result_policy), source=source,
                   sub=sub, extras=dict(extras or {}))

    # ---------- serialization ----------

    def params(self) -> dict:
        """The comparable part: two specs with equal params describe the same generation."""
        return {
            "mode": self.mode,
            "prompt": self.prompt,
            "seed": self.seed,
            "seconds": self.seconds,
            "width": self.width,
            "height": self.height,
            "aspect": self.aspect,
            "quality": self.quality,
            "model": self.model,
            "acceleration": self.acceleration,
            "models": dict(sorted(self.models.items())),
            "loras": [list(pair) for pair in self.loras],
            "inputs": [ref.to_dict() for ref in self.inputs],
            "result_policy": self.result_policy.value,
            "extras": dict(sorted(self.extras.items())),
        }

    def to_dict(self) -> dict:
        """Serializable form with a schema version; `source` and `sub` are display only."""
        return {"schema_version": SCHEMA_VERSION, **self.params(), "source": self.source, "sub": self.sub}

    @classmethod
    def from_dict(cls, data: Mapping) -> "JobSpec":
        version = data.get("schema_version", 0)
        if version > SCHEMA_VERSION:
            raise SchemaVersionError(f"Задание записано более новой версией ({version} > {SCHEMA_VERSION})")
        return cls(
            mode=data.get("mode", ""), prompt=data.get("prompt", ""), seed=int(data.get("seed", 0)),
            seconds=float(data.get("seconds", MIN_SECONDS)), width=int(data.get("width", 0)),
            height=int(data.get("height", 0)), aspect=data.get("aspect", ""), quality=data.get("quality", ""),
            model=data.get("model", "fast"), acceleration=data.get("acceleration", "normal"),
            models=dict(data.get("models") or {}),
            loras=tuple((str(n), float(w)) for n, w in (data.get("loras") or ())),
            inputs=tuple(InputRef.from_dict(x) for x in (data.get("inputs") or ())),
            result_policy=ResultPolicy.of(data.get("result_policy", "library")),
            source=data.get("source", "ui"), sub=data.get("sub", ""), extras=dict(data.get("extras") or {}))


@dataclass
class JobRecord:
    """One job as the app knows it: identity, engine identity, state and version."""

    job_id: str
    spec: JobSpec | None = None
    state: JobState = JobState.CREATED
    version: int = 1
    backend: str = ""              # "comfy" | "cloud"
    backend_id: str = ""           # which instance: url or cloud name, so a job is never sent to another
    backend_job_id: str | None = None
    prompt_id: str | None = None   # the public id
    created_at: float = 0.0
    updated_at: float = 0.0
    error: str | None = None
    cancel_requested: bool = False
    finalize_attempts: int = 0
    progress: dict = field(default_factory=dict)   # stage/step/max/scene: not a business event
    results: list = field(default_factory=list)    # MediaRecord

    # ---------- state ----------

    def can(self, target) -> bool:
        target = JobState(target)
        return target == self.state or target in TRANSITIONS[self.state]

    def apply(self, target, *, now=None, error=None) -> bool:
        """Move to `target`, or refuse. Returns True when the state actually changed.

        A terminal state never changes again: a late `execution_success` for a cancelled job must not
        turn it into a success, and it must not raise into the websocket loop either — the caller
        checks `can()` first or catches `InvalidTransition`.
        """
        target = JobState(target)
        if target == self.state:
            return False
        if self.state.terminal or target not in TRANSITIONS[self.state]:
            raise InvalidTransition(f"Нельзя перевести задание из {self.state.value} в {target.value}")
        self.state = target
        self.version += 1
        self.updated_at = float(now if now is not None else self.updated_at)
        if target is JobState.FAILED:
            self.error = str(error or self.error or "задание не выполнено")
        elif target is JobState.SUCCEEDED:
            self.error = None
            self.cancel_requested = False
        return True

    def request_cancel(self, now=None) -> bool:
        """Cancelling is a request: the state changes only when the engine confirms it."""
        if self.state.terminal or self.state is JobState.FINALIZING:
            raise JobConflict(f"Задание уже нельзя отменить: {self.state.value}")
        if self.cancel_requested:
            return False
        self.cancel_requested = True
        self.updated_at = float(now if now is not None else self.updated_at)
        return True

    def cancel_confirmed(self, now=None) -> bool:
        return self.apply(JobState.CANCELED, now=now)

    def fail(self, message, now=None) -> bool:
        return self.apply(JobState.FAILED, now=now, error=message)

    def succeed(self, now=None) -> bool:
        return self.apply(JobState.SUCCEEDED, now=now)

    def set_progress(self, **fields) -> None:
        """Progress is not a state change: no version bump, no business event."""
        self.progress.update(fields)

    def add_result(self, media) -> None:
        self.results.append(media)

    # ---------- serialization ----------

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "job_id": self.job_id,
            "spec": self.spec.to_dict() if self.spec else None,
            "state": self.state.value,
            "version": self.version,
            "backend": self.backend,
            "backend_id": self.backend_id,
            "backend_job_id": self.backend_job_id,
            "prompt_id": self.prompt_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "cancel_requested": self.cancel_requested,
            "finalize_attempts": self.finalize_attempts,
            "progress": dict(self.progress),
            "results": [m.to_dict() for m in self.results],
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "JobRecord":
        version = data.get("schema_version", 0)
        if version > SCHEMA_VERSION:
            raise SchemaVersionError(f"Задание записано более новой версией ({version} > {SCHEMA_VERSION})")
        from domain.media import MediaRecord   # local: media does not need jobs, jobs needs media

        return cls(
            job_id=str(data.get("job_id") or new_job_id()),
            spec=JobSpec.from_dict(data["spec"]) if data.get("spec") else None,
            state=parse_state(data.get("state", JobState.CREATED.value)),
            version=int(data.get("version", 1)),
            backend=str(data.get("backend") or ""), backend_id=str(data.get("backend_id") or ""),
            backend_job_id=data.get("backend_job_id"), prompt_id=data.get("prompt_id"),
            created_at=float(data.get("created_at") or 0.0), updated_at=float(data.get("updated_at") or 0.0),
            error=data.get("error"), cancel_requested=bool(data.get("cancel_requested")),
            finalize_attempts=int(data.get("finalize_attempts") or 0),
            progress=dict(data.get("progress") or {}),
            results=[MediaRecord.from_dict(m) for m in (data.get("results") or ())],
        )
