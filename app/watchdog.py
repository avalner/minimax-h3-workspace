"""Is the engine stuck or only slow? While a generation runs the window sees one thing: a percentage.

That percentage stops moving for two very different reasons. A heavy step (or loading a 22 GB model
from disk) looks exactly like a ComfyUI that has stopped working, and only the video card tells them
apart: real work warms it up and draws power. On an RTX 4090 one sampling step is ~100 % load,
65-80 °C and 250-450 W; a deadlocked engine leaves the card at 35-40 °C and 30-40 W. A card that
reports a load but stays cold is not computing at all - that is a driver or device fault.

So the rule is always "no steps for a long time AND the card says ...":

  offline   the websocket to ComfyUI has been dead for a minute while a job is running - the engine
            is not answering any more (this is what a hang looks like from outside);
  lost      nvidia-smi cannot read the card any more - the driver dropped it, ComfyUI cannot recover;
  cold      load on the card but cold and at idle power for minutes - nothing is being computed;
  deadlock  no steps and the card is idle for a long time - the engine waits for something forever.

A busy card is never restarted, however long a step takes: better a slow render than a broken one.
This module only decides. Starting, stopping and telling the user is the server's job.
"""

import collections
import time

# one reading every PERIOD seconds (the server sets the pace)
PERIOD = 10.0

BUSY_UTIL = 30.0     # % of the card that counts as "the card is computing"; below it the card idles
COLD_TEMP = 50.0     # °C - under a real load a card passes this within a minute
COLD_POWER = 0.30    # share of the power limit; a 450 W card idles near 35 W and works near 300

OFFLINE_STALL = 90.0    # s without steps while ComfyUI does not answer at all
NODATA_STALL = 180.0    # s without steps while nvidia-smi cannot read the card
COLD_STALL = 240.0      # s without steps while the card pretends to work but stays cold
IDLE_STALL = 900.0      # s without steps while the card is idle (loading 22 GB takes less than this)
COOLDOWN = 600.0        # s before a second restart: a job that hangs twice must not loop forever

# what one reading says about the card
NODATA = "nodata"    # nvidia-smi failed or returned no temperature
IDLE = "idle"        # the card is not computing
LOAD = "load"        # the card is computing and warm
COLD = "cold"        # the card claims a load but stays cold and at idle power


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def classify(gpu):
    """What the card is doing, from one reading of `monitor.Monitor.gpu()` (None when it is not there)."""
    if not gpu:
        return NODATA
    util, temp = _num(gpu.get("util")), _num(gpu.get("temp"))
    if util is None or temp is None:
        return NODATA
    if util < BUSY_UTIL:
        return IDLE
    power, limit = _num(gpu.get("power")), _num(gpu.get("power_limit"))
    if temp <= COLD_TEMP and (power is None or not limit or power <= COLD_POWER * limit):
        return COLD
    return LOAD


def _w(value, unit=""):
    n = _num(value)
    return "—" if n is None else f"{n:.0f}{unit}"


class GpuWatchdog:
    """Remembers when the job last moved and what the card was doing, and says when that is a hang.

    `clock` is injectable so the whole thing can be replayed in a test without a video card or a wait.
    """

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.samples = collections.deque(maxlen=60)  # the last minutes, for the report
        self.reset()

    # ---------- what the server tells it ----------

    def reset(self):
        """A new job (or a freshly started engine): the clock starts now, nothing is stuck yet."""
        self.progress_at = self.clock()
        self.samples.clear()
        self.state = None
        self.same = 0           # how many readings in a row said the same thing
        self.fired = None       # the verdict this watchdog last acted on
        self.hold_until = 0.0   # no second restart before this moment

    def paused(self):
        """Nothing is running: no stall can exist, and an old verdict is no longer interesting."""
        self.progress_at = self.clock()
        self.samples.clear()
        self.state, self.same = None, 0

    def note_progress(self, now=None):
        """The job moved (a stage, a step, a preview): whatever was stuck is not stuck any more."""
        self.progress_at = self.clock() if now is None else now
        self.same = 0

    def stall(self, now=None):
        """Seconds since the job last showed any sign of life."""
        return max(0.0, (self.clock() if now is None else now) - self.progress_at)

    # ---------- the decision ----------

    def observe(self, gpu, busy, online=True, now=None):
        """One reading. Returns a verdict dict when ComfyUI looks stuck, otherwise None.

        `busy` - a job of ours is running (a queued one proves nothing), `online` - the websocket to
        ComfyUI is up. `now` is in the same units as `clock`.
        """
        now = self.clock() if now is None else now
        if not busy:
            self.paused()
            return None

        state = classify(gpu)
        self.same = self.same + 1 if state == self.state else 1
        self.state = state
        self.samples.append({"at": now, "state": state, "gpu": gpu or {}})

        stall = now - self.progress_at
        if now < self.hold_until:
            return None
        if not online and stall >= OFFLINE_STALL:
            return self._fire("offline", now, gpu, stall, "движок не отвечает")
        if state == NODATA and self.same * PERIOD >= NODATA_STALL and stall >= NODATA_STALL:
            return self._fire("lost", now, gpu, stall, "nvidia-smi не видит видеокарту")
        if state == COLD and self.same * PERIOD >= COLD_STALL and stall >= COLD_STALL:
            return self._fire("cold", now, gpu, stall, "загрузка на карте есть, а счёт не идёт")
        if state == IDLE and self.same * PERIOD >= IDLE_STALL and stall >= IDLE_STALL:
            return self._fire("deadlock", now, gpu, stall, "карта простаивает, а задание не двигается")
        return None

    def _fire(self, kind, now, gpu, stall, why):
        self.fired = {"kind": kind, "at": now, "stall": round(stall), "gpu": dict(gpu or {}), "why": why}
        self.hold_until = now + COOLDOWN
        return self.fired


def _minutes(seconds):
    m = int(seconds // 60)
    return f"{m} мин" if m else f"{int(seconds)} с"


def verdict_text(v):
    """What the window tells the user about this verdict (a notice, one or two lines)."""
    kind = v.get("kind")
    g = v.get("gpu") or {}
    mins = _minutes(v.get("stall") or 0)
    card = f'{_w(g.get("util"), " %")} загрузки, {_w(g.get("temp"), " °C")}, {_w(g.get("power"))} Вт'
    if kind == "offline":
        return (f"ComfyUI не отвечает уже {mins}, хотя задание идёт: движок завис. "
                "Перезапускаю его — незаконченное задание придётся запустить заново.")
    if kind == "lost":
        return (f"Видеокарта перестала отвечать ({mins}): похоже, драйвер потерял её. Перезапускаю ComfyUI. "
                "Если это повторится, перезагрузите компьютер и обновите драйвер NVIDIA.")
    if kind == "cold":
        return (f"Видеокарта показывает {card}, но счёт не идёт уже {mins}: работа до неё не доходит. "
                "Перезапускаю ComfyUI.")
    return (f"Задание не двигается {mins}, а видеокарта всё это время простаивала ({card}). "
            "Движок завис — перезапускаю ComfyUI.")


def describe(v):
    """One line for the log and for the report: the verdict, the numbers and the last minutes of readings."""
    if not v:
        return "нет"
    g = v.get("gpu") or {}
    return (f'{v.get("kind")} ({v.get("why")}): загрузка {_w(g.get("util"), " %")}, '
            f'температура {_w(g.get("temp"), " °C")}, питание {_w(g.get("power"))} из {_w(g.get("power_limit"))} Вт, '
            f'без шагов {_minutes(v.get("stall") or 0)}')
