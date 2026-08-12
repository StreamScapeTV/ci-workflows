from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows.flux_reconcile import reconcile, resolve_request
from ci_workflows.maintenance_contract import MaintenanceError, load_contract

ROOT = Path(__file__).resolve().parents[1]
SHA = ""


POLICY = r'''#!/usr/bin/env python3
import argparse, json
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument("--central-interface", required=True)
p.add_argument("--central-request", type=Path, required=True)
p.add_argument("--allowlist", type=Path, required=True)
p.add_argument("--output-plan", type=Path, required=True)
a=p.parse_args()
if a.central_interface != "central-flux-policy-v1": raise SystemExit(2)
request=json.loads(a.central_request.read_text())
plan={"schema_version":1,"target_id":request["target_id"],"product_id":request["product_id"],"operation":request["operation"],"flux_source":{"kind":"oci","name":"synthetic-source","namespace":"synthetic"} if request["operation"]=="deploy" else None,"kustomization":None,"helm_release":None,"workloads":[{"kind":"deployment","name":"synthetic-app","namespace":"synthetic"}]}
if request["product_id"] == "command-field": plan["command"] = "kubectl get secrets"
a.output_plan.write_text(json.dumps(plan, sort_keys=True))
if request["product_id"] == "mutate-source": a.allowlist.write_text('{"mutated":true}\n')
'''

EXECUTOR = r'''#!/usr/bin/env python3
import argparse
p=argparse.ArgumentParser()
p.add_argument("--operation", required=True)
p.add_argument("--flux-source-kind", default="")
p.add_argument("--flux-source-name", default="")
p.add_argument("--flux-source-namespace", default="")
p.add_argument("--kustomization-name", default="")
p.add_argument("--kustomization-namespace", default="")
p.add_argument("--helm-release-name", default="")
p.add_argument("--helm-release-namespace", default="")
p.add_argument("--workloads-json", required=True)
p.parse_args()
'''


def initialize_source(root: Path) -> str:
    (root / "scripts/rollout").mkdir(parents=True)
    (root / ".github").mkdir()
    (root / "scripts/rollout/validate_request.py").write_text(POLICY, encoding="utf-8")
    (root / "scripts/rollout/execute_request.py").write_text(EXECUTOR, encoding="utf-8")
    (root / ".github/flux-rollout-allowlist.json").write_text('{"schema_version":99,"targets":{}}\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()


class FluxReconcileContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_contract(ROOT)

    def resolve(self, source: Path, sha: str, state: Path, *, product_id: str = "synthetic-product", operation: str = "deploy"):
        return resolve_request(
            self.contract,
            source_root=source,
            source_repository="StreamScapeTV/flux",
            admitted_sha=sha,
            target_id="synthetic-target",
            product_id=product_id,
            operation=operation,
            policy_path="scripts/rollout/validate_request.py",
            allowlist_path=".github/flux-rollout-allowlist.json",
            request_id="flux-test",
            state_root=state,
        )

    def test_exact_flux_owned_policy_produces_structured_plan_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"; source.mkdir(); sha = initialize_source(source)
            plan = self.resolve(source, sha, Path(directory) / "state")
            self.assertEqual(plan.admitted_sha, sha)
            self.assertEqual(plan.flux_source.name, "synthetic-source")
            self.assertEqual(plan.workloads[0].kind, "deployment")
            self.assertRegex(plan.policy_sha256, r"^[0-9a-f]{64}$")
            self.assertEqual(subprocess.run(["git", "status", "--porcelain"], cwd=source, check=True, text=True, stdout=subprocess.PIPE).stdout, "")

    def test_repository_path_operation_and_command_field_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"; source.mkdir(); sha = initialize_source(source); state = Path(directory) / "state"
            with self.assertRaisesRegex(MaintenanceError, "flux_repository_rejected"):
                resolve_request(self.contract, source_root=source, source_repository="StreamScapeTV/iptv-backend", admitted_sha=sha, target_id="synthetic-target", product_id="synthetic-product", operation="deploy", policy_path="scripts/rollout/validate_request.py", allowlist_path=".github/flux-rollout-allowlist.json", request_id="flux-repo", state_root=state)
            with self.assertRaisesRegex(MaintenanceError, "flux_policy_path_rejected"):
                resolve_request(self.contract, source_root=source, source_repository="StreamScapeTV/flux", admitted_sha=sha, target_id="synthetic-target", product_id="synthetic-product", operation="deploy", policy_path="scripts/rollout/other.py", allowlist_path=".github/flux-rollout-allowlist.json", request_id="flux-path", state_root=state)
            with self.assertRaisesRegex(MaintenanceError, "flux_operation_rejected"):
                resolve_request(self.contract, source_root=source, source_repository="StreamScapeTV/flux", admitted_sha=sha, target_id="synthetic-target", product_id="synthetic-product", operation="delete", policy_path="scripts/rollout/validate_request.py", allowlist_path=".github/flux-rollout-allowlist.json", request_id="flux-op", state_root=state)
            with self.assertRaisesRegex(MaintenanceError, "flux_policy_plan_invalid"):
                self.resolve(source, sha, state, product_id="command-field")

    def test_policy_source_mutation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"; source.mkdir(); sha = initialize_source(source)
            with self.assertRaisesRegex(MaintenanceError, "flux_source_mutated_by_policy"):
                self.resolve(source, sha, Path(directory) / "state", product_id="mutate-source")

    def test_apply_revalidates_hashes_uses_fixed_executor_and_cleans_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"; source.mkdir(); sha = initialize_source(source); state = Path(directory) / "state"
            plan = self.resolve(source, sha, state)
            real_run = subprocess.run; observed: list[list[str]] = []
            def fake_run(argv, **kwargs):
                if argv[0] == "git": return real_run(argv, **kwargs)
                observed.append(list(argv)); return subprocess.CompletedProcess(argv, 0, "", "")
            with mock.patch("ci_workflows.flux_reconcile.shutil.which", return_value="/usr/bin/tool"), mock.patch("ci_workflows.flux_reconcile.subprocess.run", side_effect=fake_run):
                reconcile(self.contract, plan, source_root=source, state_root=state, flux_kubeconfig="apiVersion: v1\n", flux_sops_age_key="AGE-SECRET-KEY-TEST")
            self.assertEqual(len(observed), 1)
            command = observed[0]
            self.assertTrue(command[1].endswith("scripts/rollout/execute_request.py"))
            self.assertIn("--operation", command); self.assertNotIn("shell", command)
            self.assertFalse((state / "kubeconfig").exists()); self.assertFalse((state / "sops-age-key").exists())

    def test_apply_detects_exact_flux_source_hash_drift_before_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"; source.mkdir(); sha = initialize_source(source); state = Path(directory) / "state"
            plan = self.resolve(source, sha, state)
            (source / "scripts/rollout/execute_request.py").write_text(EXECUTOR + "\n# drift\n", encoding="utf-8")
            with self.assertRaisesRegex(MaintenanceError, "flux_source_changed_before_apply"):
                reconcile(self.contract, plan, source_root=source, state_root=state, flux_kubeconfig="not-written", flux_sops_age_key="not-written")
            self.assertFalse((state / "kubeconfig").exists())


if __name__ == "__main__":
    unittest.main()
