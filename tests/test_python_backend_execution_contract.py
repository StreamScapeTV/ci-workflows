from __future__ import annotations

import json
import unittest
from pathlib import Path

from ci_workflows import python_execution
from ci_workflows.python_execution import _classify_script_failure

ROOT = Path(__file__).resolve().parents[1]


class PythonPostgresExecutionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(
            (ROOT / "contracts/python-validation.json").read_text(encoding="utf-8")
        )

    def test_postgres_handoff_is_one_fixed_generic_connection_contract(self) -> None:
        postgres = self.contract["postgres"]
        self.assertEqual("CIW_POSTGRES_URL", postgres["connection_environment_variable"])
        self.assertEqual("postgresql", postgres["connection_url_scheme"])
        self.assertEqual("ephemeral-per-execution", postgres["credentials"])
        self.assertFalse(postgres["remote_fallback"])
        self.assertNotIn("consumers", self.contract)
        self.assertNotIn("command_profiles", self.contract)
        self.assertFalse(hasattr(python_execution, "_database_url_scheme"))

    def test_no_consumer_can_select_database_variable_or_url_scheme(self) -> None:
        forbidden = set(self.contract["forbidden_inputs"])
        self.assertTrue(
            {
                "database_environment_variable",
                "database_password",
                "database_url",
                "database_url_scheme",
                "environment",
                "environment_json",
            }
            <= forbidden
        )

    def test_script_failure_diagnostics_are_fixed_categories(self) -> None:
        cases = {
            "name or service not known": "script_network_failure",
            "CERTIFICATE_VERIFY_FAILED": "script_tls_failure",
            "tool: command not found": "script_tool_missing",
            "pinned archive checksum mismatch": "script_integrity_failure",
            "consumer assertion text": "script_command_failed",
        }
        for output, expected in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(expected, _classify_script_failure(output))


if __name__ == "__main__":
    unittest.main()
