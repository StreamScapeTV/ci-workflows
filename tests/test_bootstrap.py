from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

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
        MODULE.validate_runtime_lock()
        MODULE.validate_policies()
        MODULE.validate_authority_docs()

    def test_release_policy_uses_main_and_tag_without_release_assets(self) -> None:
        policy = json.loads(
            (ROOT / "contracts/security-policy.json").read_text()
        )
        release = policy["release_reference_policy"]
        self.assertEqual(release["bootstrap_channel"], "main")
        self.assertIn("git-tag", release["supported_immutable_references"])
        self.assertFalse(release["github_release_required"])
        self.assertFalse(release["attached_artifacts_required"])

    def test_public_workflow_bootstrap_contract_includes_implemented_source(
        self,
    ) -> None:
        self.assertEqual(
            MODULE.allowed_bootstrap_workflows(),
            [
                ".github/workflows/reusable-android.yml",
                ".github/workflows/reusable-flutter.yml",
                ".github/workflows/reusable-helm-publish.yml",
                ".github/workflows/reusable-helm-validate.yml",
                ".github/workflows/reusable-node.yml",
                ".github/workflows/reusable-python.yml",
                ".github/workflows/reusable-resolve-source.yml",
                ".github/workflows/reusable-tag-image-chart.yml",
            ],
        )

    def test_self_check_uses_automatic_discovery_and_verified_python(self) -> None:
        source = (ROOT / ".github/workflows/self-check.yml").read_text()
        self.assertIn(
            '"${VERIFIED_PYTHON}" -m unittest discover '
            "-s tests -p 'test_*.py' -v",
            source,
        )
        self.assertNotIn("python3 -m unittest discover", source)
        self.assertNotIn("actions/setup-python@", source)

    def test_self_check_uses_final_general_linux_capability_contract(self) -> None:
        source = (ROOT / ".github/workflows/self-check.yml").read_text()
        harness = json.loads(
            (ROOT / "contracts/validation-harness.json").read_text()
        )
        runner_contract = json.loads(
            (ROOT / "contracts/runner-profiles.json").read_text()
        )
        portable = next(
            profile
            for profile in runner_contract["profiles"]
            if profile["id"] == "portable"
        )
        self.assertEqual(
            source.count("runs-on: [linux, amd64, general]"),
            1,
        )
        self.assertNotIn("runs-on: portable", source)
        self.assertNotIn("runs-on: macOS", source)
        self.assertEqual(harness["allowed_runner_profiles"], ["portable"])
        self.assertEqual(
            portable["default_internal_selector"],
            ["linux", "amd64", "general"],
        )
        self.assertEqual(
            portable["internal_selectors"],
            [["linux", "amd64", "general"]],
        )
        exception = [
            item
            for item in harness["exceptions"]
            if item["path"] == ".github/workflows/self-check.yml"
        ]
        self.assertEqual([], exception)

    def test_self_check_rejects_obsolete_concrete_selector(self) -> None:
        source = (ROOT / ".github/workflows/self-check.yml").read_text()
        contaminated_source = source + "\n# homelab-portable-linux-x64\n"
        original = MODULE.read_text

        def read_text(relative: str) -> str:
            if relative == ".github/workflows/self-check.yml":
                return contaminated_source
            return original(relative)

        with mock.patch.object(
            MODULE,
            "read_text",
            side_effect=read_text,
        ):
            with self.assertRaisesRegex(
                SystemExit,
                "homelab-portable-linux-x64",
            ):
                MODULE.validate_self_check()


if __name__ == "__main__":
    unittest.main()
