from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ci_workflows import helm
from ci_workflows.helm_contract import load_helm_contract, request_from_environment
from ci_workflows.helm_dependency_policy import (
    load_dependency_policy,
    resolve_validation_plan,
)
from ci_workflows.helm_types import HelmValidationError


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests/fixtures/helm-validation"
SHA = "a" * 40


def environment(product_id: str = "iptv-backend-chart") -> dict[str, str]:
    repositories = {
        "iptv-backend-chart": "StreamScapeTV/iptv-backend",
        "agent-state-chart": "StreamScapeTV/agent-state",
        "flux-github-actions-runner-chart": "StreamScapeTV/flux",
    }
    return {
        "GITHUB_REPOSITORY": repositories[product_id],
        "INPUT_ADMITTED_SHA": SHA,
        "INPUT_PRODUCT_ID": product_id,
        "INPUT_RELEASE_VERSION": "1.2.3",
        "INPUT_VALUES_PROFILE": "default",
        "INPUT_POLICY_PATH": "",
        "INPUT_ARTIFACT_EXCEPTION_ID": "",
        "INPUT_SOURCE_TRUST": "trusted-exact",
    }


class HelmDependencyPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_helm_contract(ROOT)
        cls.policy = load_dependency_policy(ROOT)

    def test_central_policy_binds_exact_current_dependency_sets(self) -> None:
        self.assertEqual(
            self.contract["dependency_policy_contract"],
            "contracts/helm-dependency-policy.json",
        )
        self.assertEqual(
            set(self.policy["products"]),
            {
                "iptv-backend-chart",
                "agent-state-chart",
                "flux-github-actions-runner-chart",
            },
        )
        self.assertEqual(
            self.policy["products"]["iptv-backend-chart"],
            [
                {
                    "name": "valkey",
                    "repository": "https://valkey.io/valkey-helm/",
                    "version": "0.11.0",
                }
            ],
        )
        self.assertEqual(self.policy["products"]["agent-state-chart"], [])
        self.assertEqual(
            self.policy["products"]["flux-github-actions-runner-chart"],
            [],
        )

    def test_backend_exact_declared_dependency_is_admitted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            shutil.copytree(FIXTURE_ROOT / "backend", source)
            plan = resolve_validation_plan(
                source,
                self.contract,
                request_from_environment(environment()),
                contract_root=ROOT,
            )
        self.assertEqual(
            plan.product.locked_dependencies,
            (
                (
                    "valkey",
                    "0.11.0",
                    "https://valkey.io/valkey-helm/",
                ),
            ),
        )

    def test_caller_cannot_redirect_dependency_network_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            shutil.copytree(FIXTURE_ROOT / "backend", source)
            manifest = source / ".streamscape/helm-product.json"
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["locked_dependencies"][0]["repository"] = (
                "https://attacker.example/charts/"
            )
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                HelmValidationError,
                "dependency_policy_mismatch",
            ):
                resolve_validation_plan(
                    source,
                    self.contract,
                    request_from_environment(environment()),
                    contract_root=ROOT,
                )

    def test_product_with_no_dependencies_cannot_add_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            shutil.copytree(FIXTURE_ROOT / "agent-state", source)
            manifest = source / ".streamscape/helm-product.json"
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["locked_dependencies"] = [
                {
                    "name": "external",
                    "repository": "https://attacker.example/charts/",
                    "version": "1.2.3",
                }
            ]
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                HelmValidationError,
                "product_layout_mismatch",
            ):
                resolve_validation_plan(
                    source,
                    self.contract,
                    request_from_environment(environment("agent-state-chart")),
                    contract_root=ROOT,
                )

    def test_policy_contract_rejects_credentials_and_unknown_products(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract_dir = root / "contracts"
            contract_dir.mkdir()
            bad = {
                "schema_version": 1,
                "products": {
                    "iptv-backend-chart": [
                        {
                            "name": "valkey",
                            "repository": "https://user:password@example.com/charts/",
                            "version": "0.11.0",
                        }
                    ],
                    "agent-state-chart": [],
                    "flux-github-actions-runner-chart": [],
                },
            }
            (contract_dir / "helm-dependency-policy.json").write_text(
                json.dumps(bad), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                HelmValidationError,
                "invalid_dependency_policy",
            ):
                load_dependency_policy(root)

            bad["products"]["unexpected-chart"] = []
            bad["products"]["iptv-backend-chart"] = []
            (contract_dir / "helm-dependency-policy.json").write_text(
                json.dumps(bad), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                HelmValidationError,
                "invalid_dependency_policy",
            ):
                load_dependency_policy(root)

    def test_public_planners_use_central_dependency_policy_wrapper(self) -> None:
        self.assertIs(helm.resolve_validation_plan, resolve_validation_plan)
        ciw_source = (ROOT / "src/ci_workflows/ciw_helm.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "from .helm_dependency_policy import resolve_validation_plan",
            ciw_source,
        )
        release_script = (ROOT / "scripts/ci/helm_release.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "from ci_workflows.helm_dependency_policy import resolve_validation_plan",
            release_script,
        )


if __name__ == "__main__":
    unittest.main()
