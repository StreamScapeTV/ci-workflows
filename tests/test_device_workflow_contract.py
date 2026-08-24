from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEVICE_ACTION_SHA = "4a6c73fd7bf901c2db6b19330ba0b879bc2bb3ae"
DEVICE_ACTION_RELEASE = "issue #481 semantic authorization receipt transport checkpoint"
DEVICE_LOCK_ACTION_SHA = "599c82201e6da6ca51c4f6247f1526a4ba03d550"
FOUNDATION_ACTION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"


class DeviceWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = json.loads((ROOT / "contracts/device-profiles.json").read_text())
        self.workflow = (ROOT / ".github/workflows/reusable-device.yml").read_text()
        self.smoke = (ROOT / ".github/workflows/device-validation-contract-smoke.yml").read_text()
        self.transport = (
            ROOT / ".github/workflows/internal-device-authorization-transport.yml"
        ).read_text()
        self.action = (ROOT / "actions/validate-device/action.yml").read_text()
        self.docs = (
            (ROOT / "docs/workflows/devices.md").read_text()
            + "\n"
            + (ROOT / "docs/architecture/device-validation.md").read_text()
        )

    def test_public_api_is_product_neutral_and_matches_contract(self) -> None:
        inputs = self.workflow.split("inputs:", 1)[1].split("secrets:", 1)[0]
        actual = set(re.findall(r"^      ([a-z_]+):$", inputs, re.M))
        self.assertEqual(set(self.contract["public_inputs"]), actual)
        self.assertEqual(
            {
                "admitted_sha", "device_family", "device_capability", "host_capacity",
                "prepare_script_path", "test_script_path", "evidence_script_path",
                "cleanup_script_path", "arguments_json", "environment_json",
                "max_duration_minutes", "evidence_exception_id", "request_id",
            },
            actual,
        )
        for retired in (
            "device_alias", "command_profile", "script_path", "source_trust",
            "device_identifier", "serial", "udid", "runner_labels", "runs_on",
        ):
            self.assertNotRegex(inputs, rf"^      {retired}:$", msg=retired)

    def test_shared_public_registration_matches_device_v2(self) -> None:
        index = json.loads((ROOT / "contracts/public-workflows.json").read_text())
        validation = json.loads((ROOT / "contracts/public-workflows/validation.json").read_text())
        permissions = json.loads((ROOT / "contracts/permission-profiles.json").read_text())
        indexed = next(row for row in index["workflows"] if row["api_name"] == "validation.device")
        registered = next(row for row in validation["workflows"] if row["api_name"] == "validation.device")
        profile = next(row for row in permissions["profiles"] if row["id"] == "device-validation")
        self.assertEqual("2.0.0", indexed["api_version"])
        self.assertEqual("2.0.0", registered["api_version"])
        self.assertEqual(set(self.contract["public_inputs"]), {item["name"] for item in registered["inputs"]})
        self.assertEqual(["device_authorization_receipt"], registered["secrets"])
        self.assertEqual(["device_authorization_receipt"], profile["named_secrets_allowed"])
        self.assertEqual(
            {"prepare_script_path", "test_script_path", "evidence_script_path", "cleanup_script_path"},
            set(registered["repository_owned_hooks"]),
        )

    def test_only_authorization_receipt_is_public_secret(self) -> None:
        secrets = self.workflow.split("secrets:", 1)[1].split("outputs:", 1)[0]
        actual = set(re.findall(r"^      ([a-z_]+):$", secrets, re.M))
        self.assertEqual({"device_authorization_receipt"}, actual)
        self.assertEqual(["device_authorization_receipt"], self.contract["public_secrets"])
        self.assertNotIn("live_test_credentials", self.workflow + self.action)

    def test_no_product_or_repository_identity_is_central_selection_authority(self) -> None:
        text = (self.contract.__repr__() + self.workflow + self.action + self.transport).casefold()
        for forbidden in ("iptv-android", "iptv-apple", "streamscape-media", "vlc"):
            self.assertNotIn(forbidden, text)
        self.assertNotIn("profiles", self.contract)
        self.assertNotIn("command_profiles", self.contract)
        self.assertNotIn("live_backend_profiles", self.contract)

    def test_action_has_typed_plan_and_no_caller_authority_inputs(self) -> None:
        input_block = self.action.split("inputs:", 1)[1].split("outputs:", 1)[0]
        for forbidden in (
            "\n  source_trust:", "\n  device_identifier:", "\n  serial:", "\n  udid:",
            "\n  device_alias:", "\n  command_profile:", "\n  runner_labels:", "\n  runs_on:",
        ):
            self.assertNotIn(forbidden, input_block)
        for required in (
            "host_capacity:", "prepare_script_path:", "test_script_path:",
            "evidence_script_path:", "cleanup_script_path:", "arguments_json:",
            "environment_json:", "validated_plan:", "validated_plan_sha256:",
            "selected_device_hash:", "resource_lock_receipt:",
        ):
            self.assertIn(required, input_block)

    def test_all_private_actions_are_immutable(self) -> None:
        refs = re.findall(
            r"uses: StreamScapeTV/ci-workflows/([^@\s]+)@([0-9a-f]{40})",
            self.workflow + "\n" + self.smoke + "\n" + self.transport,
        )
        self.assertTrue(refs)
        self.assertTrue(all(len(sha) == 40 for _path, sha in refs))
        validate_refs = {sha for path, sha in refs if path == "actions/validate-device"}
        self.assertEqual({DEVICE_ACTION_SHA}, validate_refs)
        action_lock = json.loads((ROOT / "contracts/action-tool-lock.json").read_text())
        validate_lock = next(
            row
            for row in action_lock["third_party_actions"]
            if row["uses"] == "StreamScapeTV/ci-workflows/actions/validate-device"
        )
        self.assertEqual(
            {
                "uses": "StreamScapeTV/ci-workflows/actions/validate-device",
                "sha": DEVICE_ACTION_SHA,
                "release": DEVICE_ACTION_RELEASE,
                "runtime": "composite",
                "source": f"https://github.com/StreamScapeTV/ci-workflows/tree/{DEVICE_ACTION_SHA}/actions/validate-device",
            },
            validate_lock,
        )
        self.assertIn(
            f"actions/device-lock@{DEVICE_LOCK_ACTION_SHA}",
            {f"{path}@{sha}" for path, sha in refs},
        )
        for action in ("exact-checkout", "prepare-workspace", "cleanup-workspace"):
            self.assertIn(
                f"actions/{action}@{FOUNDATION_ACTION_SHA}",
                {f"{path}@{sha}" for path, sha in refs},
            )

    def test_planner_selects_semantic_host_and_executor_consumes_typed_plan(self) -> None:
        self.assertIn("host_capacity: ${{ inputs.host_capacity }}", self.workflow)
        self.assertIn("runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}", self.workflow)
        self.assertIn("validated_plan: ${{ needs.plan.outputs.validated_plan }}", self.workflow)
        self.assertIn("validated_plan_sha256: ${{ needs.plan.outputs.validated_plan_sha256 }}", self.workflow)
        self.assertNotIn("runs-on: self-hosted", self.workflow)
        self.assertNotIn("runs-on: physical-device", self.workflow)

    def test_real_execution_fails_closed_without_authorization(self) -> None:
        self.assertIn("authorization_denied:", self.workflow)
        self.assertIn("needs.plan.outputs.execution_authorized != 'true'", self.workflow)
        self.assertIn("needs.plan.outputs.execution_authorized == 'true'", self.workflow)
        self.assertIn("physical_authorization_required", self.workflow)
        self.assertFalse(self.contract["owner_authorization"]["runner_or_secret_is_authorization"])

    def test_same_repository_pr_is_an_allowed_exact_consumer_event(self) -> None:
        self.assertEqual(
            ["pull_request", "workflow_call", "workflow_dispatch"],
            self.contract["allowed_events"],
        )
        self.assertNotIn("pull_request_target", self.contract["allowed_events"])
        self.assertIn("CIW_DEVICE_HEAD_FORK", self.action)
        self.assertIn("CIW_DEVICE_HEAD_REPOSITORY", self.action)
        self.assertIn("CIW_DEVICE_EVENT_SHA", self.action)

    def test_lock_order_and_exactly_once_restore_are_explicit(self) -> None:
        device_job = self.workflow.split("  device:\n", 1)[1]
        names = [
            "Deterministically discover one eligible physical device",
            "Acquire production cross-run device fencing receipt",
            "Verify exact fencing receipt before mutation",
            "Execute only the exact typed plan emitted by the planner",
            "Restore caller-owned device state exactly once",
            "Expected-state release exact fencing receipt",
            "Verify production fencing receipt has no live residue",
        ]
        positions = [device_job.index(name) for name in names]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(1, device_job.count("Restore caller-owned device state exactly once"))
        self.assertIn("cancel-in-progress: false", device_job)
        self.assertNotIn(
            "concurrency_group:",
            self.workflow.split("inputs:", 1)[1].split("secrets:", 1)[0],
        )

    def test_zero_actions_cache_and_routine_artifacts(self) -> None:
        text = (self.workflow + self.smoke + self.transport).casefold()
        self.assertNotIn("actions/cache", text)
        self.assertNotIn("upload-artifact", text)
        self.assertNotIn("download-artifact", text)
        self.assertIn("Verify zero routine device artifacts", self.smoke)

    def test_public_smoke_covers_synthetic_families_and_authorized_pr_plan(self) -> None:
        self.assertIn("phase: synthetic", self.smoke)
        for family in ("android", "ios", "tvos"):
            self.assertIn(f"family: {family}", self.smoke)
        self.assertIn("Authorized same-repository PR device plan", self.smoke)
        self.assertIn("CIW_DEVICE_AUTHORIZATION_RECEIPT", self.smoke)
        self.assertIn("test \"$AUTHORIZED\" = true", self.smoke)
        self.assertIn("test \"$TRUST\" = trusted-exact", self.smoke)
        self.assertIn("Reusable secret transport / Authorized PR plan", self.smoke)
        self.assertIn("uses: ./.github/workflows/internal-device-authorization-transport.yml", self.smoke)
        self.assertIn("CIW_DEVICE_AUTHORIZATION_RECEIPT: ${{ secrets.device_authorization_receipt }}", self.transport)
        self.assertIn("issue-481-transport-contract", self.transport)
        direct = re.findall(r"^    runs-on: (.+)$", self.smoke, re.M)
        self.assertTrue(direct)
        self.assertTrue(all(value == "[ubuntu-latest]" for value in direct))
        self.assertNotIn("[linux, amd64, general, small]", self.smoke)

    def test_documentation_states_required_boundaries(self) -> None:
        text = self.docs.casefold()
        for phrase in (
            "semantic host capacity", "checked-in", "non-secret environment",
            "device_authorization_receipt", "raw device", "device-lock/1",
            "exactly once", "zero routine actions artifacts", "ordinary android",
            "same-repository pull request", "semantic json", "duplicate keys",
        ):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
