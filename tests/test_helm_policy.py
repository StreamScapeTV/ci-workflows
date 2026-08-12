from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows.helm_policy import (
    enforce_policy_hook,
    load_policy_hook_contract,
    run_policy_hook,
)
from ci_workflows.helm_types import HelmPlan, HelmProduct, HelmValidationError


ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def plan(policy_path: str | None) -> HelmPlan:
    product = HelmProduct(
        product_id="iptv-backend-chart",
        repository="StreamScapeTV/iptv-backend",
        chart_name="iptv-backend",
        chart_root="charts/iptv-backend",
        values_profiles={"default": "values.yaml"},
        policy_path=policy_path,
        registry_repository="oci://git.faruqi.dev/mimranfaruqi/helm-charts",
        locked_dependencies=(),
        required_image_references=(),
    )
    return HelmPlan(
        product=product,
        release_version="1.2.3",
        values_profile="default",
        values_path="values.yaml",
        policy_path=policy_path,
    )


class HelmPolicyHookTests(unittest.TestCase):
    def test_current_products_have_no_approved_hook(self) -> None:
        contract = load_policy_hook_contract(ROOT)
        self.assertEqual(
            contract["products"],
            {
                "agent-state-chart": None,
                "flux-github-actions-runner-chart": None,
                "iptv-backend-chart": None,
            },
        )

    def test_nonapproved_manifest_hook_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            manifest = source / ".streamscape/helm-product.json"
            manifest.parent.mkdir()
            manifest.write_text(
                json.dumps({"policy_path": "ci/helm-policy.sh"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                HelmValidationError,
                "policy_hook_policy_mismatch",
            ):
                enforce_policy_hook(
                    source,
                    "iptv-backend-chart",
                    load_policy_hook_contract(ROOT),
                )

    def test_future_centrally_approved_hook_requires_exact_file(self) -> None:
        policy = {
            "schema_version": 1,
            "products": {
                "agent-state-chart": None,
                "flux-github-actions-runner-chart": None,
                "iptv-backend-chart": "ci/helm-policy.sh",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            manifest = source / ".streamscape/helm-product.json"
            manifest.parent.mkdir()
            manifest.write_text(
                json.dumps({"policy_path": "ci/helm-policy.sh"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(HelmValidationError, "policy_hook_invalid"):
                enforce_policy_hook(source, "iptv-backend-chart", policy)
            hook = source / "ci/helm-policy.sh"
            hook.parent.mkdir()
            hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            enforce_policy_hook(source, "iptv-backend-chart", policy)

    def test_hook_execution_uses_fixed_bash_argv_and_scrubbed_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            hook = source / "ci/helm-policy.sh"
            hook.parent.mkdir()
            hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            state = root / "state"
            state.mkdir()
            work_chart = state / "helm-validation/work/iptv-backend"
            work_chart.mkdir(parents=True)
            (work_chart / "values.yaml").write_text("{}\n", encoding="utf-8")
            runtime = {
                "PATH": "/usr/bin:/bin",
                "HOME": str(root),
                "INPUT_REGISTRY_USERNAME": "secret-user",
                "INPUT_REGISTRY_TOKEN": "secret-token",
                "KUBECONFIG": "/must-not-propagate",
            }
            calls: list[tuple[list[str], dict[str, str]]] = []

            def fake_run(argv, *, environment, **kwargs):
                calls.append((list(argv), dict(environment)))
                from subprocess import CompletedProcess

                return CompletedProcess(list(argv), 0, "", "")

            with (
                patch("ci_workflows.helm_policy.verify_exact_source") as verify,
                patch("ci_workflows.helm_policy._run", side_effect=fake_run),
            ):
                count = run_policy_hook(
                    source,
                    state,
                    plan("ci/helm-policy.sh"),
                    SHA,
                    runtime,
                )
            self.assertEqual(count, 1)
            self.assertEqual(verify.call_count, 2)
            self.assertEqual(
                calls[0][0],
                ["bash", "--noprofile", "--norc", str(hook)],
            )
            execution_environment = calls[0][1]
            self.assertNotIn("INPUT_REGISTRY_USERNAME", execution_environment)
            self.assertNotIn("INPUT_REGISTRY_TOKEN", execution_environment)
            self.assertNotIn("KUBECONFIG", execution_environment)
            self.assertEqual(
                execution_environment["CIW_HELM_CHART_ROOT"],
                str(work_chart),
            )
            self.assertEqual(
                execution_environment["CIW_HELM_VALUES_PATH"],
                str(work_chart / "values.yaml"),
            )
            self.assertEqual(
                execution_environment["CIW_HELM_PRODUCT_ID"],
                "iptv-backend-chart",
            )

    def test_null_hook_is_noop(self) -> None:
        with patch("ci_workflows.helm_policy._run") as run:
            self.assertEqual(
                run_policy_hook(
                    Path("/unused"),
                    Path("/unused"),
                    plan(None),
                    SHA,
                    {},
                ),
                0,
            )
        run.assert_not_called()

    def test_all_production_validation_paths_route_policy_hook(self) -> None:
        for relative in (
            "src/ci_workflows/ciw_helm.py",
            "src/ci_workflows/helm.py",
            "scripts/ci/helm_release.py",
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("run_policy_hook", text, relative)


if __name__ == "__main__":
    unittest.main()
