"""The application's refusals become HTTP in one place, and the two faces keep their own dialects.

    python tests/unit/test_http_errors.py
"""

from __future__ import annotations

import unittest

import support  # noqa: F401  (puts app/ on sys.path)

from domain.errors import InvalidSpec, InvalidTransition, JobConflict, JobNotFound, SchemaVersionError
from http_errors import agent_error, window_error


class AgentDialectTest(unittest.TestCase):
    def test_an_invalid_request_is_a_400_with_the_agents_code(self):
        status, code, message, hint = agent_error(InvalidSpec("Длительность должна быть числом", hint="3–25 с"))
        self.assertEqual((status, code), (400, "INVALID_PARAM"))
        self.assertIn("Длительность", message)
        self.assertEqual(hint, "3–25 с")

    def test_a_conflict_keeps_its_own_code(self):
        status, code, message, _ = agent_error(JobConflict("Этот ключ уже использован", code="IDEMPOTENCY_CONFLICT"))
        self.assertEqual((status, code), (409, "IDEMPOTENCY_CONFLICT"))
        self.assertIn("ключ", message)

    def test_a_plain_conflict_is_a_409(self):
        self.assertEqual(agent_error(JobConflict("Нельзя отменить"))[:2], (409, "JOB_CONFLICT"))

    def test_an_unknown_job_is_a_404(self):
        self.assertEqual(agent_error(JobNotFound("нет задания"))[:2], (404, "JOB_NOT_FOUND"))

    def test_an_illegal_transition_is_a_409(self):
        self.assertEqual(agent_error(InvalidTransition("нельзя"))[:2], (409, "JOB_CONFLICT"))

    def test_a_newer_database_is_a_500(self):
        self.assertEqual(agent_error(SchemaVersionError("новее"))[:2], (500, "SCHEMA_VERSION"))


class WindowDialectTest(unittest.TestCase):
    def test_the_window_gets_the_message_and_a_plain_status(self):
        cases = ((InvalidSpec("плохой параметр"), 400), (JobConflict("нельзя"), 409),
                 (JobNotFound("нет"), 404), (InvalidTransition("нельзя"), 409),
                 (SchemaVersionError("новее"), 500))
        for error, status in cases:
            message, got = window_error(error)
            self.assertEqual(got, status, type(error).__name__)
            self.assertEqual(message, str(error))


if __name__ == "__main__":
    unittest.main()
