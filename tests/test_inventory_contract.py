from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI_SCRIPTS = ROOT / "scripts" / "ci"
if str(CI_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(CI_SCRIPTS))

import inventory_contract  # noqa: E402
import inventory_live_check  # noqa: E402


class InventoryContractTests(unittest.TestCase):
    def test_checked_in_contract_is_complete_and_deterministic(self) -> None:
        data = inventory_contract.validate(ROOT)
        self.assertEqual(len(data["inventory"]["repositories"]), 11)
        self.assertEqual(data["workflow_total"], 88)
        self.assertGreater(data["counts"]["disposition"]["retire"], 0)
        self.assertGreater(data["counts"]["trust"]["publish"], 0)
        self.assertGreater(data["counts"]["trust"]["device"], 0)
        self.assertGreater(data["counts"]["trust"]["flux"], 0)
        self.assertEqual(
            data["paths"]["report"].read_text(encoding="utf-8"),
            inventory_contract.render(data),
        )

    def test_repository_and_product_boundaries_are_exact(self) -> None:
        data = inventory_contract.validate(ROOT)
        repositories = {
            row["repository"] for row in data["inventory"]["repositories"]
        }
        self.assertIn("StreamScapeTV/organization-rules", repositories)
        products = data["products"]["products"]
        oci = {
            product["repository"]
            for product in products
            if product["kind"] in {"oci-image", "oci-runner-image-family"}
        }
        charts = {
            product["repository"]
            for product in products
            if product["kind"] in {"helm-oci-chart", "helm-oci-chart-assets"}
        }
        self.assertEqual(oci, inventory_contract.OCI_PRODUCERS)
        self.assertEqual(charts, inventory_contract.OCI_PRODUCERS)

    def test_live_comparison_accepts_matching_paths_and_blobs(self) -> None:
        data = inventory_contract.validate(ROOT)
        live = {
            repository["repository"]: {
                workflow[0]: workflow[6] or ("a" * 40)
                for workflow in repository["workflows"]
            }
            for repository in data["inventory"]["repositories"]
        }
        self.assertEqual(
            inventory_live_check.compare_inventory(data["inventory"], live),
            [],
        )

    def test_live_comparison_reports_add_remove_and_changed_blob(self) -> None:
        data = inventory_contract.validate(ROOT)
        inventory = copy.deepcopy(data["inventory"])
        repository = inventory["repositories"][0]
        recorded = next(
            workflow
            for workflow in repository["workflows"]
            if workflow[6] is not None
        )
        live = {
            row["repository"]: {
                workflow[0]: workflow[6] or ("a" * 40)
                for workflow in row["workflows"]
            }
            for row in inventory["repositories"]
        }
        live[repository["repository"]][recorded[0]] = "b" * 40
        removed = repository["workflows"][-1][0]
        live[repository["repository"]].pop(removed)
        live[repository["repository"]][
            ".github/workflows/new-unregistered.yml"
        ] = "c" * 40
        errors = inventory_live_check.compare_inventory(inventory, live)
        self.assertTrue(any("workflow changed" in error for error in errors))
        self.assertTrue(any("workflow removed" in error for error in errors))
        self.assertTrue(any("workflow added" in error for error in errors))

    def test_contract_fails_closed_on_unknown_classification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (
                "contracts/consumers.json",
                "contracts/workflow-inventory.json",
                "contracts/products.json",
                "docs/architecture/ownership-boundaries.md",
                "docs/inventory/workflows.md",
            ):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, target)
            inventory_path = root / "contracts/workflow-inventory.json"
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            inventory["repositories"][0]["workflows"][0][3] = "unclassified"
            inventory_path.write_text(
                json.dumps(inventory, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(inventory_contract.ContractError):
                inventory_contract.validate(root)

    def test_live_client_requires_explicit_read_only_token(self) -> None:
        with self.assertRaises(inventory_contract.ContractError):
            inventory_live_check.GitHubClient("", "https://api.github.com")


if __name__ == "__main__":
    unittest.main()
