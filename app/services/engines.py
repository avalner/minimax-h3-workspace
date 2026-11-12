"""Who owns ComfyUI, and who may replace it.

Two engines look the same to the rest of the program: one the app installed and runs (`managed`), and
the user's own, which the app may start again from the command line ComfyUI reports (`external`).
This is the only place that starts, stops or replaces either of them, and that is what makes the
dangerous cases impossible rather than unlikely:

- **one restart at a time.** A watch, a cancel timeout and a button press can all ask at the same
  moment. The first starts the work, the others wait for it and receive its answer; two restarts
  never start two ComfyUI processes on one card.
- **the app stops only what it started.** A managed engine is a child of this program and is stopped
  with it. The user's own ComfyUI is theirs; if the app started it again after a hang, the app does
  not stop it at exit (the owner is the user, not the window).
- **the card is freed before a job, not during it.** `prepare()` is the one place that asks the
  agent's model to leave the VRAM and the previous job's models to be unloaded; it is skipped while
  other jobs are still running.

The watchdog thresholds are not here on purpose: `watchdog.py` decides *when* the engine looks hung,
this module only performs the replacement.
"""

from __future__ import annotations

import asyncio
import urllib.parse

from engine import ComfyProcess, log_tail

PICTURE_MODES = ("krea", "kreareal", "qwen", "flux")   # one big model stays loaded between pictures


class EngineManager:
    """The engine process, the one restart that may run, and the VRAM handshake."""

    def __init__(self, *, url, wait_ready, log_path, external_restart, stats=lambda: {},
                 stop_llm=None, release_vram=None, busy=lambda: False, note_error=lambda text: None,
                 clock=None):
        self.process = None       # ComfyProcess of the app's own install, when it runs it
        self.external = None      # the user's ComfyUI, when the app had to start it again
        self._url = url
        self._wait_ready = wait_ready
        self._log_path = log_path
        self._external_restart = external_restart
        self._stats = stats
        self._stop_llm = stop_llm
        self._release_vram = release_vram
        self._busy = busy
        self._note_error = note_error
        self._clock = clock
        self._restarting = None   # the Task of the restart that is running now
        self._last = None         # {"at", "ok", "why"}: what the last restart did, for the sidebar
        self._last_mode = None

    # ---------- reading ----------

    def local(self) -> bool:
        """A rented or otherwise remote ComfyUI cannot be started again from here, and its card is not ours."""
        return urllib.parse.urlparse(self._url()).hostname in (None, "", "127.0.0.1", "localhost")

    @property
    def restarting(self) -> bool:
        return self._restarting is not None and not self._restarting.done()

    @property
    def last(self):
        return self._last

    def log_tail(self, n=15):
        if self.process is not None:
            return self.process.log_tail(n)
        return log_tail(self._log_path, n)

    # ---------- the app's own process ----------

    def start_managed(self, comfy_root):
        """Start the engine of the app's own install and return the process (it owns every child)."""
        self.process = ComfyProcess(comfy_root, self._log_path)
        self.process.start()
        return self.process

    async def stop(self):
        """Stop what this program started. Called once, at the end of the shutdown.

        The user's own ComfyUI is not ours: if the app started it again after a hang, it keeps
        running (the reference stays, so the death of that process is still noticed while we run).
        """
        process, self.process = self.process, None
        if process is not None:
            await asyncio.to_thread(process.stop)   # a worker thread: terminate + wait 15 s + taskkill

    # ---------- replacing it ----------

    async def restart(self):
        """Replace a hung engine. Returns (ok, why); callers that arrive during a restart wait for it.

        The work is a task of its own, so cancelling the request that asked for it (a closed window,
        a dropped HTTP call) does not leave half a restart behind. The answer is the same for every
        caller of that one restart.
        """
        if not self.restarting:
            self._restarting = asyncio.ensure_future(self._replace())
        return await asyncio.shield(self._restarting)

    async def _replace(self):
        try:
            if not self.local():
                return self._remember(False, "это ComfyUI на другом компьютере: перезапустите его там, где он запущен")
            if self.process is not None:
                await asyncio.to_thread(self.process.restart)
            else:
                if self.external is not None and self.external.poll() is not None:
                    self.external = None   # an old copy of it is already gone
                self.external = await asyncio.to_thread(
                    self._external_restart, self._stats(), self._log_path, self._url())
            await self._wait_ready(timeout=240)
            return self._remember(True, "")
        except Exception as e:
            why = str(e) or type(e).__name__
            if self.process is None:
                tail = log_tail(self._log_path, 6)
                why = f"{why}. Журнал: {tail}" if tail else why
            return self._remember(False, why)
        finally:
            self._restarting = None

    def _remember(self, ok, why):
        self._last = {"at": self._clock() if self._clock else None, "ok": bool(ok), "why": str(why or "")}
        return bool(ok), str(why or "")

    # ---------- the VRAM handshake ----------

    async def prepare(self, mode_key):
        """Before a job: the agent's model out of VRAM, then the previous job's models freed.

        Not between pictures of the same mode: the same big models would just be loaded from disk
        again. Returns True when the models were kept (the caller may use it for its own decision).
        """
        if self._stop_llm is not None:
            await self._stop_llm()   # the agent's model and H3 do not fit in VRAM together
        same = mode_key == self._last_mode and mode_key in PICTURE_MODES
        self._last_mode = mode_key
        if not self._busy() and not same and self._release_vram is not None:
            await self._release_vram()
        return same


__all__ = ["EngineManager", "PICTURE_MODES"]
