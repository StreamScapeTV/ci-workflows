from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from ci_workflows.ciw_device import _runs_on_json
from ci_workflows.device_contract import (
    build_plan,
    load_device_contract,
    request_from_environment,
)
from device_test_support import ROOT, real_environment


class DeviceDeniedPlanTests(unittest.TestCase):
    def test_real_request_reaches_stable_authorization_denial_before_scheduling(self) -> None:
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
        plan = build_plan(contract, request_from_environment(environment, contract))
        self.assertFalse(plan.execution_authorized)
        self.assertEqual("physical_authorization_required", plan.authorization_failure)
        with patch.dict(os.environ, {"CIW_DEVICE_FOCUSED_TEST": "false"}, clear=False):
            self.assertEqual(
                ["linux", "amd64", "mobile"],
                json.loads(_runs_on_json(ROOT, plan)),
            )


if __name__ == "__main__":
    unittest.main()
