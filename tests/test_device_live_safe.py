from __future__ import annotations

import unittest

from ci_workflows.device_contract import build_plan, load_device_contract, request_from_environment
from ci_workflows.device_execution import product_environment
from ci_workflows.device_types import DeviceFamily, SelectedDevice
from device_test_support import ROOT, real_environment


class LiveDeviceSafetyTests(unittest.TestCase):
    def test_product_environment_is_secret_minimized_and_merges_only_validated_caller_env(self) -> None:
        contract = load_device_contract(ROOT)
        environment = real_environment(
            repository="StreamScapeTV/streamscape-media",
            family="android",
            capability="vlc",
            host_capacity="apple",
            caller_environment={"STREAMSCAPE_VLC_PACKET": "all"},
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
                "UNRELATED_SECRET": "should-not-pass",
                "PATH": "/usr/bin",
                "HOME": "/runner/home",
            }
        )
        plan = build_plan(contract, request_from_environment(environment, contract))
        selected = SelectedDevice(
            identity_hash="a" * 64,
            family=DeviceFamily.ANDROID,
            model_class="phone",
            connection_class="usb",
            os_or_api="api-37",
            capabilities=("vlc",),
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
            "UNRELATED_SECRET",
        ):
            self.assertNotIn(key, product)
        self.assertEqual("/usr/bin", product["PATH"])
        self.assertEqual("/runner/home", product["HOME"])
        self.assertEqual("all", product["STREAMSCAPE_VLC_PACKET"])
        self.assertEqual("apple", product["CIW_DEVICE_HOST_CAPACITY"])
        self.assertEqual("vlc", product["CIW_DEVICE_CAPABILITY"])
        self.assertEqual("private-device-identifier", product["CIW_DEVICE_IDENTIFIER"])
        self.assertEqual("private-device-identifier", product["ANDROID_SERIAL"])


if __name__ == "__main__":
    unittest.main()
