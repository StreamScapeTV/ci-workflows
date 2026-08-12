from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/flux-infrastructure-products.json"
FIXTURE = ROOT / "tests/fixtures/flux-infrastructure-assets/dependency-evidence.json"


class FluxHelmDependencyAlignmentTests(unittest.TestCase):
    def test_helm_dependencies_use_canonical_chart_product(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        for api in ("helm.validate", "helm.publish"):
            self.assertEqual(
                contract["dependency_interfaces"][api]["product_id"],
                "flux-github-actions-runner-chart",
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


if __name__ == "__main__":
    unittest.main()
