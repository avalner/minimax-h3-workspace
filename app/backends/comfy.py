"""ComfyUI as a generation backend: the video recipe, the queue, the history and the files.

Nothing here knows about the window, the database or the agent API. The two things that do come from
outside are injected:

- `client(method, path, **kwargs) -> (status, payload)` — the HTTP call, so the backend can be checked
  against a fake engine and so the key of a rented server stays where the app configures it;
- `models()` and `loras(mode)` — snapshots of what this engine sees and of the user's own LoRAs;
- `view(path, params)` — an async context manager yielding `(status, content_length, chunks)`, so an
  output file is streamed and never read into memory as a whole.

`BackendUnavailable` means "I could not ask the engine"; that is deliberately not the same as a job
that failed, and the job service must never resend a job on that evidence.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Callable, Mapping

import workflows as wf
from backends.base import (BackendError, BackendUnavailable, Capabilities, InspectResult, OutputStream,
                           SubmitRejected, SubmitResult)
from domain.jobs import JobState


def error_text(payload) -> str:
    """The engine's refusal in words the user can act on (its own node errors, not a stack trace)."""
    if not isinstance(payload, dict):
        return "ComfyUI отклонил задание"
    err = payload.get("error") or {}
    parts = [err.get("message", "") + (": " + err.get("details", "") if err.get("details") else "")]
    for node_id, node_error in (payload.get("node_errors") or {}).items():
        for e in node_error.get("errors", []):
            parts.append(f'{node_error.get("class_type", node_id)}: {e.get("message")} {e.get("details", "")}'.strip())
    return "\n".join(p for p in parts if p) or "ComfyUI отклонил задание"


def _input(spec, key):
    """The name inside ComfyUI's input folder for a machine-purpose key of the spec."""
    for ref in spec.inputs:
        if ref.key == key and ref.comfy:
            return ref.comfy
    return None


class ComfyBackend:
    """A local or rented ComfyUI behind the backend protocol."""

    name = "comfy"
    capabilities = Capabilities(can_cancel_queued=True, can_cancel_running=True)

    def __init__(self, *, client, url, client_id, models: Callable[[], Mapping[str, str]],
                 loras: Callable[[str], list] = lambda mode: (), view=None):
        self.client = client
        self.url = url
        self.client_id = client_id
        self.models = models
        self.loras = loras
        self.view = view

    async def submit(self, spec, *, model_names=None):
        if spec.mode != "video":
            raise SubmitRejected(f"Этот движок пока принимает только видео, а не «{spec.mode}»")
        # the snapshot taken when the request was described wins: a retry must build the same graph,
        # not the one the engine happens to have today
        models = dict(model_names or spec.models or self.models())
        if spec.model == "fused" and not models.get("fused_unet"):
            raise SubmitRejected("Для модели Fused Turbo нужен её файл (около 21 ГБ)")
        if spec.acceleration == "sla" and not models.get("sla_lora"):
            raise SubmitRejected("Для быстрого режима нужен файл Turbo-SLA")
        first = _input(spec, "first_frame")
        last = _input(spec, "last_frame")
        graph, stages = wf.video_graph(models, spec.prompt, spec.width, spec.height, spec.seconds, spec.seed,
                                       first, last, tuple(spec.loras),
                                       models.get("sla_lora") if spec.acceleration == "sla" else None,
                                       models.get("fused_unet") if spec.model == "fused" else None)
        try:
            status, payload = await self.client("POST", "/prompt", json={
                "prompt": graph, "client_id": self.client_id, "extra_data": {"preview_method": "auto"}})
        except BackendUnavailable:
            raise
        if status != 200:
            raise SubmitRejected(error_text(payload))
        if not isinstance(payload, dict) or not payload.get("prompt_id"):
            raise SubmitRejected("ComfyUI не вернул идентификатор задания (история и очередь пусты)")
        return SubmitResult(backend_job_id=str(payload["prompt_id"]), graph=graph, stages=stages,
                            extra={"number": payload.get("number")})

    async def inspect(self, record):
        pid = record.backend_job_id
        if not pid:
            return InspectResult(state="unknown")
        _, queue = await self.client("GET", "/queue")
        running = {str(item[1]) for item in (queue or {}).get("queue_running", []) if len(item) > 1}
        pending = {str(item[1]) for item in (queue or {}).get("queue_pending", []) if len(item) > 1}
        if pid in running:
            return InspectResult(state="running")
        if pid in pending:
            return InspectResult(state="queued")
        _, history = await self.client("GET", f"/history/{pid}")
        entry = (history or {}).get(pid)
        if not entry:
            return InspectResult(state="unknown")
        if entry.get("status", {}).get("status_str") == "error":
            messages = entry.get("status", {}).get("messages") or []
            error = next((str(m[1].get("exception_message") or "") for m in messages
                          if isinstance(m, (list, tuple)) and len(m) > 1 and isinstance(m[1], dict)), "")
            return InspectResult(state="failed", error=error or "ComfyUI закончил задание с ошибкой")
        return InspectResult(state="finished", outputs=entry.get("outputs") or {})

    async def cancel(self, record, *, force=False):
        pid = record.backend_job_id
        if not pid:
            raise BackendError("У задания ещё нет идентификатора на движке: отменять нечего")
        if record.state is JobState.QUEUED and not force:
            status, _ = await self.client("POST", "/queue", json={"delete": [pid]})
        else:
            status, _ = await self.client("POST", "/interrupt", json={"prompt_id": pid})
        return status == 200

    async def outputs(self, record):
        if not record.backend_job_id:
            return {}
        _, history = await self.client("GET", f"/history/{record.backend_job_id}")
        entry = (history or {}).get(record.backend_job_id) or {}
        return entry.get("outputs") or {}

    @asynccontextmanager
    async def open_output(self, file_ref):
        """Stream one output file. The `.part` name, the size check and the rename belong to the
        media service; here only the engine's answer is checked."""
        if self.view is None:
            raise BackendError("этот движок не умеет отдавать файлы потоком")
        params = {"filename": file_ref["filename"], "subfolder": file_ref.get("subfolder", ""),
                  "type": file_ref.get("type", "output")}
        async with self.view("/view", params) as (status, size, chunks):
            if status != 200:
                raise BackendUnavailable(f"движок не отдал файл {file_ref.get('filename')}: HTTP {status}")
            yield OutputStream(chunks=chunks, expected_bytes=size)
