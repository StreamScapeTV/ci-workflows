from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ci_workflows.helm_contract import load_helm_contract, request_from_environment
from ci_workflows.helm_dependency_policy import resolve_validation_plan
from ci_workflows.helm_product_layout import load_product_layout
from ci_workflows.helm_types import HelmValidationError


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/helm-validation"
SHA = "a" * 40


def environment(product_id: str, repository: str) -> dict[str, str]:
    return {
        "GITHUB_REPOSITORY": repository,
        "INPUT_ADMITTED_SHA": SHA,
        "INPUT_PRODUCT_ID": product_id,
        "INPUT_RELEASE_VERSION": "1.2.3",
        "INPUT_VALUES_PROFILE": "default",
        "INPUT_POLICY_PATH": "",
        "INPUT_ARTIFACT_EXCEPTION_ID": "",
        "INPUT_SOURCE_TRUST": "trusted-exact",
    }


class HelmProductLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_helm_contract(ROOT)
        cls.layout = load_product_layout(ROOT)

    def test_current_roots_profiles_and_minimum_image_counts_are_central(self) -> None:
        self.assertEqual(
            self.layout["products"]["iptv-backend-chart"],
            {
                "chart_root": "charts/iptv-backend",
                "values_profiles": {"default": "values.yaml"},
                "minimum_required_image_references": 1,
            },
        )
        self.assertEqual(
            self.layout["products"]["agent-state-chart"],
            {
                "chart_root": "charts/agent-state",
                "values_profiles": {"default": "values.yaml"},
                "minimum_required_image_references": 1,
            },
        )
        self.assertEqual(
            self.layout["products"]["flux-github-actions-runner-chart"],
            {
                "chart_root": "apps/github-actions-runner",
                "values_profiles": {"default": "values.yaml"},
                "minimum_required_image_references": 0,
            },
        )

    def copied_backend(self) -> tempfile.TemporaryDirectory[str]:
        directory = tempfile.TemporaryDirectory()
        shutil.copytree(FIXTURES / "backend", Path(directory.name) / "source")
        return directory

    def resolve_backend(self, root: Path):
        return resolve_validation_plan(
            root / "source",
            self.contract,
            request_from_environment(
                environment("iptv-backend-chart", "StreamScapeTV/iptv-backend")
            ),
            contract_root=ROOT,
        )

    def test_exact_backend_layout_is_admitted(self) -> None:
        with self.copied_backend() as directory:
            plan = self.resolve_backend(Path(directory))
            self.assertEqual(plan.product.chart_root, "charts/iptv-backend")
            self.assertEqual(plan.product.values_profiles, {"default": "values.yaml"})

    def test_caller_cannot_redirect_validation_to_same_named_synthetic_chart(self) -> None:
        with self.copied_backend() as directory:
            root = Path(directory)
            manifest_path = root / "source/.streamscape/helm-product.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["chart_root"] = "test-fixtures/fake-backend"
            manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(
                HelmValidationError,
                "product_layout_mismatch",
            ):
                self.resolve_backend(root)

    def test_caller_cannot_replace_or_add_render_profiles(self) -> None:
        mutations = (
            {"default": "other-values.yaml"},
            {"default": "values.yaml", "minimal": "minimal.yaml"},
        )
        for profiles in mutations:
            with self.subTest(profiles=profiles), self.copied_backend() as directory:
                root = Path(directory)
                manifest_path = root / "source/.streamscape/helm-product.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["values_profiles"] = profiles
                manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
                with self.assertRaisesRegex(
                    HelmValidationError,
                    "product_layout_mismatch",
                ):
                    self.resolve_backend(root)

    def test_application_chart_cannot_drop_all_required_image_evidence(self) -> None:
        with self.copied_backend() as directory:
            root = Path(directory)
            manifest_path = root / "source/.streamscape/helm-product.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["required_image_references"] = []
            manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(
                HelmValidationError,
                "product_layout_mismatch",
            ):
                self.resolve_backend(root)

    def test_layout_contract_rejects_unexpected_product(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contracts = root / "contracts"
            contracts.mkdir()
            payload = json.loads(
                (ROOT / "contracts/helm-product-layout.json").read_text(encoding="utf-8")
            )
            payload["products"]["unexpected-chart"] = {
                "chart_root": "charts/unexpected",
                "values_profiles": {"default": "values.yaml"},
                "minimum_required_image_references": 0,
            }
            (contracts / "helm-product-layout.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            with self.assertRaisesRegex(HelmValidationError, "invalid_product_layout"):
                load_product_layout(root)


if __name__ == "__main__":
    unittest.main()
