from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DeviceWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = json.loads((ROOT / "contracts/device-profiles.json").read_text())
        self.evidence = json.loads((ROOT / "contracts/device-evidence.json").read_text())
        self.workflow = (ROOT / ".github/workflows/reusable-device.yml").read_text()
        self.smoke = (ROOT / ".github/workflows/device-validation-contract-smoke.yml").read_text()
        self.action = (ROOT / "actions/validate-device/action.yml").read_text()
        self.docs = (
            (ROOT / "docs/workflows/devices.md").read_text()
            + "\n"
            + (ROOT / "docs/architecture/device-validation.md").read_text()
        )

    def test_public_api_uses_opaque_alias_and_no_raw_identifier_or_trust(self) -> None:
        inputs = self.workflow.split("inputs:", 1)[1].split("secrets:", 1)[0]
        actual = set(re.findall(r"^      ([a-z_]+):$", inputs, re.M))
        self.assertEqual(
            {
                "admitted_sha",
                "device_family",
                "device_capability",
                "device_alias",
                "command_profile",
                "script_path",
                "max_duration_minutes",
                "evidence_exception_id",
                "request_id",
            },
            actual,
        )
        for forbidden in ("device_identifier:", "source_trust:", "serial:", "udid:"):
            self.assertNotIn(forbidden, inputs)
        self.assertEqual(set(self.profile["public_inputs"]), actual)

    def test_action_has_no_caller_trust_or_raw_identifier_input(self) -> None:
        input_block = self.action.split("inputs:", 1)[1].split("outputs:", 1)[0]
        for forbidden in ("\n  source_trust:", "\n  device_identifier:", "\n  serial:", "\n  udid:"):
            self.assertNotIn(forbidden, input_block)
        self.assertIn("validated_plan:", input_block)
        self.assertIn("validated_plan_sha256:", input_block)
        self.assertIn("CIW_DEVICE_HEAD_FORK", self.action)
        self.assertIn("CIW_DEVICE_EVENT_SHA", self.action)

    def test_planner_emits_typed_plan_and_executor_consumes_it(self) -> None:
        for output in ("validated_plan", "validated_plan_sha256", "concurrency_group"):
            self.assertIn(f"{output}: ${{{{ steps.plan.outputs.{output} }}}}", self.workflow)
        self.assertIn("validated_plan: ${{ needs.plan.outputs.validated_plan }}", self.workflow)
        self.assertIn(
            "validated_plan_sha256: ${{ needs.plan.outputs.validated_plan_sha256 }}",
            self.workflow,
        )
        self.assertIn("Execute only the exact typed plan emitted by the planner", self.workflow)

    def test_concurrency_group_is_planner_owned_and_never_cancellable(self) -> None:
        self.assertIn(
            "group: ${{ needs.plan.outputs.concurrency_group }}",
            self.workflow,
        )
        self.assertIn("cancel-in-progress: false", self.workflow)
        public_inputs = self.workflow.split("inputs:", 1)[1].split("secrets:", 1)[0]
        self.assertNotIn("concurrency_group:", public_inputs)
        self.assertNotIn("cancel_in_progress:", public_inputs)
        self.assertFalse(self.profile["serialization_contract"]["caller_override"])
        self.assertFalse(self.profile["serialization_contract"]["cancel_in_progress"])
        self.assertFalse(self.profile["serialization_contract"]["fencing_token"])

    def test_executor_revalidates_exact_checkout(self) -> None:
        device_job = self.workflow.split("  device:\n", 1)[1]
        self.assertIn("Revalidate exact admitted caller source in executor", device_job)
        self.assertIn("git rev-parse HEAD", device_job)
        self.assertIn("git status --porcelain=v1 --untracked-files=all", device_job)
        self.assertIn("needs.plan.outputs.admitted_sha", device_job)

    def test_real_physical_execution_is_fail_closed(self) -> None:
        self.assertIn("needs.plan.outputs.execution_authorized == 'true'", self.workflow)
        self.assertIn("authorization_denied:", self.workflow)
        self.assertIn(
            "needs.plan.outputs.execution_authorized != 'true'",
            self.workflow,
        )
        self.assertIn("Report stable physical authorization denial", self.workflow)
        self.assertIn("physical_authorization_required", self.workflow)
        self.assertIn("jobs.authorization_denied.outputs.result", self.workflow)
        self.assertEqual([], self.profile["owner_authorization"]["authorized_families"])
        self.assertEqual(
            "physical_authorization_required",
            self.profile["owner_authorization"]["failure_code"],
        )
        self.assertFalse(self.profile["owner_authorization"]["runner_or_secret_is_authorization"])

    def test_ciw_and_source_cleanup_are_always_no_follow_and_proven_absent(self) -> None:
        combined = self.workflow + "\n" + self.smoke
        self.assertGreaterEqual(combined.count("if: always()"), 4)
        self.assertGreaterEqual(combined.count("cleanup-checkout --source-root .ciw"), 3)
        self.assertGreaterEqual(combined.count("! -e .ciw"), 3)
        self.assertGreaterEqual(combined.count("! -L .ciw"), 3)
        self.assertGreaterEqual(combined.count("test ! -e source"), 3)
        self.assertGreaterEqual(combined.count("test ! -L source"), 3)

    def test_smoke_is_source_only_and_synthetic(self) -> None:
        self.assertEqual(2, self.smoke.count("runs-on: [linux, amd64, general]"))
        self.assertEqual(3, self.smoke.count("phase: synthetic"))
        self.assertNotIn("phase: execute", self.smoke)
        self.assertNotIn("live_test_credentials", self.smoke)
        self.assertNotIn("device_identifier:", self.smoke)
        self.assertNotIn("source_trust:", self.smoke)
        self.assertIn("Verify device contract smoke artifacts remain zero", self.smoke)

    def test_direct_synthetic_selectors_are_general_linux_not_semantic_profiles(self) -> None:
        selector = "runs-on: [linux, amd64, general]"
        selector_value = "[linux, amd64, general]"
        self.assertEqual(2, self.workflow.count(selector))
        self.assertNotIn("runs-on: portable", self.workflow + self.smoke)
        self.assertIn(
            "runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}",
            self.workflow,
        )
        direct_selectors = re.findall(r"^    runs-on: (.+)$", self.workflow + "\n" + self.smoke, re.M)
        self.assertEqual(4, direct_selectors.count(selector_value))
        direct_selector_text = "\n".join(direct_selectors)
        for forbidden in (
            "physical-device",
            "self-hosted",
            "buildah",
            "buildah-tiny",
            "buildah-small",
            "buildah-medium",
            "buildah-high",
        ):
            self.assertNotIn(forbidden, direct_selector_text)

    def test_zero_publication_cluster_signing_and_artifact_authority(self) -> None:
        text = (self.workflow + self.smoke + self.action).casefold()
        for forbidden in (
            "upload-artifact",
            "download-artifact",
            "buildah",
            "docker",
            "podman",
            "kubectl",
            "helm install",
            "helm upgrade",
            "testflight",
            "notarization",
        ):
            self.assertNotIn(forbidden, text)

    def test_in_memory_adapter_is_explicitly_test_only(self) -> None:
        self.assertEqual(
            "in-memory-tests-only",
            self.profile["lock_contract"]["temporary_reference_adapter"],
        )
        self.assertFalse(self.profile["lock_contract"]["cross_run_fencing_claimed"])
        self.assertEqual("none-in-source-package", self.profile["lock_contract"]["production_adapter"])

    def test_shared_high_collision_files_are_not_owned(self) -> None:
        owned_roots = {
            ".github/workflows/reusable-device.yml",
            ".github/workflows/device-validation-contract-smoke.yml",
            "actions/validate-device/action.yml",
            "scripts/ci/device.py",
            "src/ci_workflows/device.py",
            "src/ci_workflows/device_contract.py",
            "src/ci_workflows/device_execution.py",
            "src/ci_workflows/device_types.py",
            "src/ci_workflows/ciw_device.py",
            "contracts/device-profiles.json",
            "contracts/device-evidence.json",
            "tests/fixtures/device-validation",
            "tests/test_device_validation.py",
            "tests/test_device_workflow_contract.py",
            "docs/workflows/devices.md",
            "docs/architecture/device-validation.md",
        }
        for deferred in (
            "contracts/public-workflows.json",
            "contracts/public-workflow-types.json",
            "contracts/ciw-commands.json",
            "contracts/action-tool-lock.json",
            "contracts/runner-profiles.json",
            "contracts/bootstrap-public-workflows.json",
            "generated/runner-mappings.json",
            "src/ci_workflows/ciw.py",
            "tests/test_bootstrap.py",
        ):
            self.assertNotIn(deferred, owned_roots)

    def test_documentation_states_all_required_boundaries(self) -> None:
        text = self.docs.casefold()
        for phrase in (
            "opaque alias",
            "raw serial or udid",
            "current github source admission",
            "cancel-in-progress: false",
            "not a fencing token",
            "physical_authorization_required",
            "no real physical-device execution is authorized",
            "zero routine actions artifacts",
            ".ciw",
        ):
            self.assertIn(phrase.casefold(), text)


if __name__ == "__main__":
    unittest.main()
