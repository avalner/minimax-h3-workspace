"""What a generation backend has to be able to do, and what it answers with.

The service talks to this protocol, never to ComfyUI itself: that is what makes a fake backend in a
check possible, and what keeps the cancellation rules of a local engine apart from a cloud one.

`Capabilities` is a statement of fact, not an aspiration: a backend that cannot cancel a running job
says so, and the service then refuses the request instead of pretending.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable


class BackendError(Exception):
    """The backend could not do what was asked (and knows it)."""


class BackendUnavailable(BackendError):
    """The engine does not answer. This is not a failed job: the job is still there on its side."""


class SubmitRejected(BackendError):
    """The engine refused the job (a bad graph, a missing model). Nothing was created."""


@dataclass(frozen=True)
class Capabilities:
    can_cancel_queued: bool = True
    can_cancel_running: bool = True
    can_inspect: bool = True      # can say what happened to a job after a restart
    can_enumerate: bool = True    # can list the files a job produced
    can_download: bool = True     # can give those files


@dataclass
class SubmitResult:
    """What the engine answered: its own id for the job, and what was actually sent."""

    backend_job_id: str
    graph: dict | None = None
    stages: dict = field(default_factory=dict)
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class InspectResult:
    """What the engine says about a job of an earlier run.

    "unknown" means the engine does not remember it — it may have been restarted, or the POST never
    arrived. The caller must not send it again on that evidence alone.
    """

    state: str                    # "unknown" | "queued" | "running" | "finished" | "failed"
    outputs: dict = field(default_factory=dict)
    error: str | None = None


@runtime_checkable
class Backend(Protocol):
    """The small set of operations the job service needs. Everything else is engine-specific."""

    name: str
    capabilities: Capabilities

    async def submit(self, spec, *, model_names: Mapping[str, str] | None = None) -> SubmitResult:
        """Send the job. Raises SubmitRejected when the engine refuses it."""

    async def inspect(self, record) -> InspectResult:
        """Ask the engine what became of this job. Raises BackendUnavailable when it cannot ask."""

    async def cancel(self, record, *, force: bool = False) -> bool:
        """Ask the engine to stop the job. True when the engine accepted the request."""

    async def outputs(self, record) -> dict:
        """The files the engine reports for a finished job (its own output format)."""

    async def download(self, record, file_ref, destination) -> None:
        """Copy one of those files to `destination`."""
