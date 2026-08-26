from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
VALUES = ROOT / "charts/ci-broker/values.yaml"
VALUES_SCHEMA = ROOT / "charts/ci-broker/values.schema.json"


class BrokerChartSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
        cls.schema = json.loads(VALUES_SCHEMA.read_text(encoding="utf-8"))

    def test_default_top_level_values_are_declared_and_required(self) -> None:
        expected = set(self.values)
        self.assertTrue(expected.issubset(set(self.schema["properties"])))
        self.assertEqual(set(self.schema["required"]), expected)
        self.assertFalse(self.schema["additionalProperties"])

    def test_withdrawn_diagnostics_surface_is_only_a_false_noop_compatibility_key(self) -> None:
        self.assertNotIn("diagnostics", self.values)
        self.assertNotIn("diagnostics", self.schema["required"])
        diagnostics = self.schema["properties"]["diagnostics"]
        self.assertFalse(diagnostics["additionalProperties"])
        self.assertEqual(diagnostics["required"], ["enabled"])
        self.assertEqual(diagnostics["properties"]["enabled"]["const"], False)
        self.assertIn("permanently withdrawn", diagnostics["description"])


if __name__ == "__main__":
    unittest.main()
