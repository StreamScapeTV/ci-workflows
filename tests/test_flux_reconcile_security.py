from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows.flux_reconcile import reconcile, resolve_request
from ci_workflows.maintenance_contract import MaintenanceError, load_contract
from tests.test_flux_reconcile_contract import initialize_source

ROOT = Path(__file__).resolve().parents[1]


class FluxReconcileSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_contract(ROOT)

    def resolve(self, source: Path, sha: str, state: Path):
        return resolve_request(
            self.contract,
            source_root=source,
            source_repository="StreamScapeTV/flux",
            admitted_sha=sha,
            target_id="synthetic-target",
            product_id="synthetic-product",
            operation="deploy",
            policy_path="scripts/rollout/validate_request.py",
            allowlist_path=".github/flux-rollout-allowlist.json",
            request_id="flux-security",
            state_root=state,
        )

    def test_state_symlink_is_rejected_without_following_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"; source.mkdir(); sha = initialize_source(source)
            outside = Path(directory) / "outside"; outside.mkdir(); sentinel = outside / "sentinel"; sentinel.write_text("preserve")
            state = Path(directory) / "state"; state.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(MaintenanceError, "flux_state_invalid"):
                self.resolve(source, sha, state)
            self.assertEqual(sentinel.read_text(), "preserve")
            self.assertTrue(state.is_symlink())

    def test_invalid_request_id_is_rejected_before_state_path_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner_temp = Path(directory) / "runner-temp"
            runner_temp.mkdir()
            # If the invalid id were interpolated before validation, this
            # prepared intermediate directory would let path traversal resolve
            # the cleanup target to runner_temp/outside.
            (runner_temp / "flux-reconcile-local-..").mkdir()
            outside = runner_temp / "outside"
            outside.mkdir()
            sentinel = outside / "sentinel"
            sentinel.write_text("preserve", encoding="utf-8")
            environment = os.environ.copy()
            environment["RUNNER_TEMP"] = str(runner_temp)
            environment.pop("GITHUB_RUN_ID", None)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/ci/flux_reconcile.py"),
                    "--source-root",
                    str(Path(directory) / "unused-source"),
                    "--source-repository",
                    "StreamScapeTV/flux",
                    "--admitted-sha",
                    "a" * 40,
                    "--target-id",
                    "synthetic-target",
                    "--product-id",
                    "synthetic-product",
                    "--operation",
                    "deploy",
                    "--policy-path",
                    "scripts/rollout/validate_request.py",
                    "--allowlist-path",
                    ".github/flux-rollout-allowlist.json",
                    "--request-id",
                    "../../outside",
                    "--dry-run",
                    "true",
                ],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn('"failure_code": "invalid_request_id"', completed.stdout)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_credential_symlink_is_rejected_without_writing_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"; source.mkdir(); sha = initialize_source(source)
            state = Path(directory) / "state"; plan = self.resolve(source, sha, state)
            outside = Path(directory) / "outside-secret"; outside.write_text("preserve")
            (state / "kubeconfig").symlink_to(outside)
            with mock.patch("ci_workflows.flux_reconcile_apply.shutil.which", return_value="/usr/bin/tool"):
                with self.assertRaisesRegex(MaintenanceError, "flux_state_invalid"):
                    reconcile(self.contract, plan, source_root=source, state_root=state, flux_kubeconfig="do-not-write", flux_sops_age_key="do-not-write")
            self.assertEqual(outside.read_text(), "preserve")

    def test_credentials_are_0600_during_fixed_executor_and_removed_after(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"; source.mkdir(); sha = initialize_source(source)
            state = Path(directory) / "state"; plan = self.resolve(source, sha, state)
            real_run = subprocess.run
            observed = []
            def fake_run(argv, **kwargs):
                if argv[0] == "git":
                    return real_run(argv, **kwargs)
                env = kwargs["env"]
                self.assertEqual(stat.S_IMODE(Path(env["KUBECONFIG"]).lstat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(Path(env["SOPS_AGE_KEY_FILE"]).lstat().st_mode), 0o600)
                observed.append(list(argv))
                return subprocess.CompletedProcess(argv, 0, "", "")
            with mock.patch("ci_workflows.flux_reconcile_apply.shutil.which", return_value="/usr/bin/tool"), mock.patch("ci_workflows.flux_reconcile_apply.subprocess.run", side_effect=fake_run):
                reconcile(self.contract, plan, source_root=source, state_root=state, flux_kubeconfig="apiVersion: v1\n", flux_sops_age_key="AGE-SECRET-KEY-TEST")
            self.assertEqual(len(observed), 1)
            self.assertFalse((state / "kubeconfig").exists())
            self.assertFalse((state / "sops-age-key").exists())


if __name__ == "__main__":
    unittest.main()
