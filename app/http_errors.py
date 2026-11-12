"""One place where the application's refusals become HTTP — for both faces of the program.

The window and the agent API speak different dialects, and they must keep speaking them:

- the window expects `{"error": "текст"}` with a plain status (its JS shows the text);
- the agent expects `{"ok": false, "error": {"code", "message", "hint"}}` and a stable code.

The statuses and codes are the contract (`docs/agent-api.md`, `tests/agent_api_smoke.py`), so they
live in one table instead of being repeated by hand in every handler. A new domain error gets one row
here and both faces answer consistently.

Not a package named `http`: `app/` is on `sys.path`, and a package `http` would shadow the standard
library module that `urllib.request` needs (`http.client`) for every program that imports from here.
"""

from __future__ import annotations

from domain.errors import (DomainError, InvalidSpec, InvalidTransition, JobConflict, JobNotFound,
                           SchemaVersionError)

#: domain error -> (HTTP status, code of the agent API)
AGENT = (
    (InvalidSpec, 400, "INVALID_PARAM"),
    (JobConflict, 409, None),            # JobConflict carries its own code (IDEMPOTENCY_CONFLICT, ...)
    (JobNotFound, 404, "JOB_NOT_FOUND"),
    (InvalidTransition, 409, "JOB_CONFLICT"),
    (SchemaVersionError, 500, "SCHEMA_VERSION"),
)

#: domain error -> HTTP status of the window's own API (the body is `{"error": message}`)
WINDOW = (
    (InvalidSpec, 400),
    (JobConflict, 409),
    (JobNotFound, 404),
    (InvalidTransition, 409),
    (SchemaVersionError, 500),
)


def agent_error(error) -> tuple:
    """(status, code, message, hint) for the agent API. An unknown error is a 400 with its own code."""
    for kind, status, code in AGENT:
        if isinstance(error, kind):
            return status, code or getattr(error, "code", "ERROR"), str(error), getattr(error, "hint", None)
    return 400, getattr(error, "code", "ERROR"), str(error), getattr(error, "hint", None)


def window_error(error) -> tuple:
    """(message, status) for the window's own API; the caller answers `{"error": message}`."""
    for kind, status in WINDOW:
        if isinstance(error, kind):
            return str(error), status
    return str(error), 500


__all__ = ["agent_error", "window_error", "DomainError"]
