from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from ci_workflows.release_contract import (
    load_release_plans,
    resolve_release_plan,
    validate_release_version,
)
from ci_workflows.release_types import ReleaseError


ROOT = Path(__file__).resolve().parents[1]


class ReleaseContractTest(unittest.TestCase):
    def test_inventory_resolves_exact_current_release_shapes(self) -> None:
        plans = load_release_plans(ROOT)
        self.assertEqual(
            ["agent-state", "flux-runner-assets", "iptv-backend"],
            sorted(plans),
        )
        self.assertEqual(
            ("iptv-backend-image", "iptv-backend-chart"),
            plans["iptv-backend"].product_ids,
        )
        self.assertEqual("StreamScapeTV/flux", plans["flux-runner-assets"].repository)
        for plan in plans.values():
            self.assertTrue(plan.chart_requires_image_identity)
            self.assertTrue(plan.github_release)
            self.assertEqual("flux-selection-request", plan.handoff_kind)
            self.assertEqual("StreamScapeTV/flux", plan.handoff_target_repository)
            self.assertEqual("review-selection", plan.handoff_requested_action)

    def test_repository_cannot_be_redirected_by_caller(self) -> None:
        with self.assertRaisesRegex(ReleaseError, r"^repository_rejected$"):
            resolve_release_plan(
                ROOT,
                "iptv-backend",
                "StreamScapeTV/agent-state",
            )

    def test_unknown_release_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(ReleaseError, r"^release_id_rejected$"):
            resolve_release_plan(ROOT, "invented-release", "StreamScapeTV/iptv-backend")

    def test_release_version_is_stable_semver_without_v_prefix(self) -> None:
        self.assertEqual("1.2.3", validate_release_version("1.2.3"))
        for invalid in (
            "v1.2.3",
            "01.2.3",
            "1.2",
            "latest",
            "1.2.3 latest",
            "1.2.3-rc.1",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ReleaseError):
                    validate_release_version(invalid)

    def test_contract_fails_closed_if_product_repository_drifts(self) -> None:
        releases = json.loads((ROOT / "contracts/releases.json").read_text(encoding="utf-8"))
        products = json.loads((ROOT / "contracts/products.json").read_text(encoding="utf-8"))
        for product in products["products"]:
            if product["id"] == "iptv-backend-chart":
                product["repository"] = "StreamScapeTV/agent-state"
                break
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contracts = root / "contracts"
            contracts.mkdir()
            (contracts / "releases.json").write_text(
                json.dumps(releases), encoding="utf-8"
            )
            (contracts / "products.json").write_text(
                json.dumps(products), encoding="utf-8"
            )
            with self.assertRaisesRegex(ReleaseError, r"^release_contract_invalid$"):
                load_release_plans(root)

    def test_contract_rejects_mutable_or_deployment_handoff_policy(self) -> None:
        releases = json.loads((ROOT / "contracts/releases.json").read_text(encoding="utf-8"))
        products = json.loads((ROOT / "contracts/products.json").read_text(encoding="utf-8"))
        releases["releases"][0]["handoff"]["mutation_authorized"] = True
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contracts = root / "contracts"
            contracts.mkdir()
            (contracts / "releases.json").write_text(json.dumps(releases), encoding="utf-8")
            (contracts / "products.json").write_text(json.dumps(products), encoding="utf-8")
            with self.assertRaisesRegex(ReleaseError, r"^release_contract_invalid$"):
                load_release_plans(root)


if __name__ == "__main__":
    unittest.main()
