from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ci_workflows.maintenance_contract import MaintenanceError, load_contract

ROOT = Path(__file__).resolve().parents[1]


class MaintenanceProjectionContractTests(unittest.TestCase):
    def test_projection_policy_is_exact_and_machine_readable(self) -> None:
        policy = load_contract(ROOT).projection
        self.assertEqual(policy["repository_authority"], "projects")
        self.assertIs(policy["expected_state_required"], True)
        self.assertEqual(
            policy["status_states"],
            ["error", "failure", "pending", "success"],
        )
        self.assertEqual(policy["status_description_max_bytes"], 140)
        self.assertEqual(policy["comment_body_max_bytes"], 16000)
        self.assertEqual(policy["label_max_count"], 20)
        self.assertEqual(policy["label_max_bytes"], 50)

    def test_projection_policy_widening_fails_closed_without_code_review(self) -> None:
        raw = json.loads(
            (ROOT / "contracts/organization-maintenance.json").read_text(
                encoding="utf-8"
            )
        )
        for field, value in (
            ("status_description_max_bytes", 1000),
            ("comment_body_max_bytes", 100000),
            ("label_max_count", 100),
            ("label_max_bytes", 500),
            ("status_context_pattern", ".*"),
            ("comment_marker_pattern", ".*"),
        ):
            with self.subTest(field=field):
                changed = json.loads(json.dumps(raw))
                changed["projection"][field] = value
                with tempfile.TemporaryDirectory() as directory:
                    contracts = Path(directory) / "contracts"
                    contracts.mkdir()
                    (contracts / "organization-maintenance.json").write_text(
                        json.dumps(changed),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        MaintenanceError,
                        "invalid_contract",
                    ):
                        load_contract(Path(directory))

    def test_projection_policy_cannot_change_repository_or_expected_state_authority(self) -> None:
        raw = json.loads(
            (ROOT / "contracts/organization-maintenance.json").read_text(
                encoding="utf-8"
            )
        )
        for field, value in (
            ("repository_authority", "caller"),
            ("expected_state_required", False),
            ("status_states", ["success", "failure", "neutral"]),
        ):
            with self.subTest(field=field):
                changed = json.loads(json.dumps(raw))
                changed["projection"][field] = value
                with tempfile.TemporaryDirectory() as directory:
                    contracts = Path(directory) / "contracts"
                    contracts.mkdir()
                    (contracts / "organization-maintenance.json").write_text(
                        json.dumps(changed),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        MaintenanceError,
                        "invalid_contract",
                    ):
                        load_contract(Path(directory))


if __name__ == "__main__":
    unittest.main()
