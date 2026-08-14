from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ci_workflows.ciw_maintenance import (
    execute_flux_reconcile,
    execute_maintenance_artifacts,
)
from ci_workflows.ciw_types import CIWContext
from ci_workflows.flux_reconcile_fs import remove_state
from ci_workflows.maintenance_contract import MaintenanceError
from ci_workflows.maintenance_core import OperationResult


class MaintenanceCiwTests(unittest.TestCase):
    def context(self, root: Path, environment: dict[str, str]) -> CIWContext:
        return CIWContext(
            root=root,
            environment=environment,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )

    def flux_args(self) -> argparse.Namespace:
        return argparse.Namespace(
            admitted_sha="0123456789abcdef0123456789abcdef01234567",
            target_id="target-a",
            product_id="product-a",
            operation="reconcile",
            policy_path="policy.json",
            allowlist_path="allowlist.json",
            request_id="request-123",
            dry_run=True,
        )

    def test_artifact_adapter_projects_existing_typed_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = self.context(
                root,
                {
                    "MAINTENANCE_GITHUB_TOKEN": "test-token",
                    "GITHUB_API_URL": "https://api.example.test",
                },
            )
            args = argparse.Namespace(
                repository_scope="",
                dry_run=True,
                request_id="request-123",
            )
            result_value = OperationResult(
                result="success",
                request_id="request-123",
                mutation_count=2,
            )
            with (
                patch("ci_workflows.ciw_maintenance.load_contract") as load_contract,
                patch("ci_workflows.ciw_maintenance.GitHubApi") as github_api,
                patch(
                    "ci_workflows.ciw_maintenance.artifacts",
                    return_value=result_value,
                ) as artifacts,
            ):
                result = execute_maintenance_artifacts(args, context)
            self.assertEqual((result.domain, result.operation), ("maintenance", "artifacts"))
            self.assertEqual(result.outputs["result"], "success")
            self.assertEqual(result.outputs["mutation_count"], "2")
            self.assertEqual(result.outputs["request_id"], "request-123")
            self.assertEqual(result.outputs["failure_code"], "")
            github_api.assert_called_once_with(
                "test-token",
                api_url="https://api.example.test",
            )
            artifacts.assert_called_once_with(
                load_contract.return_value,
                github_api.return_value,
                root=root,
                repository_scope="",
                dry_run=True,
                request_id="request-123",
            )
            self.assertNotIn("test-token", str(dict(result.outputs)))

    def test_artifact_adapter_projects_stable_failure_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "github-output"
            context = self.context(
                root,
                {
                    "MAINTENANCE_GITHUB_TOKEN": "test-token",
                    "GITHUB_OUTPUT": str(output),
                },
            )
            args = argparse.Namespace(
                repository_scope="",
                dry_run=True,
                request_id="request-123",
            )
            with (
                patch("ci_workflows.ciw_maintenance.load_contract"),
                patch("ci_workflows.ciw_maintenance.GitHubApi"),
                patch(
                    "ci_workflows.ciw_maintenance.artifacts",
                    side_effect=MaintenanceError("artifact_state_changed"),
                ),
                self.assertRaisesRegex(MaintenanceError, "artifact_state_changed"),
            ):
                execute_maintenance_artifacts(args, context)
            lines = set(output.read_text(encoding="utf-8").splitlines())
            self.assertIn("result=failure", lines)
            self.assertIn("mutation_count=0", lines)
            self.assertIn("request_id=request-123", lines)
            self.assertIn("failure_code=artifact_state_changed", lines)

    def test_flux_adapter_fixes_source_identity_and_dry_run_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            source = workspace / "source"
            runner_temp = root / "runner-temp"
            source.mkdir(parents=True)
            runner_temp.mkdir()
            context = self.context(
                root,
                {
                    "GITHUB_WORKSPACE": str(workspace),
                    "RUNNER_TEMP": str(runner_temp),
                    "GITHUB_RUN_ID": "12345",
                    "GITHUB_RUN_ATTEMPT": "2",
                    "FLUX_KUBECONFIG": "unused-dry-run",
                    "FLUX_SOPS_AGE_KEY": "unused-dry-run",
                },
            )
            contract = Mock()
            plan = object()
            with (
                patch(
                    "ci_workflows.ciw_maintenance.load_contract",
                    return_value=contract,
                ),
                patch(
                    "ci_workflows.ciw_maintenance.resolve_request",
                    return_value=plan,
                ) as resolve_request,
                patch(
                    "ci_workflows.ciw_maintenance.plan_summary",
                    return_value={"result": "success", "request_id": "request-123"},
                ),
                patch("ci_workflows.ciw_maintenance.reconcile") as reconcile,
            ):
                result = execute_flux_reconcile(self.flux_args(), context)
            contract.validate_request_id.assert_called_once_with("request-123")
            self.assertEqual(
                resolve_request.call_args.kwargs["source_repository"],
                "StreamScapeTV/flux",
            )
            self.assertEqual(resolve_request.call_args.kwargs["source_root"], source.resolve())
            self.assertEqual(
                resolve_request.call_args.kwargs["state_root"],
                runner_temp / "flux-reconcile-12345-2",
            )
            reconcile.assert_not_called()
            self.assertEqual(result.outputs["reconciliation_state"], "dry-run")
            self.assertEqual(result.outputs["failure_code"], "")
            self.assertFalse((runner_temp / "flux-reconcile-12345-2").exists())

    def test_flux_adapter_rejects_symlinked_source_without_following_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            target = root / "target"
            runner_temp = root / "runner-temp"
            workspace.mkdir()
            target.mkdir()
            runner_temp.mkdir()
            sentinel = target / "keep"
            sentinel.write_text("safe", encoding="utf-8")
            (workspace / "source").symlink_to(target, target_is_directory=True)
            context = self.context(
                root,
                {
                    "GITHUB_WORKSPACE": str(workspace),
                    "RUNNER_TEMP": str(runner_temp),
                    "GITHUB_RUN_ID": "12345",
                    "GITHUB_RUN_ATTEMPT": "2",
                },
            )
            with (
                patch("ci_workflows.ciw_maintenance.load_contract", return_value=Mock()),
                self.assertRaisesRegex(MaintenanceError, "flux_source_invalid"),
            ):
                execute_flux_reconcile(self.flux_args(), context)
            self.assertTrue(sentinel.is_file())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "safe")
            self.assertTrue((workspace / "source").is_symlink())
            self.assertFalse((runner_temp / "flux-reconcile-12345-2").exists())

    def test_remove_state_rejects_symlink_without_following_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            sentinel = target / "keep"
            sentinel.write_text("safe", encoding="utf-8")
            state = root / "state"
            state.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(MaintenanceError, "flux_state_invalid"):
                remove_state(state, fail_on_unsafe=True)
            self.assertFalse(state.exists())
            self.assertTrue(sentinel.is_file())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "safe")


if __name__ == "__main__":
    unittest.main()
