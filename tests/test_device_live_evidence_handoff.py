from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows.device_contract import build_plan, load_device_contract, request_from_environment
from ci_workflows.device_live_safe import execute_live_device
from ci_workflows.device_retained_evidence import inspect_retained_evidence
from ci_workflows.device_types import DeviceFamily, DeviceValidationError, SelectedDevice
from device_test_support import ROOT, real_environment

COMMAND_PROFILE = "streamscape-media-ios-central-device"
PREPARE_SCRIPT = "scripts/ci/prepare-ios-central-device.sh"
TEST_SCRIPT = "scripts/ci/run-ios-central-device-test.sh"
EVIDENCE_SCRIPT = "scripts/ci/publish-ios-central-device-evidence.sh"
CLEANUP_SCRIPT = "scripts/ci/cleanup-ios-central-device.sh"
RETAINED_PATH = ".tmp/ci-retained/ios-central-device-evidence.json"


class LiveDeviceEvidenceHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_device_contract(ROOT)
        environment = real_environment(
            repository="StreamScapeTV/streamscape-media",
            family="ios",
            capability="native-video-output-frame-rates",
            command_profile=COMMAND_PROFILE,
            script_path=TEST_SCRIPT,
            alias="media-primary",
        )
        environment["INPUT_REQUEST_ID"] = "issue-527-media-ios-packet"
        environment["CIW_DEVICE_AUTHORIZATION_PRESENT"] = "true"
        self.environment = environment
        self.plan = build_plan(
            self.contract,
            request_from_environment(environment, self.contract),
        )
        self.selected = SelectedDevice(
            identity_hash="a" * 64,
            family=DeviceFamily.IOS,
            model_class="iphone",
            connection_class="usb",
            os_or_api="26.4",
            capabilities=("native-video-output-frame-rates",),
            _raw_identifier="private-ios-device",
        )
        self.inventory = {
            "name": "ios-central-device-evidence.json",
            "media_type": "application/json",
            "bytes": 128,
            "sha256": "b" * 64,
        }

    def execute_with_failures(self, *failure_scripts: str):
        calls: list[str] = []

        def run_stage(source_root, script, *, args, environment, timeout_seconds, failure_code):
            calls.append(script)
            if script in failure_scripts:
                raise DeviceValidationError(failure_code)

        with (
            patch(
                "ci_workflows.device_live_safe.load_selected_device",
                return_value=self.selected,
            ),
            patch("ci_workflows.device_live_safe.verify_production_lock"),
            patch(
                "ci_workflows.device_live_safe._run_product_stage",
                side_effect=run_stage,
            ),
            patch(
                "ci_workflows.device_live_safe.inspect_retained_evidence",
                return_value=self.inventory,
            ) as inspect,
        ):
            result = execute_live_device(
                contract_root=ROOT,
                plan=self.plan,
                source_root=Path("/source"),
                state_root=Path("/state"),
                selected_identity_hash=self.selected.identity_hash,
                authorization_receipt="owner-receipt",
                resource_lock_receipt="lock-receipt",
                environment=self.environment,
            )
        return result, calls, inspect

    def test_failed_test_still_runs_evidence_cleanup_and_retained_handoff(self) -> None:
        result, calls, inspect = self.execute_with_failures(TEST_SCRIPT)
        self.assertEqual(
            [PREPARE_SCRIPT, TEST_SCRIPT, EVIDENCE_SCRIPT, CLEANUP_SCRIPT],
            calls,
        )
        self.assertEqual("failure", result.result)
        self.assertEqual("stage_failed", result.failure_code)
        self.assertEqual("success", result.cleanup_result)
        self.assertEqual([self.inventory], result.evidence_packet["retained_evidence"])
        self.assertIn('"retained_evidence"', result.output_values()["test_summary"])
        inspect.assert_called_once_with(
            contract_root=ROOT,
            source_root=Path("/source"),
            relative_path=RETAINED_PATH,
            media_type="application/json",
        )

    def test_evidence_failure_never_masks_an_existing_test_failure(self) -> None:
        result, calls, inspect = self.execute_with_failures(TEST_SCRIPT, EVIDENCE_SCRIPT)
        self.assertEqual(
            [PREPARE_SCRIPT, TEST_SCRIPT, EVIDENCE_SCRIPT, CLEANUP_SCRIPT],
            calls,
        )
        self.assertEqual("failure", result.result)
        self.assertEqual("stage_failed", result.failure_code)
        inspect.assert_called_once()

    def test_evidence_failure_fails_an_otherwise_successful_test(self) -> None:
        result, calls, _ = self.execute_with_failures(EVIDENCE_SCRIPT)
        self.assertEqual(
            [PREPARE_SCRIPT, TEST_SCRIPT, EVIDENCE_SCRIPT, CLEANUP_SCRIPT],
            calls,
        )
        self.assertEqual("failure", result.result)
        self.assertEqual("evidence_policy_failed", result.failure_code)

    def test_prepare_failure_skips_test_and_evidence_but_cleanup_still_runs(self) -> None:
        result, calls, inspect = self.execute_with_failures(PREPARE_SCRIPT)
        self.assertEqual([PREPARE_SCRIPT, CLEANUP_SCRIPT], calls)
        self.assertEqual("failure", result.result)
        self.assertEqual("prepare_failed", result.failure_code)
        inspect.assert_not_called()

    def test_cleanup_failure_is_terminal_without_erasing_retained_metadata(self) -> None:
        result, calls, inspect = self.execute_with_failures(CLEANUP_SCRIPT)
        self.assertEqual(
            [PREPARE_SCRIPT, TEST_SCRIPT, EVIDENCE_SCRIPT, CLEANUP_SCRIPT],
            calls,
        )
        self.assertEqual("failure", result.result)
        self.assertEqual("cleanup_failed", result.failure_code)
        self.assertEqual("failure", result.cleanup_result)
        self.assertEqual([self.inventory], result.evidence_packet["retained_evidence"])
        inspect.assert_called_once()

    def test_retained_json_is_validated_and_only_inventory_is_returned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            target = source / RETAINED_PATH
            target.parent.mkdir(parents=True)
            document = {
                "schema": "streamscape-ios-physical-evidence-manifest-v1",
                "packetStatus": "failure",
                "configuredDeviceIdentifierSha256": "c" * 64,
            }
            payload = json.dumps(document, indent=2, sort_keys=True) + "\n"
            target.write_text(payload, encoding="utf-8")

            inventory = inspect_retained_evidence(
                contract_root=ROOT,
                source_root=source,
                relative_path=RETAINED_PATH,
                media_type="application/json",
            )

            self.assertEqual(target.name, inventory["name"])
            self.assertEqual("application/json", inventory["media_type"])
            self.assertEqual(len(payload.encode("utf-8")), inventory["bytes"])
            self.assertRegex(str(inventory["sha256"]), r"^[0-9a-f]{64}$")
            self.assertNotIn("content", inventory)
            self.assertNotIn(str(target.parent), json.dumps(inventory))

    def test_retained_handoff_rejects_symlink_escape_and_private_host_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            outside = root / "outside.json"
            outside.write_text("{}\n", encoding="utf-8")
            retained = source / ".tmp/ci-retained"
            retained.mkdir(parents=True)
            link = retained / "ios-central-device-evidence.json"
            link.symlink_to(outside)
            with self.assertRaises(DeviceValidationError) as caught:
                inspect_retained_evidence(
                    contract_root=ROOT,
                    source_root=source,
                    relative_path=RETAINED_PATH,
                    media_type="application/json",
                )
            self.assertEqual("evidence_policy_failed", caught.exception.code)

            link.unlink()
            link.write_text(
                json.dumps({"diagnostic": "/Users/private/device-state"}) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(DeviceValidationError) as caught:
                inspect_retained_evidence(
                    contract_root=ROOT,
                    source_root=source,
                    relative_path=RETAINED_PATH,
                    media_type="application/json",
                )
            self.assertEqual("evidence_policy_failed", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
