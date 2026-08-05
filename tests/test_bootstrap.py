from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "ci" / "bootstrap_check.py"
SPEC = importlib.util.spec_from_file_location("bootstrap_check", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BootstrapContractTests(unittest.TestCase):
    def test_complete_bootstrap_contract(self) -> None:
        MODULE.validate_required_paths()
        MODULE.validate_no_public_workflow_api()
        MODULE.validate_self_check()
        MODULE.validate_policies()
        MODULE.validate_authority_docs()

    def test_release_policy_uses_main_and_tag_without_release_assets(self) -> None:
        policy = json.loads((ROOT / "contracts/security-policy.json").read_text())
        release = policy["release_reference_policy"]
        self.assertEqual(release["bootstrap_channel"], "main")
        self.assertIn("git-tag", release["supported_immutable_references"])
        self.assertFalse(release["github_release_required"])
        self.assertFalse(release["attached_artifacts_required"])

    def test_artifact_registry_starts_empty(self) -> None:
        policy = json.loads((ROOT / "contracts/artifact-policy.json").read_text())
        self.assertEqual(policy["default"], "zero-routine-artifacts")
        self.assertEqual(policy["exceptions"], [])

    def test_no_consumer_facing_workflow_is_published_yet(self) -> None:
        workflows = ROOT / ".github" / "workflows"
        public = list(workflows.glob("reusable-*.yml")) + list(
            workflows.glob("reusable-*.yaml")
        )
        self.assertEqual(public, [])


if __name__ == "__main__":
    unittest.main()
