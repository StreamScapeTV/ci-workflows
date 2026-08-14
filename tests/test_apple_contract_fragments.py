from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ci_workflows.apple_contract import build_plan
from ci_workflows.apple_contract_fragments import load_apple_contract
from ci_workflows.apple_types import AppleProfile, AppleValidationError, AppleValidationRequest

ROOT = Path(__file__).resolve().parents[1]


class AppleContractFragmentTests(unittest.TestCase):
    @staticmethod
    def source_audit_task() -> dict[str, object]:
        return {
            "validation_profile": "source-audit",
            "working_directory": ".",
            "container": None,
            "simulator_id": None,
            "commands": [],
            "protected_paths": ["AGENTS.md"],
            "cleanup_paths": [],
            "artifact_exception_ids": [],
            "environment": {},
        }

    def fragment_root(self, directory: str, fragment: object) -> Path:
        root = Path(directory)
        contracts = root / "contracts"
        contracts.mkdir()
        (contracts / "apple-validation.json").write_text(
            (ROOT / "contracts/apple-validation.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (contracts / "apple-validation-synthetic.json").write_text(
            json.dumps(fragment),
            encoding="utf-8",
        )
        return root

    def test_valid_addition_uses_normal_typed_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.fragment_root(
                directory,
                {
                    "tasks": {"fragment-source-audit": self.source_audit_task()},
                    "consumer_contracts": {
                        "fragment-apple-smoke": {
                            "repository": "StreamScapeTV/ci-workflows",
                            "profiles": {"source-audit": "fragment-source-audit"},
                        }
                    },
                },
            )
            contract = load_apple_contract(root)
            plan = build_plan(
                contract,
                AppleValidationRequest(
                    repository="StreamScapeTV/ci-workflows",
                    admitted_sha="a" * 40,
                    consumer_contract="fragment-apple-smoke",
                    validation_profile=AppleProfile.SOURCE_AUDIT,
                    source_trust="trusted-exact",
                    platform="apple",
                ),
            )
            self.assertEqual(plan.task_profile, "fragment-source-audit")
            self.assertEqual(plan.runner_profile.value, "apple")
            self.assertEqual(plan.planner_runner_profile.value, "portable")
            self.assertEqual(plan.commands, ())
            self.assertEqual(plan.protected_paths, ("AGENTS.md",))

    def test_shape_task_and_consumer_collisions_fail_closed(self) -> None:
        cases = (
            (
                {
                    "tasks": {"fragment-source-audit": self.source_audit_task()},
                    "consumer_contracts": {
                        "fragment-apple-smoke": {
                            "repository": "StreamScapeTV/ci-workflows",
                            "profiles": {"source-audit": "fragment-source-audit"},
                        }
                    },
                    "extra": {},
                },
                "contract_invalid",
            ),
            (
                {
                    "tasks": {"ciw-source-audit": self.source_audit_task()},
                    "consumer_contracts": {
                        "fragment-apple-smoke": {
                            "repository": "StreamScapeTV/ci-workflows",
                            "profiles": {"source-audit": "ciw-source-audit"},
                        }
                    },
                },
                "contract_invalid",
            ),
            (
                {
                    "tasks": {"fragment-source-audit": self.source_audit_task()},
                    "consumer_contracts": {
                        "ciw-apple-smoke": {
                            "repository": "StreamScapeTV/ci-workflows",
                            "profiles": {"source-audit": "fragment-source-audit"},
                        }
                    },
                },
                "consumer_contract_rejected",
            ),
        )
        for fragment, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(AppleValidationError) as failure:
                    load_apple_contract(self.fragment_root(directory, fragment))
                self.assertEqual(failure.exception.code, expected)

    def test_shared_loader_contains_no_product_branch(self) -> None:
        source = (
            ROOT / "src/ci_workflows/apple_contract_fragments.py"
        ).read_text(encoding="utf-8").casefold()
        self.assertNotIn("streamscapetv/iptv-apple", source)
        self.assertNotIn("streamscapetv/streamscape-media", source)
        self.assertNotIn("streamscape_", source)


if __name__ == "__main__":
    unittest.main()
