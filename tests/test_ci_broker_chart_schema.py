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

    def test_default_top_level_values_are_all_declared_and_required(self) -> None:
        expected = set(self.values)
        self.assertEqual(set(self.schema["properties"]), expected)
        self.assertEqual(set(self.schema["required"]), expected)
        self.assertFalse(self.schema["additionalProperties"])

    def test_withdrawn_diagnostics_surface_cannot_be_configured(self) -> None:
        self.assertNotIn("diagnostics", self.values)
        self.assertNotIn("diagnostics", self.schema["properties"])
        self.assertNotIn("diagnostics", self.schema["required"])


if __name__ == "__main__":
    unittest.main()
