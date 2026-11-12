"""Events for the open windows, without letting one window hold the other work.

Until this module, `App.broadcast` awaited `ws.send_str` for every connected window. One slow or
frozen window (a suspended laptop tab, a blocked WebSocket) therefore delayed the end of a
finalization, the next job and every other window — the database commit was already done, but the
program behaved as if the event mattered more than the work.

The rules here:

- **a fact is not an event.** `publish` first calls the recorder (`App.remember`), so a finished job
  is written down whether or not any window is open; then it queues the event. Nothing in the delivery
  path decides what is true.
- **events are queued, never awaited.** Each window has a bounded queue and its own sender task. A
  window that cannot take an event (a 5-second timeout) is dropped and reconnects; it then receives
  the snapshot (`hello`) and continues from there.
- **progress may be dropped, an outcome may not.** When a queue is full, a progress event is dropped
  (the window recomputes progress from the next one and from the snapshot); a terminal event pushes
  out the oldest item so it always gets in.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

TERMINAL = {"done", "error", "interrupted"}


def _text(payload) -> str:
    return json.dumps(payload, ensure_ascii=False)


class Subscription:
    """One window: its queue of events and the task that sends them."""

    def __init__(self, ws, size):
        self.ws = ws
        self.queue = asyncio.Queue(maxsize=int(size))
        self.task = None
        self.closed = False

    def offer(self, item, *, terminal=False) -> bool:
        """Put one item in the queue; never waits on the socket. False when the item was dropped."""
        if self.closed:
            return False
        try:
            self.queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            if not terminal:
                return False   # progress: the window will get the next one, and the snapshot on connect
            try:
                self.queue.get_nowait()   # an outcome pushes out the oldest progress event
            except asyncio.QueueEmpty:
                pass
            try:
                self.queue.put_nowait(item)
                return True
            except asyncio.QueueFull:   # pragma: no cover - one item out, one in: cannot be full
                self.closed = True
                return False


class EventHub:
    """The open windows and their delivery; the facts are the caller's business."""

    def __init__(self, *, tasks, remember=lambda payload: None, size=256, timeout=5.0,
                 note_error=lambda text: None):
        self._tasks = tasks       # TaskSupervisor: the sender tasks are stopped with everything else
        self._remember = remember
        self._size = int(size)
        self._timeout = float(timeout)
        self._note_error = note_error
        self._subs = {}           # id(subscription) -> subscription
        self._count = 0

    # ---------- a window comes and goes ----------

    def attach(self, ws, *, hello=None) -> Subscription:
        """Start sending to a window. `hello` is its first message: the state it may have missed."""
        sub = Subscription(ws, self._size)
        if hello is not None:
            sub.offer(_text(hello), terminal=True)
        self._count += 1
        self._subs[id(sub)] = sub
        sub.task = self._tasks.spawn(f"events:{self._count}", self._send(sub))
        return sub

    async def detach(self, sub):
        self._subs.pop(id(sub), None)
        sub.closed = True
        task, sub.task = sub.task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task

    async def close(self):
        """Stop every sender (the program is closing). The facts are already saved by their owner."""
        for sub in list(self._subs.values()):
            await self.detach(sub)

    def windows(self) -> int:
        return len(self._subs)

    # ---------- what the work publishes ----------

    async def publish(self, payload):
        """Write the fact down, then queue the event for every open window. Never waits on a socket."""
        self._remember(payload)
        text = _text(payload)
        terminal = payload.get("type") in TERMINAL
        for sub in list(self._subs.values()):
            sub.offer(text, terminal=terminal)

    def preview(self, image: bytes):
        """A live preview frame: high frequency, mergeable, never allowed to hold anything up."""
        for sub in list(self._subs.values()):
            sub.offer(image, terminal=False)

    # ---------- the sender ----------

    async def _send(self, sub):
        try:
            while not sub.closed:
                item = await sub.queue.get()
                send = sub.ws.send_bytes if isinstance(item, bytes) else sub.ws.send_str
                try:
                    await asyncio.wait_for(send(item), timeout=self._timeout)
                except asyncio.TimeoutError:
                    self._note_error("Окно не принимает события: соединение закрыто, окно переподключится")
                    break
                except Exception:  # noqa: BLE001 - a dead socket is not an error of the program
                    break
        except asyncio.CancelledError:
            raise
        finally:
            sub.closed = True
            with contextlib.suppress(Exception):
                await sub.ws.close()


__all__ = ["EventHub", "Subscription", "TERMINAL"]
