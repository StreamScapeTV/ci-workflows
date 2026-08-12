from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import flux


class FluxComponentFacadeTests(unittest.TestCase):
    def test_reviewed_public_component_names_are_real_functions(self) -> None:
        for name in ("resolve_request", "reconcile", "verify_health"):
            value = getattr(flux, name)
            self.assertTrue(inspect.isfunction(value), name)
            self.assertEqual(value.__module__, "ci_workflows.flux")

    def test_resolve_request_forwards_only_typed_named_arguments(self) -> None:
        sentinel = object()
        contract = object()
        with mock.patch("ci_workflows.flux._resolve_request", return_value=sentinel) as delegated:
            result = flux.resolve_request(
                contract,
                source_root=Path("source"),
                source_repository="StreamScapeTV/flux",
                admitted_sha="a" * 40,
                target_id="target",
                product_id="product",
                operation="deploy",
                policy_path="policy.py",
                allowlist_path="allowlist.json",
                request_id="request",
                state_root=Path("state"),
            )
        self.assertIs(result, sentinel)
        delegated.assert_called_once_with(
            contract,
            source_root=Path("source"),
            source_repository="StreamScapeTV/flux",
            admitted_sha="a" * 40,
            target_id="target",
            product_id="product",
            operation="deploy",
            policy_path="policy.py",
            allowlist_path="allowlist.json",
            request_id="request",
            state_root=Path("state"),
        )

    def test_reconcile_and_health_forward_without_shell_or_target_policy(self) -> None:
        contract = object()
        plan = object()
        with mock.patch("ci_workflows.flux._reconcile") as apply, mock.patch("ci_workflows.flux._verify_health") as health:
            flux.reconcile(
                contract,
                plan,
                source_root=Path("source"),
                state_root=Path("state"),
                flux_kubeconfig="kube",
                flux_sops_age_key="age",
            )
            flux.verify_health(plan)
        apply.assert_called_once_with(
            contract,
            plan,
            source_root=Path("source"),
            state_root=Path("state"),
            flux_kubeconfig="kube",
            flux_sops_age_key="age",
        )
        health.assert_called_once_with(plan)


if __name__ == "__main__":
    unittest.main()
