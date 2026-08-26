from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import importlib.util

MODULE_PATH = ROOT / "scripts" / "ci" / "bootstrap_check.py"
SPEC = importlib.util.spec_from_file_location("bootstrap_check", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BootstrapContractTests(unittest.TestCase):
    def test_complete_bootstrap_contract(self) -> None:
        MODULE.validate_required_paths()
        MODULE.validate_public_workflow_exceptions()
        MODULE.validate_runner_contract()
        MODULE.validate_self_check()
        MODULE.validate_requirements()
        MODULE.validate_authority_docs()

    def test_retired_global_policy_and_custom_runtime_are_absent(self) -> None:
        for relative in (
            "contracts/action-tool-lock.json",
            "contracts/artifact-policy.json",
            "contracts/security-policy.json",
            "requirements/validation.lock",
            "scripts/ci/bootstrap_validation_runtime.py",
            "src/ci_workflows/validation_runtime.py",
        ):
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_validation_dependencies_are_conventional_repository_requirements(self) -> None:
        source = (ROOT / "requirements/validation.txt").read_text(encoding="utf-8")
        self.assertIn("PyYAML", source)
        self.assertNotIn("--hash", source)
        self.assertNotIn("sha256", source.casefold())
        self.assertNotIn("files.pythonhosted.org", source)

    def test_public_workflow_bootstrap_contract_is_exactly_supported_surface(self) -> None:
        self.assertEqual(
            MODULE.allowed_bootstrap_workflows(),
            [
                ".github/workflows/reusable-android-live-service.yml",
                ".github/workflows/reusable-android-release.yml",
                ".github/workflows/reusable-android.yml",
                ".github/workflows/reusable-apple.yml",
                ".github/workflows/reusable-device.yml",
                ".github/workflows/reusable-flutter.yml",
                ".github/workflows/reusable-gitops-validation.yml",
                ".github/workflows/reusable-gradle-maven-publish.yml",
                ".github/workflows/reusable-native-image-chart.yml",
                ".github/workflows/reusable-native.yml",
                ".github/workflows/reusable-node.yml",
                ".github/workflows/reusable-oci-reproducibility.yml",
                ".github/workflows/reusable-package-publish.yml",
                ".github/workflows/reusable-public-native-image-chart.yml",
                ".github/workflows/reusable-python.yml",
                ".github/workflows/reusable-resolve-source.yml",
                ".github/workflows/reusable-script.yml",
                ".github/workflows/reusable-static-web.yml",
                ".github/workflows/reusable-tag-image-chart.yml",
            ],
        )

    def test_retired_public_wrappers_are_not_bootstrap_exceptions(self) -> None:
        allowed = set(MODULE.allowed_bootstrap_workflows())
        for retired in (
            ".github/workflows/reusable-network-download.yml",
            ".github/workflows/reusable-oci-build.yml",
            ".github/workflows/reusable-oci-publish.yml",
            ".github/workflows/reusable-helm-validate.yml",
            ".github/workflows/reusable-helm-publish.yml",
        ):
            self.assertNotIn(retired, allowed)
            self.assertFalse((ROOT / retired).exists())

    def test_self_check_uses_automatic_discovery_and_verified_python(self) -> None:
        source = (ROOT / ".github/workflows/self-check.yml").read_text()
        self.assertIn(
            '"${VERIFIED_PYTHON}" -m unittest discover '
            "-s tests -p 'test_*.py' -v",
            source,
        )
        self.assertNotIn("python3 -m unittest discover", source)
        self.assertNotIn("actions/setup-python@", source)
        self.assertIn("requirements/validation.txt", source)
        self.assertIn('"${VERIFIED_PYTHON}" -m pip install', source)
        self.assertNotIn("bootstrap_validation_runtime.py", source)
        self.assertNotIn("action-tool-lock.json", source)

    def test_self_check_uses_final_general_linux_capability_contract(self) -> None:
        source = (ROOT / ".github/workflows/self-check.yml").read_text()
        self.assertEqual(source.count("runs-on: [ubuntu-latest]"), 1)
        self.assertNotIn("runs-on: [linux, amd64, general, small]", source)
        self.assertNotIn("runs-on: [linux, amd64, general]", source)
        self.assertNotIn("runs-on: portable", source)
        self.assertNotIn("runs-on: macOS", source)
        MODULE.validate_runner_contract()


if __name__ == "__main__":
    unittest.main()
