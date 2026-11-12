#!/usr/bin/env python3
"""Runs the `state.js` checks under Node (the module is a browser classic script, not Python).

    python tests/ui_state_smoke.py

The JavaScript checks live next to this file (`ui_state_test.js`): one owner for the job records,
one publication per result, the snapshot reconciled from outcomes, the page's changes only through
`update`. Node is the only interpreter that can load the browser file as it is; when Node is not
installed the check exits 3 and the runner shows it as skipped.

Exit code: 0 all checks passed, 1 something failed, 3 cannot run here.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST = ROOT / "tests" / "ui_state_test.js"
SKIP = 3


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    node = shutil.which("node")
    if not node:
        print("нет node в PATH: проверка state.js пропущена")
        return SKIP
    done = subprocess.run([node, str(TEST)], cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    print(done.stdout.rstrip())
    if done.returncode != 0:
        print(done.stderr.rstrip())
    return 0 if done.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
