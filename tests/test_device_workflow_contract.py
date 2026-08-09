from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DeviceWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = json.loads(
            (ROOT / "contracts/device-profiles.json").read_text(encoding="utf-8")
        )
        self.evidence = json.loads(
            (ROOT / "contracts/device-evidence.json").read_text(encoding="utf-8")
        )
        self.workflow = (
            ROOT / ".github/workflows/reusable-device.yml"
        ).read_text(encoding="utf-8")
        self.smoke = (
            ROOT / ".github/workflows/device-validation-contract-smoke.yml"
        ).read_text(encoding="utf-8")
        self.action = (
            ROOT / "actions/validate-device/action.yml"
        ).read_text(encoding="utf-8")
        self.docs = (
            (ROOT / "docs/workflows/devices.md").read_text(encoding="utf-8")
            + "\n"
            + (ROOT / "docs/architecture/device-validation.md").read_text(
                encoding="utf-8"
            )
        )

    def test_public_api_surface_matches_deferred_registry_shape(self) -> None:
        self.assertEqual("validation.device", self.profile["workflow_api"])
        self.assertEqual("1.0.0", self.profile["contract_version"])
        self.assertIn("name: CI / Physical device validation", self.workflow)
        self.assertIn("workflow_call:", self.workflow)
        inputs = self.workflow.split("inputs:", 1)[1].split("secrets:", 1)[0]
        actual_inputs = set(re.findall(r"^      ([a-z_]+):$", inputs, re.M))
        self.assertEqual(
            {
                "admitted_sha",
                "device_family",
                "device_capability",
                "device_identifier",
                "command_profile",
                "script_path",
                "max_duration_minutes",
                "evidence_exception_id",
                "request_id",
            },
            actual_inputs,
        )
        outputs = self.workflow.split("outputs:", 1)[1].split(
            "permissions:", 1
        )[0]
        actual_outputs = set(re.findall(r"^      ([a-z_]+):$", outputs, re.M))
        self.assertEqual(
            {
                "result",
                "device_evidence_id",
                "artifact_exception_used",
                "request_id",
            },
            actual_outputs,
        )
        self.assertIn("live_test_credentials:", self.workflow)
        self.assertNotIn("secrets: inherit", self.workflow)

    def test_reusable_workflow_has_no_direct_trigger_or_generic_pr_matrix(self) -> None:
        header = self.workflow.split("permissions:", 1)[0]
        self.assertNotIn("pull_request:", header)
        self.assertNotIn("push:", header)
        self.assertNotIn("workflow_dispatch:", header)
        self.assertNotIn("strategy:", self.workflow)
        self.assertNotIn("matrix:", self.workflow)

    def test_semantic_planning_and_guarded_execution_have_no_fallback(self) -> None:
        self.assertIn("runs-on: portable", self.workflow)
        self.assertIn(
            "runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}",
            self.workflow,
        )
        self.assertIn(
            "needs.plan.outputs.execution_authorized == 'true'",
            self.workflow,
        )
        for forbidden in (
            "runs-on: self-hosted",
            "runs-on: macOS",
            "runs-on: mobile",
            "ubuntu-latest",
            "macos-latest",
            "fallback",
        ):
            self.assertNotIn(forbidden, self.workflow)

    def test_contract_smoke_is_portable_synthetic_and_never_physical(self) -> None:
        self.assertIn("runs-on: portable", self.smoke)
        self.assertNotIn("fromJSON(", self.smoke)
        self.assertNotIn("reusable-device.yml@", self.smoke)
        self.assertNotIn("phase: execute", self.smoke)
        self.assertEqual(3, self.smoke.count("phase: synthetic"))
        self.assertIn("synthetic_mode: \"true\"", self.smoke)
        self.assertNotIn("live_test_credentials", self.smoke)
        self.assertNotIn("device_identifier:", self.smoke)

    def test_checkout_and_cleanup_are_exact_and_unconditional(self) -> None:
        combined = self.workflow + "\n" + self.smoke
        self.assertIn("persist-credentials: false", combined)
        self.assertIn("git rev-parse HEAD", combined)
        self.assertIn("if: always()", self.workflow)
        self.assertIn("phase: cleanup", self.workflow)
        self.assertIn("phase: residue", self.workflow)
        self.assertIn("rm -rf source", combined)
        self.assertIn("test ! -e source", combined)

    def test_zero_routine_artifacts_and_no_publication_authority(self) -> None:
        text = (self.workflow + self.smoke + self.action).casefold()
        for forbidden in (
            "upload-artifact",
            "download-artifact",
            "docker",
            "podman",
            "buildah",
            "kubectl",
            "helm",
            "play store",
            "app store",
            "testflight",
            "notarization",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("zero Actions artifacts", self.smoke)

    def test_action_exposes_only_bounded_internal_extensions(self) -> None:
        self.assertIn("phase:", self.action)
        self.assertIn("inventory_fixture:", self.action)
        self.assertIn("synthetic_mode:", self.action)
        for forbidden in (
            "\n  runner:",
            "\n  runs_on:",
            "\n  runner_labels:",
            "\n  arbitrary_command:",
            "\n  secret_name:",
            "\n  database_url:",
            "\n  deployment:",
        ):
            self.assertNotIn(forbidden, self.action)

    def test_contract_uses_no_real_identifiers_or_private_endpoints(self) -> None:
        text = json.dumps(self.profile, sort_keys=True).casefold()
        for forbidden in (
            "192.168.",
            "10.0.",
            "private.example",
            "real-udid",
            "personal-iphone",
            "bearer ",
        ):
            self.assertNotIn(forbidden, text)
        self.assertEqual(
            "canonical-resource-rpc-required",
            self.profile["lock_contract"]["backend"],
        )
        self.assertTrue(
            self.profile["lock_contract"]["ordinary_provisional_rpc_forbidden"]
        )
        self.assertTrue(
            self.profile["lock_contract"]["legacy_agent_state_forbidden"]
        )

    def test_evidence_contract_is_redacted_and_cannot_overclaim(self) -> None:
        self.assertIn("serial", self.evidence["forbidden_fields"])
        self.assertIn("udid", self.evidence["forbidden_fields"])
        self.assertIn("personal_data", self.evidence["forbidden_fields"])
        self.assertEqual(
            "physical-device/android",
            self.evidence["certification_scope_by_family"]["android"],
        )
        self.assertIn(
            "does-not-certify-simulator-or-emulator",
            self.evidence["required_limitations"],
        )

    def test_every_family_has_synthetic_and_consumer_profiles(self) -> None:
        profiles = self.profile["profiles"]
        for family in ("android", "ios", "tvos"):
            family_profiles = [
                value for value in profiles.values() if value["family"] == family
            ]
            self.assertTrue(any(not item["execution_allowed"] for item in family_profiles))
            self.assertTrue(any(item["execution_allowed"] for item in family_profiles))

    def test_fixed_command_profiles_have_prepare_test_evidence_cleanup(self) -> None:
        expected = {
            "prepare_script",
            "test_script",
            "evidence_script",
            "cleanup_script",
            "fixed_arguments",
            "live_backend_profile",
            "state_restoration",
        }
        for profile in self.profile["command_profiles"].values():
            self.assertEqual(expected, set(profile))
            for key in (
                "prepare_script",
                "test_script",
                "evidence_script",
                "cleanup_script",
            ):
                self.assertNotIn("..", profile[key])
                self.assertFalse(profile[key].startswith("/"))

    def test_documentation_records_authorization_and_ordered_blockers(self) -> None:
        for phrase in (
            "No live physical-device execution is authorized",
            "runner label is not a device lock",
            "raw serial or UDID",
            "canonical resource-fencing",
            "after Apple issue #13",
            "simulator or emulator evidence",
            "zero routine Actions artifacts",
        ):
            self.assertIn(phrase.casefold(), self.docs.casefold())

    def test_owned_file_manifest_is_exact_and_shared_registrations_absent(self) -> None:
        owned = {
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
            "tests/fixtures/device-validation/README.md",
            "tests/fixtures/device-validation/android.txt",
            "tests/fixtures/device-validation/android-offline.txt",
            "tests/fixtures/device-validation/ios.json",
            "tests/fixtures/device-validation/tvos.json",
            "tests/fixtures/device-validation/malformed.json",
            "tests/fixtures/device-validation/cases.json",
            "tests/fixtures/device-validation/scripts/prepare.sh",
            "tests/fixtures/device-validation/scripts/test.sh",
            "tests/fixtures/device-validation/scripts/evidence.sh",
            "tests/fixtures/device-validation/scripts/cleanup.sh",
            "tests/test_device_validation.py",
            "tests/test_device_workflow_contract.py",
            "docs/workflows/devices.md",
            "docs/architecture/device-validation.md",
        }
        self.assertTrue(all((ROOT / path).exists() for path in owned))
        for deferred in (
            "contracts/public-workflows.json",
            "contracts/ciw-commands.json",
            "contracts/runner-profiles.json",
            "generated/runner-mappings.json",
            "src/ci_workflows/ciw.py",
        ):
            self.assertNotIn(deferred, owned)


if __name__ == "__main__":
    unittest.main()
