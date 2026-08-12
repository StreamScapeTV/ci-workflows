from __future__ import annotations

import importlib
import inspect
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MaintenanceOperationsFragmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fragment = json.loads((ROOT / "contracts/public-workflows/operations.json").read_text(encoding="utf-8"))
        cls.maintenance = json.loads((ROOT / "contracts/organization-maintenance.json").read_text(encoding="utf-8"))
        cls.rows = {row["api_name"]: row for row in cls.fragment["workflows"]}

    def test_all_issue_20_public_operations_are_implemented(self) -> None:
        self.assertEqual(set(self.rows), {"flux.reconcile", "maintenance.artifacts", "maintenance.branches", "maintenance.conformance", "maintenance.runner-retry"})
        self.assertTrue(all(row["status"] == "implemented" for row in self.rows.values()))

    def test_fragment_trust_outputs_and_caller_schedule_agree_with_maintenance_contract(self) -> None:
        mapping = {"maintenance.artifacts": "artifacts", "maintenance.branches": "branches", "maintenance.conformance": "conformance", "maintenance.runner-retry": "runner_retry", "flux.reconcile": "flux_reconcile"}
        for api_name, operation_name in mapping.items():
            row = self.rows[api_name]
            operation = self.maintenance["operations"][operation_name]
            self.assertEqual(row["trust_class"], operation["trust_class"])
            self.assertEqual(set(row["outputs"]), set(operation["outputs"]))
            for event in operation["trigger"]["events"]:
                self.assertIn(event, row["permitted_events"])
        self.assertIn("schedule", self.rows["maintenance.runner-retry"]["permitted_events"])

    def test_every_declared_component_resolves_to_a_real_function(self) -> None:
        for row in self.rows.values():
            for component in row["implementation_components"]:
                module_name, function_name = component.rsplit(".", 1)
                value = getattr(importlib.import_module(module_name), function_name)
                self.assertTrue(inspect.isfunction(value), component)
                self.assertEqual(value.__module__, module_name)


if __name__ == "__main__":
    unittest.main()
