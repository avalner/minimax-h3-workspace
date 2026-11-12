"""A temporary folder that the running program can actually write into.

`tempfile.mkdtemp` creates its folder with mode 0700. On Windows such a folder gets an ACL that does
not inherit from its parent, so under a confined token — a sandbox that grants write access through an
inheritable entry — a program cannot create anything inside the folder it has just made, and a check
fails for a reason that has nothing to do with the app. A plain `mkdir` inherits the rights of its
parent as a program's own folders normally do.

The folders live under `_test/tmp` (ignored by git) rather than in the system temp folder: that one may
be unwritable, and Python then quietly falls back to the current directory — which put test data into
the repository root once already.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMP_ROOT = ROOT / "_test" / "tmp"


def make_temp_dir(prefix, base=None):
    """Create and return a new empty folder with the rights inherited from `base`."""
    folder_base = Path(base) if base else TEMP_ROOT
    folder_base.mkdir(parents=True, exist_ok=True)
    for n in range(10000):
        folder = folder_base / f"{prefix}{os.getpid()}-{n}"
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            continue
    raise RuntimeError("cannot create a temporary folder")
