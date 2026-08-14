from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEVICE_ACTION_SHA = "092d26777aebaf87a0a07d39a1660b4bc5cb658d"
DEVICE_LOCK_ACTION_SHA = "599c82201e6da6ca51c4f6247f1526a4ba03d550"
FOUNDATION_ACTION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"


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

    def test_public_secret_contract_separates_authorization_from_live_backend(self) -> None:
        secrets = self.workflow.split("secrets:", 1)[1].split("outputs:", 1)[0]
        actual = set(re.findall(r"^      ([a-z_]+):$", secrets, re.M))
        self.assertEqual(
            {"device_authorization_receipt", "live_test_credentials"},
            actual,
        )
        self.assertEqual(self.profile["public_secrets"], sorted(actual))
        self.assertIn("CIW_DEVICE_AUTHORIZATION_RECEIPT", self.workflow)
        self.assertIn("CIW_DEVICE_LIVE_BACKEND_PRESENT", self.workflow)

    def test_action_has_no_caller_trust_or_raw_identifier_input(self) -> None:
        input_block = self.action.split("inputs:", 1)[1].split("outputs:", 1)[0]
        for forbidden in ("\n  source_trust:", "\n  device_identifier:", "\n  serial:", "\n  udid:"):
            self.assertNotIn(forbidden, input_block)
        self.assertIn("validated_plan:", input_block)
        self.assertIn("validated_plan_sha256:", input_block)
        self.assertIn("selected_device_hash:", input_block)
        self.assertIn("resource_lock_receipt:", input_block)
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

    def test_called_workflow_uses_immutable_private_actions_without_central_checkout(self) -> None:
        self.assertNotIn("uses: actions/checkout@", self.workflow)
        self.assertNotIn("job.workflow_repository", self.workflow)
        self.assertNotIn("job.workflow_sha", self.workflow)
        self.assertNotIn("uses: ./", self.workflow)
        device_ref = (
            "StreamScapeTV/ci-workflows/actions/validate-device@" + DEVICE_ACTION_SHA
        )
        lock_ref = (
            "StreamScapeTV/ci-workflows/actions/device-lock@" + DEVICE_LOCK_ACTION_SHA
        )
        self.assertEqual(8, self.workflow.count(device_ref))
        self.assertEqual(4, self.workflow.count(lock_ref))
        for action in ("exact-checkout", "prepare-workspace", "cleanup-workspace"):
            self.assertIn(
                f"StreamScapeTV/ci-workflows/actions/{action}@{FOUNDATION_ACTION_SHA}",
                self.workflow,
            )

    def test_concurrency_group_is_supplemental_to_production_fencing(self) -> None:
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
        self.assertTrue(self.profile["serialization_contract"]["fencing_token"])
        self.assertTrue(self.profile["lock_contract"]["cross_run_fencing_claimed"])

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

    def test_production_lock_is_verified_immediately_before_mutation(self) -> None:
        device_job = self.workflow.split("  device:\n", 1)[1]
        discover = device_job.index("Deterministically discover one eligible physical device")
        acquire = device_job.index("Acquire production cross-run device fencing receipt")
        verify = device_job.index("Verify exact fencing receipt before physical mutation")
        execute = device_job.index("Execute only the exact typed plan emitted by the planner")
        restore = device_job.index("Restore product-owned physical-device state before releasing lock")
        release = device_job.index("Expected-state release exact production fencing receipt")
        residue = device_job.index("Verify production fencing receipt has no live residue")
        self.assertLess(discover, acquire)
        self.assertLess(acquire, verify)
        self.assertLess(verify, execute)
        self.assertLess(execute, restore)
        self.assertLess(restore, release)
        self.assertLess(release, residue)
        self.assertIn("resource_lock_receipt: ${{ steps.acquire_lock.outputs.resource_lock_receipt }}", device_job)
        self.assertIn("selected_device_hash: ${{ steps.discover.outputs.selected_device_hash }}", device_job)

    def test_source_cleanup_is_action_owned_and_smoke_cleanup_remains_no_follow(self) -> None:
        self.assertEqual(2, self.workflow.count("phase: cleanup-checkout"))
        self.assertNotIn("path: .ciw", self.workflow)
        self.assertNotIn("cleanup-checkout --source-root .ciw", self.workflow)
        self.assertGreaterEqual(self.smoke.count("if: always()"), 2)
        self.assertGreaterEqual(
            self.smoke.count("def remove_no_follow(path: Path) -> None:"), 3
        )
        self.assertGreaterEqual(self.smoke.count("os.lstat(path)"), 3)
        self.assertGreaterEqual(self.smoke.count("os.unlink(path)"), 3)
        self.assertGreaterEqual(self.smoke.count("os.rmdir(path)"), 3)
        self.assertGreaterEqual(self.smoke.count('remove_no_follow(Path("source"))'), 2)
        self.assertGreaterEqual(self.smoke.count('remove_no_follow(Path(".ciw"))'), 3)
        self.assertGreaterEqual(self.smoke.count("test ! -e source"), 1)
        self.assertGreaterEqual(self.smoke.count("test ! -L source"), 1)
        self.assertGreaterEqual(self.smoke.count("test ! -e .ciw"), 2)
        self.assertGreaterEqual(self.smoke.count("test ! -L .ciw"), 2)
        self.assertEqual(3, self.smoke.count("clean: true"))
        self.assertGreaterEqual(self.smoke.count("rev-parse HEAD"), 3)

    def test_terminal_projection_preserves_restore_lock_and_cleanup_failures(self) -> None:
        device_job = self.workflow.split("  device:\n", 1)[1]
        self.assertGreaterEqual(device_job.count("continue-on-error: true"), 11)
        self.assertIn(
            "Project terminal physical-device result after restore, release, and cleanup",
            device_job,
        )
        for name in (
            "DISCOVER_OUTCOME",
            "ACQUIRE_LOCK_OUTCOME",
            "VERIFY_LOCK_OUTCOME",
            "EXECUTE_OUTCOME",
            "RESTORE_OUTCOME",
            "RELEASE_LOCK_OUTCOME",
            "LOCK_RESIDUE_OUTCOME",
            "DEVICE_CLEANUP_OUTCOME",
            "DEVICE_RESIDUE_OUTCOME",
            "SOURCE_CLEANUP_OUTCOME",
            "WORKSPACE_CLEANUP_OUTCOME",
        ):
            self.assertIn(name, device_job)

    def test_smoke_is_source_only_and_synthetic(self) -> None:
        self.assertEqual(2, self.smoke.count("runs-on: [linux, amd64, general]"))
        self.assertEqual(3, self.smoke.count("phase: synthetic"))
        self.assertNotIn("phase: execute", self.smoke)
        self.assertNotIn("device_authorization_receipt", self.smoke)
        self.assertNotIn("live_test_credentials", self.smoke)
        self.assertNotIn("device_identifier:", self.smoke)
        self.assertNotIn("source_trust:", self.smoke)
        self.assertIn("Verify device contract smoke artifacts remain zero", self.smoke)

    def test_smoke_finalizer_skips_cancelled_runs_but_cleanup_remains_unconditional(self) -> None:
        cleanup = self.smoke.split(
            "      - name: Remove exact synthetic and central source without following links\n", 1
        )[1].split("  zero_artifacts:\n", 1)[0]
        finalizer = self.smoke.split("  zero_artifacts:\n", 1)[1]
        self.assertIn("if: always()", cleanup)
        self.assertIn("if: ${{ always() && !cancelled() }}", finalizer)
        self.assertNotIn("if: always()\n    runs-on", finalizer)

    def test_direct_synthetic_selectors_are_general_linux_not_semantic_profiles(self) -> None:
        selector = "runs-on: [linux, amd64, general]"
        selector_value = "[linux, amd64, general]"
        self.assertEqual(2, self.workflow.count(selector))
        self.assertNotIn("runs-on: portable", self.workflow + self.smoke)
        self.assertIn(
            "runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}",
            self.workflow,
        )
        direct_selectors = re.findall(
            r"^    runs-on: (.+)$",
            self.workflow + "\n" + self.smoke,
            re.M,
        )
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

    def test_in_memory_adapter_is_test_only_and_production_adapter_is_canonical(self) -> None:
        self.assertEqual(
            "in-memory-tests-only",
            self.profile["lock_contract"]["temporary_reference_adapter"],
        )
        self.assertTrue(self.profile["lock_contract"]["cross_run_fencing_claimed"])
        self.assertEqual(
            "device-lock/1:posix-shared-root-v1",
            self.profile["lock_contract"]["production_adapter"],
        )
        self.assertFalse(self.profile["lock_contract"]["agent_state_transport_used"])

    def test_shared_registration_surfaces_include_device(self) -> None:
        public = json.loads((ROOT / "contracts/public-workflows.json").read_text())
        validation = json.loads(
            (ROOT / "contracts/public-workflows/validation.json").read_text()
        )
        bootstrap = json.loads(
            (ROOT / "contracts/bootstrap-public-workflows.json").read_text()
        )
        public_device = next(
            item for item in public["workflows"] if item["api_name"] == "validation.device"
        )
        validation_device = next(
            item
            for item in validation["workflows"]
            if item["api_name"] == "validation.device"
        )
        self.assertEqual("implemented", public_device["status"])
        self.assertEqual("implemented", validation_device["status"])
        self.assertEqual(
            ["device_authorization_receipt", "live_test_credentials"],
            validation_device["secrets"],
        )
        self.assertIn(
            ".github/workflows/reusable-device.yml",
            {item["path"] for item in bootstrap["allowed"]},
        )

    def test_documentation_states_all_required_boundaries(self) -> None:
        text = self.docs.casefold()
        for phrase in (
            "opaque alias",
            "raw serial or udid",
            "current github source admission",
            "cancel-in-progress: false",
            "device-lock/1",
            "device_authorization_receipt",
            "physical_authorization_required",
            "restore",
            "release",
            "zero routine actions artifacts",
            "immutable private action",
        ):
            self.assertIn(phrase.casefold(), text)


if __name__ == "__main__":
    unittest.main()
