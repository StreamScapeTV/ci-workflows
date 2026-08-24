from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
POLICY = ROOT / "contracts/repository-policy.json"
PUBLIC = ROOT / "contracts/public-workflows.json"

RETAINED_NON_PUBLIC = {
    ".github/workflows/android-validation-smoke.yml",
    ".github/workflows/apple-physical-device-lock-smoke.yml",
    ".github/workflows/apple-validation-smoke.yml",
    ".github/workflows/central-ci-dispatch.yml",
    ".github/workflows/ci-broker-image.yml",
    ".github/workflows/device-lock-contract-smoke.yml",
    ".github/workflows/device-validation-contract-smoke.yml",
    ".github/workflows/flutter-apple-validation-smoke.yml",
    ".github/workflows/flutter-validation-smoke.yml",
    ".github/workflows/gitops-validation-smoke.yml",
    ".github/workflows/internal-runner-image.yml",
    ".github/workflows/r2-diagnostics-smoke.yml",
    ".github/workflows/runner-images-release.yml",
    ".github/workflows/runner-images-validation.yml",
    ".github/workflows/self-check.yml",
    ".github/workflows/service-runner-smoke.yml",
}

RETIRED_ENTRYPOINTS = {
    ".github/workflows/android-completion-smoke.yml",
    ".github/workflows/apple-certification-smoke.yml",
    ".github/workflows/apple-test.yml",
    ".github/workflows/helm-validation-smoke.yml",
    ".github/workflows/native-image-chart-call-parse-smoke.yml",
    ".github/workflows/oci-build-smoke.yml",
    ".github/workflows/oci-publish-smoke.yml",
}


class WorkflowSurfaceTests(unittest.TestCase):
    @staticmethod
    def public_rows() -> list[dict[str, object]]:
        return json.loads(PUBLIC.read_text(encoding="utf-8"))["workflows"]

    @staticmethod
    def policy_rows() -> dict[str, dict[str, object]]:
        payload = json.loads(POLICY.read_text(encoding="utf-8"))
        return payload["workflow_admission"]["workflows"]

    def test_terminal_surface_is_public_api_plus_reviewed_execution_boundaries(self) -> None:
        public_paths = {str(row["file"]) for row in self.public_rows()}
        actual = {
            path.relative_to(ROOT).as_posix()
            for path in WORKFLOWS.glob("*.y*ml")
        }
        self.assertEqual(19, len(public_paths))
        self.assertEqual(public_paths | RETAINED_NON_PUBLIC, actual)
        self.assertEqual(35, len(actual))
        self.assertTrue(RETIRED_ENTRYPOINTS.isdisjoint(actual))

    def test_every_supported_public_capability_has_one_callable_entrypoint(self) -> None:
        rows = self.public_rows()
        api_names = [str(row["api_name"]) for row in rows]
        paths = [str(row["file"]) for row in rows]
        self.assertEqual(len(api_names), len(set(api_names)))
        self.assertEqual(len(paths), len(set(paths)))

        policy = self.policy_rows()
        for row in rows:
            relative = str(row["file"])
            with self.subTest(api=row["api_name"]):
                self.assertTrue(relative.startswith(".github/workflows/reusable-"))
                self.assertEqual("reusable-call", policy[relative]["trust_class"])
                self.assertEqual(["workflow_call"], policy[relative]["allowed_events"])
                workflow = yaml.load(
                    (ROOT / relative).read_text(encoding="utf-8"),
                    Loader=ActionsLoader,
                )
                self.assertEqual({"workflow_call"}, set(workflow["on"]))

    def test_repository_policy_is_exact_inventory_not_a_second_capability_catalogue(self) -> None:
        policy = self.policy_rows()
        actual = {
            path.relative_to(ROOT).as_posix()
            for path in WORKFLOWS.glob("*.y*ml")
        }
        self.assertEqual(actual, set(policy))
        public_paths = {str(row["file"]) for row in self.public_rows()}
        reusable_policy_paths = {
            path
            for path, row in policy.items()
            if row["trust_class"] == "reusable-call"
        }
        self.assertEqual(public_paths, reusable_policy_paths)

    def test_retired_contract_and_parser_workflows_stay_ordinary_test_concerns(self) -> None:
        for relative in sorted(RETIRED_ENTRYPOINTS):
            with self.subTest(workflow=relative):
                self.assertFalse((ROOT / relative).exists())

        self.assertTrue((ROOT / "tests/test_android_live_service.py").is_file())
        self.assertTrue((ROOT / "tests/test_android_release.py").is_file())
        self.assertTrue((ROOT / "tests/test_apple_release_profiles.py").is_file())
        self.assertTrue((ROOT / "tests/test_native_image_chart_release.py").is_file())
        self.assertTrue((ROOT / "tests/test_oci_workflow_contract.py").is_file())
        self.assertTrue((ROOT / "tests/test_oci_publication_workflow_contract.py").is_file())


if __name__ == "__main__":
    unittest.main()
