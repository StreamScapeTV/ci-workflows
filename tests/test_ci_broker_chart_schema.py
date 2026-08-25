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

    def test_diagnostics_defaults_match_strict_schema_contract(self) -> None:
        diagnostics = self.values["diagnostics"]
        schema = self.schema["properties"]["diagnostics"]

        self.assertTrue(diagnostics["enabled"])
        self.assertEqual(diagnostics["service"], {"type": "ClusterIP", "port": 8081})
        self.assertEqual(set(schema["required"]), {"enabled", "service", "resources"})
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["enabled"], {"type": "boolean"})

        service = schema["properties"]["service"]
        self.assertFalse(service["additionalProperties"])
        self.assertEqual(set(service["required"]), {"type", "port"})
        self.assertEqual(service["properties"]["type"]["const"], "ClusterIP")
        self.assertEqual(service["properties"]["port"]["const"], 8081)

        resources = schema["properties"]["resources"]
        self.assertFalse(resources["additionalProperties"])
        self.assertEqual(set(resources["required"]), {"requests", "limits"})
        for bucket in ("requests", "limits"):
            bounded = resources["properties"][bucket]
            self.assertFalse(bounded["additionalProperties"])
            self.assertEqual(set(bounded["required"]), {"cpu", "memory"})
            self.assertEqual(set(bounded["properties"]), {"cpu", "memory"})


if __name__ == "__main__":
    unittest.main()
