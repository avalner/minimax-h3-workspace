"""What a job was made with, for the window that wants to repeat it.

A job record carries its own parameters (`JobSpec.model`, `JobSpec.acceleration`), and the window gets
them as plain fields in `meta`. Records made before these fields existed have only the display caption
(`"… · Fused Turbo"`, `"… · быстрый черновик"`): this module is the one place that reads the model out
of it, and it never pretends to know more than it does:

    params_source = "record"   the job itself has the fields — nothing was guessed
    params_source = "legacy"   the caption of an older record said which model it was
    params_source = "unknown"  nothing was saved: the window shows the default and says so

The window reads `model`, `acceleration` and `params_source`; it does not parse captions any more.
"""

from __future__ import annotations

import re

FUSED = re.compile(r"fused turbo", re.I)
DRAFT_ACCELERATION = re.compile(r"быстрый черновик", re.I)

MODELS = ("fast", "fused")
ACCELERATIONS = ("normal", "sla")


def for_ui(meta) -> dict:
    """A copy of a job's meta with explicit `model`/`acceleration` and where they came from."""
    out = dict(meta or {})
    model = str(out.get("model") or "")
    acceleration = str(out.get("acceleration") or "")
    if model in MODELS:
        out["model"] = model
        out["acceleration"] = acceleration if acceleration in ACCELERATIONS else "normal"
        out["params_source"] = "record"
        return out
    caption = str(out.get("sub") or "")
    if FUSED.search(caption):
        out["model"], out["acceleration"] = "fused", "normal"
    elif DRAFT_ACCELERATION.search(caption):
        out["model"], out["acceleration"] = "fast", "sla"
    else:
        out["model"], out["acceleration"] = "fast", "normal"
    out["params_source"] = "legacy" if caption.strip() else "unknown"
    return out


__all__ = ["for_ui", "MODELS", "ACCELERATIONS"]
