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
        MODULE.validate_public_workflow_exceptions()
        MODULE.validate_self_check()
        MODULE.validate_policies()
        MODULE.validate_authority_docs()

    def test_release_policy_uses_main_and_tag_without_release_assets(self) -> None:
        policy = json.loads(
            (ROOT / "contracts/security-policy.json").read_text()
        )
        release = policy["release_reference_policy"]
        self.assertEqual(release["bootstrap_channel"], "main")
        self.assertIn(
            "git-tag",
            release["supported_immutable_references"],
        )
        self.assertFalse(release["github_release_required"])
        self.assertFalse(release["attached_artifacts_required"])

    def test_public_workflow_bootstrap_contract_includes_implemented_source(
        self,
    ) -> None:
        self.assertEqual(
            MODULE.allowed_bootstrap_workflows(),
            [
                ".github/workflows/reusable-resolve-source.yml",
                ".github/workflows/reusable-tag-image-chart.yml",
            ],
        )

    def test_self_check_uses_automatic_test_discovery(self) -> None:
        source = (ROOT / ".github/workflows/self-check.yml").read_text()
        self.assertIn(
            "python3 -m unittest discover -s tests -p 'test_*.py' -v",
            source,
        )
        self.assertNotIn(
            "python3 -m unittest -v tests/test_reusable_tag_image_chart.py",
            source,
        )
        self.assertNotIn(
            "python3 -m unittest -v tests/test_bootstrap.py",
            source,
        )


if __name__ == "__main__":
    unittest.main()
