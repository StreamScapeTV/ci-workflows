from __future__ import annotations

import json
import unittest
from pathlib import Path

from ci_workflows.device_contract import load_device_contract
from device_test_support import FIX, ROOT, synthetic_environment


class FixtureCompletenessTests(unittest.TestCase):
    def test_descriptive_fixture_inventory_is_exact_and_consumed_by_tests(self) -> None:
        cases = json.loads((FIX / "cases.json").read_text())
        data_files = set(cases["positive"]) | set(cases["negative"])
        actual_data = {
            path.name
            for path in FIX.iterdir()
            if path.is_file() and path.name not in {"README.md", "cases.json"}
        }
        self.assertEqual(data_files, actual_data)
        self.assertFalse(any(name.startswith("placeholder") for name in actual_data))
        self.assertNotIn(".checkpoint", actual_data)
        source = "\n".join(path.read_text() for path in ROOT.glob("tests/test_device_*.py"))
        for name in data_files:
            self.assertIn(name, source)

    def test_synthetic_stage_scripts_are_caller_owned_not_central_profiles(self) -> None:
        contract = load_device_contract(ROOT)
        serialized = json.dumps(contract, sort_keys=True)
        self.assertNotIn("command_profiles", contract)
        self.assertNotIn("profiles", contract)
        for script in ("prepare.sh", "test.sh", "evidence.sh", "cleanup.sh"):
            path = FIX / "scripts" / script
            self.assertTrue(path.is_file())
            relative = str(path.relative_to(ROOT))
            self.assertNotIn(relative, serialized)
            self.assertIn(relative, json.dumps(synthetic_environment(), sort_keys=True))


if __name__ == "__main__":
    unittest.main()
