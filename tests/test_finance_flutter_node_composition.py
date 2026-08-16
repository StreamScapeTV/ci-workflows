from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.flutter_contract import load_flutter_contract
from ci_workflows.node_contract import (
    load_node_contract,
    request_from_environment,
    resolve_validation_plan,
)


class FinanceFlutterNodeCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.flutter_contract = load_flutter_contract(ROOT)
        self.node_contract = load_node_contract(ROOT)
        self.profiles = self.flutter_contract["consumer_contracts"][
            "finance-embedded-web"
        ]["profiles"]

    def test_explicit_finance_audit_is_not_repeated_in_quality_or_android(self) -> None:
        for profile_name in ("canonical-gate", "android-debug"):
            with self.subTest(profile=profile_name):
                self.assertIsNone(self.profiles[profile_name]["node_composition"])

    def test_remaining_finance_node_compositions_have_one_runtime_authority(self) -> None:
        checked = 0
        for profile_name, profile in self.profiles.items():
            composition = profile["node_composition"]
            if composition is None:
                continue
            with self.subTest(profile=profile_name):
                self.assertEqual(".node-version", composition.get("version_file"))
                self.assertNotIn("node_version", composition)
                environment = {
                    "GITHUB_REPOSITORY": "StreamScapeTV/finance-hub",
                    "GITHUB_EVENT_NAME": "workflow_dispatch",
                    "INPUT_ADMITTED_SHA": "a" * 40,
                    "INPUT_VALIDATION_PROFILE": composition["validation_profile"],
                    "INPUT_VERSION_FILE": composition.get("version_file", ""),
                    "INPUT_NODE_VERSION": composition.get("node_version", ""),
                    "INPUT_WORKING_DIRECTORY": composition["working_directory"],
                    "INPUT_INSTALL_PROFILE": composition["install_profile"],
                    "INPUT_COMMAND_PROFILE": composition["command_profile"],
                    "INPUT_SCRIPT_PATH": composition["script_path"],
                    "INPUT_PUBLIC_ENVIRONMENT": "",
                    "INPUT_ARTIFACT_EXCEPTION_ID": "",
                }
                request = request_from_environment(environment, self.node_contract)
                plan = resolve_validation_plan(self.node_contract, request)
                self.assertEqual(".node-version", plan.version_file)
                self.assertEqual("22.16.0", plan.node_version)
                self.assertEqual("source-audit", plan.command_profile)
                checked += 1

        self.assertEqual(4, checked)


if __name__ == "__main__":
    unittest.main()
