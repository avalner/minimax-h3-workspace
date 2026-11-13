"""A project: the workspace everything the user makes belongs to.

A project is a label, not a sandbox: one results folder, one database, one queue. Every result — in
any mode, in the Library and among the drafts — carries the id of the project it was made in, so the
window can show one work at a time without hiding the rest: the Library keeps every item and a
filter chooses what is shown.

The default project always exists (migration 0003). A user who never creates one works in it, and the
results of older versions move there. Nothing here imports aiohttp or the server: the rules can be
tested on their own.
"""

from __future__ import annotations

import uuid

DEFAULT_PROJECT_ID = "main"
DEFAULT_PROJECT_NAME = "Основное"
MAX_NAME = 60


def new_project_id() -> str:
    """An opaque id: the name may be edited, references to the project must not break."""
    return "p" + uuid.uuid4().hex[:10]


def clean_name(value) -> str:
    """A name as the user typed it, without the mess: one line, single spaces, bounded length."""
    return " ".join(str(value or "").split())[:MAX_NAME]


__all__ = ["DEFAULT_PROJECT_ID", "DEFAULT_PROJECT_NAME", "MAX_NAME", "new_project_id", "clean_name"]
