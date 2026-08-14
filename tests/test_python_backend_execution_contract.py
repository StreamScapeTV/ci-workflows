from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from ci_workflows.python_execution import _classify_script_failure, _database_url_scheme
from ci_workflows.python_types import PythonValidationError

ROOT = Path(__file__).resolve().parents[1]


class PythonBackendExecutionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(
            (ROOT / "contracts/python-validation.json").read_text(encoding="utf-8")
        )

    def test_backend_postgres_uses_reviewed_async_sqlalchemy_scheme(self) -> None:
        plan = SimpleNamespace(
            repository="StreamScapeTV/iptv-backend",
            command_profile="postgres-test",
        )
        self.assertEqual(
            _database_url_scheme(plan, self.contract),
            "postgresql+asyncpg",
        )
        self.assertEqual(
            self.contract["consumers"]["StreamScapeTV/iptv-backend"]["profiles"][
                "postgres-test"
            ]["database_environment_variable"],
            "TEST_POSTGRES_DATABASE_URL",
        )

    def test_existing_postgres_consumer_keeps_plain_postgresql_default(self) -> None:
        plan = SimpleNamespace(
            repository="StreamScapeTV/agent-state",
            command_profile="postgres-test",
        )
        self.assertEqual(_database_url_scheme(plan, self.contract), "postgresql")

    def test_unreviewed_database_scheme_fails_closed(self) -> None:
        contract = copy.deepcopy(self.contract)
        contract["consumers"]["StreamScapeTV/iptv-backend"]["profiles"][
            "postgres-test"
        ]["database_url_scheme"] = "sqlite"
        plan = SimpleNamespace(
            repository="StreamScapeTV/iptv-backend",
            command_profile="postgres-test",
        )
        with self.assertRaises(PythonValidationError) as captured:
            _database_url_scheme(plan, contract)
        self.assertEqual(captured.exception.code, "invalid_input")

    def test_script_failure_diagnostics_are_fixed_categories(self) -> None:
        cases = {
            "name or service not known": "script_network_failure",
            "CERTIFICATE_VERIFY_FAILED": "script_tls_failure",
            "tool: command not found": "script_tool_missing",
            "pinned archive checksum mismatch": "script_integrity_failure",
            "product assertion text": "script_command_failed",
        }
        for output, expected in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(_classify_script_failure(output), expected)


if __name__ == "__main__":
    unittest.main()
