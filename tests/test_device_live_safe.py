from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows.device_contract import build_plan, load_device_contract, request_from_environment
from ci_workflows.device_execution import cleanup_live_device, execute_live_device, product_environment
from ci_workflows.device_types import DeviceFamily, SelectedDevice
from device_test_support import ROOT, real_environment


class LiveDeviceSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_device_contract(ROOT)
        self.environment = real_environment(
            repository="ExampleCo/media-sdk",
            family="android",
            capability="playback",
            host_capacity="apple",
            prepare_script_path="scripts/ci/device-prepare.sh",
            test_script_path="scripts/ci/device-test.sh",
            evidence_script_path="scripts/ci/device-evidence.sh",
            cleanup_script_path="scripts/ci/device-cleanup.sh",
            arguments=("full",),
            caller_environment={"PACKET": "all"},
        )
        self.environment.update(
            {
                "CIW_DEVICE_AUTHORIZATION_PRESENT": "true",
                "CIW_DEVICE_AUTHORIZATION_RECEIPT": "owner-receipt-secret",
                "CIW_DEVICE_LOCK_ROOT": "/private/device-lock-root",
                "CIW_LOCK_RESOURCE_RECEIPT": "lock-internal-secret",
                "INPUT_RESOURCE_LOCK_RECEIPT": "public-action-lock-secret",
                "CHECKOUT_TOKEN": "checkout-secret",
                "GITHUB_TOKEN": "github-secret",
                "UNRELATED_SECRET": "should-not-pass",
                "PATH": "/usr/bin",
                "HOME": "/runner/home",
            }
        )
        self.plan = build_plan(
            self.contract,
            request_from_environment(self.environment, self.contract),
        )
        self.selected = SelectedDevice(
            identity_hash="a" * 64,
            family=DeviceFamily.ANDROID,
            model_class="phone",
            connection_class="usb",
            os_or_api="api-37",
            capabilities=("playback",),
            _raw_identifier="private-device-identifier",
        )

    def test_product_environment_is_secret_minimized_and_merges_only_validated_caller_env(self) -> None:
        product = product_environment(
            environment=self.environment,
            plan=self.plan,
            selected=self.selected,
        )
        for key in (
            "CIW_DEVICE_AUTHORIZATION_RECEIPT",
            "CIW_DEVICE_LOCK_ROOT",
            "CIW_LOCK_RESOURCE_RECEIPT",
            "INPUT_RESOURCE_LOCK_RECEIPT",
            "CHECKOUT_TOKEN",
            "GITHUB_TOKEN",
            "UNRELATED_SECRET",
        ):
            self.assertNotIn(key, product)
        self.assertEqual("/usr/bin", product["PATH"])
        self.assertEqual("/runner/home", product["HOME"])
        self.assertEqual("all", product["PACKET"])
        self.assertEqual("apple", product["CIW_DEVICE_HOST_CAPACITY"])
        self.assertEqual("playback", product["CIW_DEVICE_CAPABILITY"])
        self.assertEqual("private-device-identifier", product["CIW_DEVICE_IDENTIFIER"])
        self.assertEqual("private-device-identifier", product["ANDROID_SERIAL"])

    def test_live_execution_runs_cleanup_exactly_once_in_restoration_phase(self) -> None:
        with (
            patch("ci_workflows.device_live_safe.load_selected_device", return_value=self.selected),
            patch("ci_workflows.device_live_safe.verify_production_lock"),
            patch("ci_workflows.device_live_safe._run_product_stage") as run_stage,
        ):
            result = execute_live_device(
                contract_root=ROOT,
                plan=self.plan,
                source_root=Path("source"),
                state_root=Path("state"),
                selected_identity_hash=self.selected.identity_hash,
                authorization_receipt="receipt",
                resource_lock_receipt="lock",
                environment=self.environment,
            )
            self.assertEqual("success", result.result)
            self.assertEqual("deferred-to-restoration", result.cleanup_result)
            self.assertEqual(3, run_stage.call_count)
            self.assertEqual(
                [
                    "scripts/ci/device-prepare.sh",
                    "scripts/ci/device-test.sh",
                    "scripts/ci/device-evidence.sh",
                ],
                [call.args[1] for call in run_stage.call_args_list],
            )
            run_stage.reset_mock()
            cleanup_live_device(
                contract_root=ROOT,
                plan=self.plan,
                source_root=Path("source"),
                state_root=Path("state"),
                selected_identity_hash=self.selected.identity_hash,
                authorization_receipt="receipt",
                resource_lock_receipt="lock",
                environment=self.environment,
            )
            run_stage.assert_called_once()
            self.assertEqual("scripts/ci/device-cleanup.sh", run_stage.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
