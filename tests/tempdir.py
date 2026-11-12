"""A temporary folder that the running program can actually write into.

`tempfile.mkdtemp` creates its folder with mode 0700. On Windows such a folder gets an ACL that does
not inherit from its parent, so under a confined token — a sandbox that grants write access through an
inheritable entry — a program cannot create anything inside the folder it has just made, and a check
fails for a reason that has nothing to do with the app. A plain `mkdir` inherits the rights of its
parent as a program's own folders normally do.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def make_temp_dir(prefix, base=None):
    """Create and return a new empty folder with the rights inherited from `base`."""
    folder_base = Path(base) if base else Path(tempfile.gettempdir())
    folder_base.mkdir(parents=True, exist_ok=True)
    for n in range(10000):
        folder = folder_base / f"{prefix}{os.getpid()}-{n}"
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            continue
    raise RuntimeError("cannot create a temporary folder")
