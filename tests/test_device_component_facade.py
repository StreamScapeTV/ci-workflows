from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import devices


class DeviceComponentFacadeTests(unittest.TestCase):
    def test_reviewed_component_names_are_real_public_functions(self) -> None:
        for name in ("lock", "validate", "cleanup"):
            with self.subTest(name=name):
                function = getattr(devices, name)
                self.assertTrue(callable(function))
                self.assertEqual(function.__module__, "ci_workflows.devices")

    def test_lock_forwards_only_typed_plan_and_selected_device(self) -> None:
        adapter = mock.Mock()
        receipt = object()
        adapter.acquire.return_value = receipt
        plan = object()
        selected = object()
        self.assertIs(
            devices.lock(adapter=adapter, plan=plan, selected=selected, now=123),
            receipt,
        )
        adapter.acquire.assert_called_once_with(plan=plan, selected=selected, now=123)

    def test_validate_delegates_to_restoration_first_lifecycle(self) -> None:
        result = object()
        values = {
            "plan": object(),
            "records": (object(),),
            "lock_adapter": object(),
            "runtime": object(),
            "evidence_contract": {"schema_version": 1},
            "now": lambda: 123,
            "synthetic_authorized": True,
        }
        with mock.patch(
            "ci_workflows.devices.execute_device_plan",
            return_value=result,
        ) as execute:
            self.assertIs(devices.validate(**values), result)
        execute.assert_called_once_with(**values)

    def test_cleanup_removes_registered_state_then_proves_zero_residue(self) -> None:
        root = Path("/synthetic/device-state")
        with mock.patch("ci_workflows.devices.cleanup_device_state") as cleanup, mock.patch(
            "ci_workflows.devices.assert_zero_device_residue"
        ) as residue:
            devices.cleanup(state_root=root)
        cleanup.assert_called_once_with(root)
        residue.assert_called_once_with(root)


if __name__ == "__main__":
    unittest.main()
