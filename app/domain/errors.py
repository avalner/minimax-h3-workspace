"""What a service refuses with, in words the UI and the agent API can both show.

A domain error carries a machine-readable `code` and a human `message`; it never knows about HTTP
statuses. The HTTP layer (stage 7) decides which status goes with which code.
"""

from __future__ import annotations

__all__ = ["DomainError", "InvalidSpec", "InvalidTransition", "JobNotFound", "JobConflict", "SchemaVersionError"]


class DomainError(Exception):
    """A refusal the caller is supposed to handle and show."""

    code = "ERROR"

    def __init__(self, message, *, code=None, hint=None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        if code:
            self.code = code


class InvalidSpec(DomainError):
    """The job as described cannot be made: an empty prompt, a wrong duration, an unknown mode."""

    code = "INVALID_SPEC"


class InvalidTransition(DomainError):
    """A job cannot go from its current state to the requested one."""

    code = "INVALID_TRANSITION"


class JobConflict(DomainError):
    """The request is valid but the job is in a state that does not allow it (cancel after the end)."""

    code = "JOB_CONFLICT"


class JobNotFound(DomainError):
    """No job with this identifier is known."""

    code = "JOB_NOT_FOUND"


class SchemaVersionError(DomainError):
    """The data was written by a newer version of the app, which must not be reinterpreted."""

    code = "SCHEMA_VERSION"
