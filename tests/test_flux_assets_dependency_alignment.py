from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ci_workflows.flux_assets import FluxAssetError
from ci_workflows.flux_assets_source import (
    validate_dependency_product_inventory,
    validate_oci_build_dependency_evidence,
    validate_runtime_repository,
    validate_source_contract_strict,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/flux-infrastructure-products.json"
SCHEMA = ROOT / "contracts/flux-infrastructure-products.schema.json"
PRODUCTS = ROOT / "contracts/products.json"
OCI_PRODUCTS = ROOT / "contracts/oci-products.json"
FIXTURE = ROOT / "tests/fixtures/flux-infrastructure-assets/dependency-evidence.json"
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
D3 = "sha256:" + "3" * 64
D4 = "sha256:" + "4" * 64


def _merged_oci_build_evidence() -> dict[str, object]:
    return {
        "oci.build": {
            "result": "success",
            "image_digest": json.dumps(
                {"runner-buildah": D1, "runner-mobile": D2}, sort_keys=True
            ),
            "platform_digests_json": json.dumps(
                {
                    "runner-buildah": [
                        {
                            "platform": "linux/amd64",
                            "manifest_digest": D1,
                            "config_digest": D3,
                            "layer_digests": [D4],
                        }
                    ],
                    "runner-mobile": [
                        {
                            "platform": "linux/amd64",
                            "manifest_digest": D2,
                            "config_digest": D3,
                            "layer_digests": [D4],
                        }
                    ],
                },
                sort_keys=True,
            ),
            "artifact_exception_used": "false",
        }
    }


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

    def test_runtime_repository_allows_only_central_self_test_or_flux(self) -> None:
        self.assertEqual(
            validate_runtime_repository("StreamScapeTV/ci-workflows"),
            "StreamScapeTV/ci-workflows",
        )
        self.assertEqual(
            validate_runtime_repository("StreamScapeTV/flux"),
            "StreamScapeTV/flux",
        )
        with self.assertRaisesRegex(FluxAssetError, "caller_repository_forbidden"):
            validate_runtime_repository("StreamScapeTV/iptv-backend")

    def test_dependency_products_must_be_current_in_merged_inventory(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        resolved = validate_dependency_product_inventory(
            contract, products_path=PRODUCTS
        )
        self.assertEqual(
            resolved,
            {
                "helm.publish": "flux-runner-chart-assets",
                "helm.validate": "flux-runner-chart-assets",
                "oci.build": "flux-runner-images",
                "oci.publish": "flux-runner-images",
            },
        )

        branch_private = json.loads(json.dumps(contract))
        branch_private["dependency_interfaces"]["helm.publish"]["product_id"] = (
            "flux-github-actions-runner-chart"
        )
        with self.assertRaisesRegex(FluxAssetError, "dependency_product_unregistered"):
            validate_dependency_product_inventory(
                branch_private, products_path=PRODUCTS
            )

    def test_dependency_api_cannot_select_current_product_of_wrong_kind(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        wrong_kind = json.loads(json.dumps(contract))
        wrong_kind["dependency_interfaces"]["oci.publish"]["product_id"] = (
            "flux-runner-chart-assets"
        )
        with self.assertRaisesRegex(FluxAssetError, "dependency_product_kind_mismatch"):
            validate_dependency_product_inventory(
                wrong_kind, products_path=PRODUCTS
            )

    def test_oci_build_evidence_matches_merged_public_shape(self) -> None:
        normalized = validate_oci_build_dependency_evidence(
            _merged_oci_build_evidence(), oci_products_path=OCI_PRODUCTS
        )
        assert normalized is not None
        self.assertEqual(
            set(normalized["image_digest"]), {"runner-buildah", "runner-mobile"}
        )
        self.assertEqual(
            normalized["platform_digests_json"]["runner-buildah"][0]["platform"],
            "linux/amd64",
        )

        old_synthetic = _merged_oci_build_evidence()
        old_synthetic["oci.build"]["platform_digests_json"] = json.dumps(
            {
                "runner-buildah": {"linux/amd64": {"manifest_digest": D1}},
                "runner-mobile": {"linux/amd64": {"manifest_digest": D2}},
            },
            sort_keys=True,
        )
        with self.assertRaisesRegex(FluxAssetError, "oci_build_evidence_invalid"):
            validate_oci_build_dependency_evidence(
                old_synthetic, oci_products_path=OCI_PRODUCTS
            )

    def test_runner_dockerfiles_are_no_follow_inside_admitted_source(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as external:
            root = Path(temporary)
            outside = Path(external) / "Dockerfile"
            outside.write_text(f"FROM registry.invalid/base@{D1}\n", encoding="utf-8")
            members = contract["products"]["flux-runner-images"]["members"]
            for member in members:
                dockerfile = root / member["dockerfile_path"]
                dockerfile.parent.mkdir(parents=True, exist_ok=True)
                dockerfile.write_text(
                    f"FROM registry.invalid/base@{D1}\n", encoding="utf-8"
                )
            result = validate_source_contract_strict(
                contract, product_id="flux-runner-images", source_root=root
            )
            self.assertEqual(result["kind"], "runner-image-family")

            escaped = root / members[0]["dockerfile_path"]
            escaped.unlink()
            escaped.symlink_to(outside)
            with self.assertRaisesRegex(FluxAssetError, "source_path_escape"):
                validate_source_contract_strict(
                    contract, product_id="flux-runner-images", source_root=root
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
