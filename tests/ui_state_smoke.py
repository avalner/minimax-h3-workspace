#!/usr/bin/env python3
"""Runs the `state.js` and `lifecycle.js` checks under Node (browser classic scripts, not Python).

    python tests/ui_state_smoke.py

The JavaScript checks live next to this file (`ui_state_test.js`, `ui_lifecycle_test.js`): one owner
for the job records, one publication per result, the snapshot reconciled from outcomes; and one
owner per mode's listeners/timers/frames/object URLs, all given back on dispose. Node is the only
interpreter that can load the browser files as they are; without Node the check exits 3 (skipped).

Exit code: 0 all checks passed, 1 something failed, 3 cannot run here.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = [ROOT / "tests" / "ui_state_test.js", ROOT / "tests" / "ui_lifecycle_test.js"]
SKIP = 3


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    node = shutil.which("node")
    if not node:
        print("нет node в PATH: проверки state.js и lifecycle.js пропущены")
        return SKIP
    failed = 0
    for test in TESTS:
        done = subprocess.run([node, str(test)], cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
        print(done.stdout.rstrip())
        if done.returncode != 0:
            failed += 1
            print(done.stderr.rstrip())
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
