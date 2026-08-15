from __future__ import annotations

import unittest

from ci_workflows.device_contract import build_plan, load_device_contract, request_from_environment
from ci_workflows.device_execution import product_environment
from ci_workflows.device_types import DeviceFamily, SelectedDevice
from device_test_support import ROOT, real_environment


class LiveDeviceSafetyTests(unittest.TestCase):
    def test_product_environment_does_not_receive_central_authority_material(self) -> None:
        contract = load_device_contract(ROOT)
        environment = real_environment(
            repository="StreamScapeTV/iptv-android",
            family="android",
            capability="instrumentation",
            command_profile="iptv-android-device",
            script_path="build.sh",
            alias="acceptance-primary",
            secret=True,
        )
        environment.update(
            {
                "CIW_DEVICE_AUTHORIZATION_PRESENT": "true",
                "CIW_DEVICE_AUTHORIZATION_RECEIPT": "owner-receipt-secret",
                "CIW_DEVICE_LOCK_ROOT": "/private/device-lock-root",
                "CIW_LOCK_RESOURCE_RECEIPT": "lock-internal-secret",
                "INPUT_RESOURCE_LOCK_RECEIPT": "public-action-lock-secret",
                "CHECKOUT_TOKEN": "checkout-secret",
                "GITHUB_TOKEN": "github-secret",
                "PATH": "/usr/bin",
                "CIW_DEVICE_LIVE_TEST_CREDENTIALS": "reviewed-live-secret",
            }
        )
        plan = build_plan(contract, request_from_environment(environment, contract))
        selected = SelectedDevice(
            identity_hash="a" * 64,
            family=DeviceFamily.ANDROID,
            model_class="phone",
            connection_class="usb",
            os_or_api="api-37",
            capabilities=("instrumentation",),
            _raw_identifier="private-device-identifier",
        )

        product = product_environment(
            environment=environment,
            plan=plan,
            selected=selected,
        )

        for key in (
            "CIW_DEVICE_AUTHORIZATION_RECEIPT",
            "CIW_DEVICE_LOCK_ROOT",
            "CIW_LOCK_RESOURCE_RECEIPT",
            "INPUT_RESOURCE_LOCK_RECEIPT",
            "CHECKOUT_TOKEN",
            "GITHUB_TOKEN",
        ):
            self.assertNotIn(key, product)
        self.assertEqual("/usr/bin", product["PATH"])
        self.assertEqual(
            "reviewed-live-secret",
            product["CIW_DEVICE_LIVE_TEST_CREDENTIALS"],
        )
        self.assertEqual("private-device-identifier", product["ANDROID_SERIAL"])


if __name__ == "__main__":
    unittest.main()
