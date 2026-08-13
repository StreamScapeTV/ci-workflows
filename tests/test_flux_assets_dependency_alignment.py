from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/flux-infrastructure-products.json"
SCHEMA = ROOT / "contracts/flux-infrastructure-products.schema.json"
PRODUCTS = ROOT / "contracts/products.json"
FIXTURE = ROOT / "tests/fixtures/flux-infrastructure-assets/dependency-evidence.json"


class FluxHelmDependencyAlignmentTests(unittest.TestCase):
    def test_helm_dependencies_use_current_merged_chart_product(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        products = json.loads(PRODUCTS.read_text(encoding="utf-8"))
        current_ids = {
            row["id"]
            for row in products["products"]
            if row["repository"] == "StreamScapeTV/flux" and row["status"] == "current"
        }
        expected = "flux-runner-chart-assets"
        self.assertIn(expected, current_ids)
        for api in ("helm.validate", "helm.publish"):
            self.assertEqual(
                contract["dependency_interfaces"][api]["product_id"],
                expected,
            )

    def test_arc_upstreams_use_apache_2_license_identity(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        product = contract["products"]["flux-runner-chart-assets"]
        self.assertEqual(
            {row["license"] for row in product["upstream_assets"]},
            {"Apache-2.0"},
        )
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(
            {row["license"] for row in fixture["chart_upstream"].values()},
            {"Apache-2.0"},
        )
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(
            schema["$defs"]["upstreamAsset"]["properties"]["license"]["const"],
            "Apache-2.0",
        )


if __name__ == "__main__":
    unittest.main()
