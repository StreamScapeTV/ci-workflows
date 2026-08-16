from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fixture_builder import create_repository, write_json
from ci_workflows.validation_harness import validate_repository


class MigrationPendingHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        create_repository(self.root)
        self.fragment = self.root / "contracts/public-workflows/validation.json"

    def _record(self) -> dict[str, object]:
        payload = json.loads(self.fragment.read_text(encoding="utf-8"))
        return payload["workflows"][0]

    def _write_record(self, record: dict[str, object]) -> None:
        payload = json.loads(self.fragment.read_text(encoding="utf-8"))
        payload["workflows"] = [record]
        write_json(self.fragment, payload)

    def _rules(self) -> set[str]:
        return {
            finding.rule
            for finding in validate_repository(
                self.root, include_public_api_validator=False
            ).findings
        }

    def test_migration_pending_suppresses_only_input_shape_drift(self) -> None:
        record = self._record()
        record["status"] = "migration-pending"
        record["inputs"] = [
            {"name": "admitted_sha", "required": True},
            {"name": "script_path", "required": True},
        ]
        self._write_record(record)

        rules = self._rules()
        self.assertNotIn("workflow-input-drift", rules)

    def test_same_input_mismatch_fails_after_contract_is_implemented(self) -> None:
        record = self._record()
        record["status"] = "implemented"
        record["inputs"] = [
            {"name": "admitted_sha", "required": True},
            {"name": "script_path", "required": True},
        ]
        self._write_record(record)

        self.assertIn("workflow-input-drift", self._rules())

    def test_migration_pending_still_enforces_secret_and_output_drift(self) -> None:
        record = self._record()
        record["status"] = "migration-pending"
        record["inputs"] = [
            {"name": "admitted_sha", "required": True},
            {"name": "script_path", "required": True},
        ]
        record["secrets"] = ["migration_token"]
        record["outputs"] = ["renamed_result"]
        self._write_record(record)

        rules = self._rules()
        self.assertNotIn("workflow-input-drift", rules)
        self.assertIn("workflow-secret-drift", rules)
        self.assertIn("workflow-output-drift", rules)


if __name__ == "__main__":
    unittest.main()
