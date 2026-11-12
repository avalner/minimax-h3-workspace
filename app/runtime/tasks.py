"""Every background coroutine of the program, in one place.

A `create_task` in a handler is fire and forget: nothing keeps a reference (so the task may be
garbage collected), nothing sees its exception (asyncio prints «Task exception was never retrieved»
later, at an unrelated moment), and nothing can wait for it when the program closes. The supervisor
does those three things:

- `spawn(name, coro)` keeps the reference and remembers the name;
- a finished task's error is written into the diagnostics and reported once — a cancellation is not
  an error;
- after `close()` no new task starts at all (a handler that is still running in the last moment
  gets `None` instead of a task that would touch a closed session), and `shutdown()` cancels what is
  left and waits for it within a budget.

`shutdown()` returns the names that did not finish in time instead of hiding them: an operation that
was cut by the budget must leave a state the next start can recover from, and the report has to say
which one it was.

A task is expected to finish by itself (a download, a poll loop); the owner of the resource it uses
is named at the `spawn` site. Nested `create_task` calls inside a task are the task's own business —
but a task that starts one must cancel and await it when it is cancelled, or the child outlives it
(see the LoRA download and `interrupt_watch`).
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time


class TaskSupervisor:
    """The background coroutines of one application: start, watch, stop."""

    def __init__(self, *, note_error=lambda text: None, clock=time.monotonic):
        self._tasks = {}      # name -> Task
        self._errors = []     # the last failures, for the status and the report
        self.refused = []     # names that were asked for after `close()`
        self._closing = False
        self._note_error = note_error
        self._clock = clock

    # ---------- reading ----------

    @property
    def closing(self) -> bool:
        return self._closing

    def get(self, name):
        return self._tasks.get(name)

    def names(self) -> list:
        return sorted(self._tasks)

    def running(self) -> dict:
        return {name: task for name, task in self._tasks.items() if not task.done()}

    def errors(self) -> list:
        return list(self._errors)

    # ---------- starting ----------

    def spawn(self, name, coro, *, replace=False):
        """Start `coro` under `name`; None when the program is already closing.

        `replace=True` cancels a task with the same name that is still running (a new watch of the
        same job, for instance) instead of leaving two of them.
        """
        if self._closing:
            with contextlib.suppress(AttributeError):
                coro.close()   # a coroutine that is never awaited would warn at the next collection
            self.refused.append(name)
            return None
        old = self._tasks.get(name)
        if old is not None and not old.done():
            if not replace:
                with contextlib.suppress(AttributeError):
                    coro.close()   # the same work is already running: this coroutine is not used
                return old   # the same work is already running: one task, one owner
            old.cancel()
        task = asyncio.ensure_future(coro)
        try:
            task.set_name(name)
        except AttributeError:   # pragma: no cover - Python without Task.set_name
            pass
        self._tasks[name] = task
        task.add_done_callback(lambda done, key=name: self._finished(key, done))
        return task

    def _finished(self, name, task):
        if self._tasks.get(name) is task:   # a replaced task finishing later must not remove the new one
            self._tasks.pop(name, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is None:
            return
        text = f"Фоновая задача {name}: {type(error).__name__}: {error}"
        self._errors = (self._errors + [text])[-20:]
        self._note_error(text)
        try:
            print(text, file=sys.stderr)
        except Exception:
            pass

    # ---------- stopping ----------

    def close(self):
        """Stop accepting new tasks. Called first in the shutdown, before anything is cancelled."""
        self._closing = True

    def cancel(self, name) -> bool:
        task = self._tasks.get(name)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def cancel_all(self):
        for task in list(self._tasks.values()):
            if not task.done():
                task.cancel()

    async def shutdown(self, timeout=10.0) -> list:
        """Cancel everything and wait `timeout` seconds. Returns the names that are still running.

        The tasks get `timeout` together (not one budget each), so the caller can pass what is left
        of the whole shutdown budget. What did not finish is returned, with its error if it had one,
        and it is cancelled once more: the loop is about to close and a task that keeps running
        would either be destroyed with a warning or touch a closed session.
        """
        self.close()
        pending = [task for task in self._tasks.values() if not task.done()]
        for task in pending:
            task.cancel()
        unfinished = []
        if pending:
            _, still = await asyncio.wait(pending, timeout=max(0.0, float(timeout)))
            unfinished = [task.get_name() for task in still]
            for task in still:
                task.cancel()
        return sorted(unfinished)

    async def wait_idle(self, timeout=10.0) -> list:
        """Wait for the tasks that are still running, without cancelling them. Names of the rest."""
        pending = [task for task in self._tasks.values() if not task.done()]
        if not pending:
            return []
        _, still = await asyncio.wait(pending, timeout=max(0.0, float(timeout)))
        return sorted(task.get_name() for task in still)


__all__ = ["TaskSupervisor"]
