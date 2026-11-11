"""The decision of the video card watch, replayed without a video card and without waiting.

Every case here is one that has actually been seen on an RTX 4090: a hung engine that leaves the card
cold, a slow step that keeps it hot, a driver that stops answering, and a job that is simply loading a
22 GB model. The thresholds are seconds; the test feeds readings through a fake clock.

    python tests/gpu_watchdog_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import watchdog

STEP = watchdog.PERIOD

COLD_IDLE = {"name": "RTX 4090", "util": 3.0, "temp": 39.0, "power": 36.0, "power_limit": 450.0}
COLD_BUSY = {"name": "RTX 4090", "util": 99.0, "temp": 39.0, "power": 36.0, "power_limit": 450.0}
HOT = {"name": "RTX 4090", "util": 99.0, "temp": 71.0, "power": 340.0, "power_limit": 450.0}
LOADING = {"name": "RTX 4090", "util": 0.0, "temp": 34.0, "power": 24.0, "power_limit": 450.0}


class Clock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def tick(self, seconds=STEP):
        self.now += seconds
        return self.now


def run(reading, samples, *, busy=True, online=True, progress_every=None, restart_ok=False):
    """Feed `samples` readings one period apart; return [(seconds, verdict), ...].

    `restart_ok` is what the server does after a successful restart: the watch starts from zero, so one
    hang gives one verdict. Without it the watch keeps counting and may ask again after the cooldown.
    """
    clock = Clock()
    wd = watchdog.GpuWatchdog(clock=clock)
    wd.reset()
    out = []
    for i in range(1, samples + 1):
        clock.tick()
        if progress_every and i % progress_every == 0:
            wd.note_progress()
        v = wd.observe(reading, busy=busy, online=online)
        if v:
            out.append((i * STEP, v))
            if restart_ok:
                wd.reset()
    return out


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def cold_card_under_load_is_a_hang():
    """Load on the card, 39 °C, 36 W, no steps: nothing is being computed."""
    fired = run(COLD_BUSY, 40, restart_ok=True)
    assert len(fired) == 1, f"ожидалось одно срабатывание, было {len(fired)}"
    at, v = fired[0]
    assert v["kind"] == "cold", v["kind"]
    assert watchdog.COLD_STALL <= at < watchdog.COLD_STALL + STEP + 1, at
    assert v["gpu"]["temp"] == 39.0


@case
def slow_but_hot_card_is_never_touched():
    """A 100 % load at 71 °C and 340 W is a render doing its job, however long the step takes."""
    assert run(HOT, 300) == []


@case
def steps_that_move_mean_nothing_is_stuck():
    """The same cold card, but the job reports a step every half minute: it is alive."""
    assert run(COLD_BUSY, 120, progress_every=3) == []
    assert run(COLD_IDLE, 300, progress_every=5) == []


@case
def idle_card_for_a_quarter_of_an_hour_is_a_deadlock():
    """No steps and the card idles for 15 minutes: no model load takes that long."""
    fired = run(COLD_IDLE, 300, restart_ok=True)
    assert fired, "движок так и остался висеть незамеченным"
    at, v = fired[0]
    assert v["kind"] == "deadlock", v["kind"]
    assert at >= watchdog.IDLE_STALL, at
    for (a, _), (b, _) in zip(fired, fired[1:]):  # a job that hangs again waits a full idle stall
        assert b - a >= watchdog.IDLE_STALL, (a, b)


@case
def loading_a_model_is_not_a_deadlock():
    """Disk-bound loading looks idle, but the first minutes of it must not count as a hang."""
    assert run(LOADING, 60) == []


@case
def a_card_the_driver_dropped_is_reported():
    """nvidia-smi answers nothing at all (None), or answers without temperature."""
    for reading in (None, {"util": None, "temp": None}, {"util": 90, "temp": None, "power": None}):
        fired = run(reading, 40)
        assert fired and fired[0][1]["kind"] == "lost", (reading, fired)


@case
def an_engine_that_stopped_answering_is_restarted_sooner():
    """The websocket is gone while a job runs: ComfyUI does not answer, whatever the card shows."""
    fired = run(HOT, 40, online=False)
    assert fired and fired[0][1]["kind"] == "offline", fired
    assert fired[0][0] >= watchdog.OFFLINE_STALL, fired[0][0]


@case
def nothing_runs_no_verdicts():
    """A cold card with no job is just an idle computer."""
    assert run(COLD_BUSY, 300, busy=False) == []
    assert run(None, 300, busy=False) == []


@case
def a_second_restart_waits_for_the_cooldown():
    """If the restart did not help, the watch waits a full cooldown before asking again (no loop)."""
    fired = run(COLD_IDLE, 400)  # 4000 s, no restart ever happens
    assert len(fired) >= 2, len(fired)
    for (a, _), (b, _) in zip(fired, fired[1:]):
        assert b - a >= watchdog.COOLDOWN, (a, b)


@case
def classify_reads_one_reading():
    assert watchdog.classify(None) == watchdog.NODATA
    assert watchdog.classify({"util": 2.0, "temp": 39.0, "power": 36.0, "power_limit": 450.0}) == watchdog.IDLE
    assert watchdog.classify(HOT) == watchdog.LOAD
    assert watchdog.classify(COLD_BUSY) == watchdog.COLD
    # one signal is not enough: warm but at idle power is a card that is throttled or just started
    assert watchdog.classify({"util": 95.0, "temp": 61.0, "power": 40.0, "power_limit": 450.0}) == watchdog.LOAD
    assert watchdog.classify({"util": 95.0, "temp": 40.0, "power": 400.0, "power_limit": 450.0}) == watchdog.LOAD


@case
def every_verdict_has_words_for_the_user():
    for kind in ("offline", "lost", "cold", "deadlock"):
        v = {"kind": kind, "stall": 300, "gpu": COLD_BUSY, "why": "test"}
        text = watchdog.verdict_text(v)
        assert 40 < len(text) < 400, text
        assert "перезапуска" in text.lower(), text
        assert watchdog.describe(v).startswith(kind), watchdog.describe(v)


def main():
    try:  # the console of a Russian Windows is cp1251 and would lose the words below
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    bad = 0
    for fn in CASES:
        try:
            fn()
            print(f"  ок   {fn.__name__}")
        except AssertionError as e:
            bad += 1
            print(f"  ПЛОХО {fn.__name__}: {e}")
    print(f"\n{len(CASES) - bad} из {len(CASES)} проверок прошли")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
