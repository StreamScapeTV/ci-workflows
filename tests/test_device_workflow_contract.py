from __future__ import annotations

import copy
import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from ci_workflows import ciw_device, device as device_validation, runners

ROOT = Path(__file__).resolve().parents[1]


class DeviceWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = json.loads((ROOT / "contracts/device-profiles.json").read_text())
        self.workflow = (ROOT / ".github/workflows/reusable-device.yml").read_text()
        self.smoke = (ROOT / ".github/workflows/device-validation-contract-smoke.yml").read_text()
        self.action = (ROOT / "actions/validate-device/action.yml").read_text()
        self.docs = (
            (ROOT / "docs/workflows/devices.md").read_text()
            + "\n"
            + (ROOT / "docs/architecture/device-validation.md").read_text()
        )
        self.runners_doc = (ROOT / "RUNNERS.md").read_text()

    @staticmethod
    def _plan(
        family: device_validation.DeviceFamily,
        host_capacity: str = "apple",
    ) -> SimpleNamespace:
        return SimpleNamespace(
            request=SimpleNamespace(
                family=family,
                host_capacity=host_capacity,
                source_trust="trusted-exact",
            )
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
        text = (self.contract.__repr__() + self.workflow + self.action + self.smoke).casefold()
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

    def test_first_party_actions_follow_main_without_component_versions(self) -> None:
        first_party = re.findall(
            r"uses: (StreamScapeTV/ci-workflows/actions/[^@\s]+)@([^\s]+)",
            self.workflow,
        )
        self.assertTrue(first_party)
        self.assertTrue(all(ref == "main" for _path, ref in first_party))
        refs = {f"{path}@{ref}" for path, ref in first_party}
        for required in (
            "StreamScapeTV/ci-workflows/actions/validate-device@main",
            "StreamScapeTV/ci-workflows/actions/device-lock@main",
            "StreamScapeTV/ci-workflows/actions/exact-checkout@main",
            "StreamScapeTV/ci-workflows/actions/prepare-workspace@main",
            "StreamScapeTV/ci-workflows/actions/cleanup-workspace@main",
        ):
            self.assertIn(required, refs)
        self.assertNotRegex(
            self.workflow,
            r"StreamScapeTV/ci-workflows/actions/[^\s@]+@[0-9a-f]{40}",
        )

    def test_planner_selects_semantic_host_and_executor_consumes_typed_plan(self) -> None:
        self.assertIn("host_capacity: ${{ inputs.host_capacity }}", self.workflow)
        self.assertIn("runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}", self.workflow)
        self.assertIn("validated_plan: ${{ needs.plan.outputs.validated_plan }}", self.workflow)
        self.assertIn("validated_plan_sha256: ${{ needs.plan.outputs.validated_plan_sha256 }}", self.workflow)
        self.assertNotIn("runs-on: self-hosted", self.workflow)
        self.assertNotIn("runs-on: physical-device", self.workflow)

    def test_physical_apple_contract_pins_organization_managed_capacity(self) -> None:
        self.assertEqual(
            {
                "apple": {
                    "families": ["ios", "tvos"],
                    "semantic_profile": "apple",
                    "capacity_owner": "organization-manual",
                    "lifecycle": "organization-managed-persistent-capacity",
                    "manual_capacity": True,
                    "exact_selector": ["macOS", "ARM64"],
                }
            },
            self.contract["physical_host_constraints"],
        )
        self.assertIn("execution_backend", self.contract["forbidden_inputs"])
        for family in ("ios", "tvos"):
            self.assertEqual(
                ["apple"],
                self.contract["family_policies"][family]["allowed_host_capacities"],
            )

    def test_github_hosted_backend_override_is_forbidden_before_request_parsing(self) -> None:
        with self.assertRaises(device_validation.DeviceValidationError) as raised:
            device_validation.request_from_environment(
                {"INPUT_EXECUTION_BACKEND": "github-hosted"},
                self.contract,
            )
        self.assertEqual("forbidden_input", raised.exception.code)

    def test_physical_apple_planner_emits_only_reviewed_organization_selector(self) -> None:
        runner_contract = runners.load_runner_contract(ROOT)
        for family in (
            device_validation.DeviceFamily.IOS,
            device_validation.DeviceFamily.TVOS,
        ):
            with self.subTest(family=family.value):
                self.assertEqual(
                    '["macOS","ARM64"]',
                    ciw_device._approved_base_runs_on_json(
                        runner_contract,
                        self.contract,
                        self._plan(family),
                    ),
                )

        self.assertEqual(
            '["linux","amd64","mobile"]',
            ciw_device._approved_base_runs_on_json(
                runner_contract,
                self.contract,
                self._plan(device_validation.DeviceFamily.ANDROID, "mobile"),
            ),
        )

    def test_physical_apple_planner_rejects_hosted_or_simulator_substitution(self) -> None:
        with self.assertRaises(ValueError):
            ciw_device._approved_base_runs_on_json(
                runners.load_runner_contract(ROOT),
                self.contract,
                self._plan(device_validation.DeviceFamily.IOS, "macos-latest"),
            )

        mutations = {
            "hosted-selector": lambda profile: profile.__setitem__(
                "default_internal_selector", ["macos-latest"]
            ),
            "hosted-owner": lambda profile: profile.__setitem__(
                "capacity_owner", "github-hosted"
            ),
            "hosted-lifecycle": lambda profile: profile.__setitem__(
                "lifecycle", "github-hosted-ephemeral"
            ),
            "no-manual-capacity": lambda profile: profile["privilege"].__setitem__(
                "manual_capacity", False
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                runner_contract = copy.deepcopy(runners.load_runner_contract(ROOT))
                profile = runners.profile_index(runner_contract)["apple"]
                mutate(profile)
                with self.assertRaises(ValueError):
                    ciw_device._approved_base_runs_on_json(
                        runner_contract,
                        self.contract,
                        self._plan(device_validation.DeviceFamily.IOS),
                    )

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

    def test_public_smoke_covers_synthetic_families_and_authorized_pr_plan(self) -> None:
        self.assertIn("phase: synthetic", self.smoke)
        for family in ("android", "ios", "tvos"):
            self.assertIn(f"family: {family}", self.smoke)
        self.assertIn("Authorized same-repository PR device plan", self.smoke)
        self.assertIn("CIW_DEVICE_AUTHORIZATION_RECEIPT", self.smoke)
        self.assertIn("test \"$AUTHORIZED\" = true", self.smoke)
        self.assertIn("test \"$TRUST\" = trusted-exact", self.smoke)
        self.assertIn("Reusable secret transport / Authorized PR plan", self.smoke)
        self.assertIn(
            "uses: StreamScapeTV/ci-workflows/.github/workflows/internal-device-authorization-transport.yml@main",
            self.smoke,
        )
        self.assertIn('device_authorization_receipt: >-', self.smoke)
        self.assertIn("issue-481-transport-contract", self.smoke)
        direct = re.findall(r"^    runs-on: (.+)$", self.smoke, re.M)
        self.assertTrue(direct)
        self.assertTrue(all(value == "[ubuntu-latest]" for value in direct))
        self.assertNotIn("[linux, amd64, general, small]", self.smoke)

    def test_documentation_states_required_boundaries(self) -> None:
        text = self.docs.casefold()
        for phrase in (
            "semantic host capacity", "checked-in", "non-secret environment",
            "device_authorization_receipt", "raw device", "device-lock/1",
            "exactly once", "ordinary android", "same-repository pull request",
            "semantic json", "duplicate keys",
        ):
            self.assertIn(phrase, text)

        runner_text = " ".join(self.runners_doc.casefold().split())
        for phrase in (
            "simulator-only apple work",
            "`macos-latest`",
            "does not imply physical-device authority",
            "exact `[macos, arm64]` selector",
            "fail closed before device mutation",
        ):
            self.assertIn(phrase, runner_text)


if __name__ == "__main__":
    unittest.main()