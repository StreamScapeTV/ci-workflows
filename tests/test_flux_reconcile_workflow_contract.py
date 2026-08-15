from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
HELPER_SHA = "a464a023cf654d00172d62ebd1700c51ce2c75bb"
EXACT_CHECKOUT_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"


class FluxReconcileWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        operations = json.loads(
            (ROOT / "contracts/public-workflows/operations.json").read_text(
                encoding="utf-8"
            )
        )
        cls.record = next(
            row
            for row in operations["workflows"]
            if row["api_name"] == "flux.reconcile"
        )
        permissions = json.loads(
            (ROOT / "contracts/permission-profiles.json").read_text(
                encoding="utf-8"
            )
        )
        cls.permission = next(
            row
            for row in permissions["profiles"]
            if row["id"] == cls.record["permission_profile"]
        )
        cls.path = ROOT / cls.record["file"]
        cls.text = cls.path.read_text(encoding="utf-8")
        cls.workflow = yaml.load(cls.text, Loader=ActionsLoader)

    def test_flux_workflow_matches_reviewed_public_api_and_flux_control_runner(self) -> None:
        self.assertEqual(set(self.workflow["on"]), {"workflow_call"})
        call = self.workflow["on"]["workflow_call"]
        self.assertEqual(
            set(call["inputs"]),
            {row["name"] for row in self.record["inputs"]},
        )
        self.assertEqual(set(call.get("secrets", {})), set(self.record["secrets"]))
        self.assertEqual(set(call["outputs"]), set(self.record["outputs"]))
        self.assertEqual(
            self.workflow["permissions"],
            self.permission["workflow_permissions"],
        )
        job = self.workflow["jobs"]["reconcile"]
        self.assertEqual(job["runs-on"], ["linux", "amd64", "flux-control"])
        self.assertEqual(job["timeout-minutes"], 120)
        self.assertIs(call["inputs"]["dry_run"]["default"], True)
        for step in job["steps"]:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                self.assertNotIn("${{ inputs.", step["run"])

    def test_flux_workflow_executes_only_exact_flux_owned_policy_source(self) -> None:
        self.assertIn(
            "uses: StreamScapeTV/ci-workflows/actions/exact-checkout@"
            f"{EXACT_CHECKOUT_SHA}",
            self.text,
        )
        self.assertIn(
            "uses: StreamScapeTV/ci-workflows/actions/flux-reconcile@"
            f"{HELPER_SHA}",
            self.text,
        )
        self.assertNotIn("actions/checkout@", self.text)
        self.assertNotIn("job.workflow_", self.text)
        self.assertNotIn("path: .ciw", self.text)
        self.assertNotIn("./.ciw/actions/", self.text)
        self.assertIn("repository: StreamScapeTV/flux", self.text)
        self.assertIn("admitted_sha: ${{ inputs.admitted_sha }}", self.text)
        self.assertIn("CIW_ADMITTED_SHA: ${{ inputs.admitted_sha }}", self.text)
        self.assertIn("cd source", self.text)
        self.assertIn(
            'test "$(git rev-parse HEAD)" = "${CIW_ADMITTED_SHA}"',
            self.text,
        )
        self.assertIn("git status --porcelain --untracked-files=all", self.text)
        self.assertIn("rm -rf source", self.text)
        self.assertIn("! -e source", self.text)
        self.assertIn("! -L source", self.text)
        self.assertIn(
            'state="${RUNNER_TEMP}/flux-reconcile-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"',
            self.text,
        )
        self.assertNotIn(
            'flux-reconcile-${GITHUB_RUN_ID}-${{ inputs.request_id }}',
            self.text,
        )
        for forbidden in (
            "pull_request_target",
            "issue_comment",
            "workflow_run",
            "secrets: inherit",
            "runs-on: self-hosted",
            "kubectl apply",
            "helm upgrade",
        ):
            self.assertNotIn(forbidden, self.text)

    def test_flux_target_data_and_credentials_remain_outside_central_source(self) -> None:
        action_path = ROOT / "actions/flux-reconcile/action.yml"
        action_text = action_path.read_text(encoding="utf-8")
        action = yaml.load(action_text, Loader=ActionsLoader)
        plan_source = (
            ROOT / "src/ci_workflows/flux_reconcile_plan.py"
        ).read_text(encoding="utf-8")
        apply_source = (
            ROOT / "src/ci_workflows/flux_reconcile_apply.py"
        ).read_text(encoding="utf-8")
        contract = (
            ROOT / "contracts/organization-maintenance.json"
        ).read_text(encoding="utf-8")
        combined = (self.text + action_text + plan_source + apply_source).casefold()
        for domain_value in (
            "agent-state-api",
            "iptv-backend-worker",
            "directus-web",
            "tailscale",
            "longhorn",
        ):
            self.assertNotIn(domain_value, combined)
        self.assertNotIn("flux_kubeconfig_b64", combined)
        self.assertIn("central-flux-policy-v1", contract)
        self.assertIn('policy["policy_interface"]', plan_source)
        self.assertIn("--central-request", plan_source)
        self.assertNotIn("shell=true", combined.replace(" ", ""))
        self.assertNotIn("${{ inputs.", action["runs"]["steps"][0]["run"])
        self.assertIn("scripts/ci/ciw.py", action_text)
        self.assertNotIn("scripts/ci/flux_reconcile.py", action_text)

    def test_flux_compatibility_cli_is_fixed_source_and_ciw_only(self) -> None:
        cli = (ROOT / "scripts/ci/flux_reconcile.py").read_text(encoding="utf-8")
        self.assertIn("from ci_workflows.ciw import main", cli)
        self.assertIn('args.source_root != "source"', cli)
        self.assertIn('args.source_repository != "StreamScapeTV/flux"', cli)
        self.assertIn('"flux",', cli)
        self.assertIn('"reconcile",', cli)
        self.assertNotIn("from ci_workflows.flux_reconcile import", cli)
        self.assertNotIn("FLUX_KUBECONFIG", cli)

    def test_flux_state_cleanup_is_no_follow_and_fail_closed(self) -> None:
        adapter = (
            ROOT / "src/ci_workflows/ciw_maintenance.py"
        ).read_text(encoding="utf-8")
        filesystem = (
            ROOT / "src/ci_workflows/flux_reconcile_fs.py"
        ).read_text(encoding="utf-8")
        self.assertIn("def remove_state", filesystem)
        self.assertIn("lstat()", filesystem)
        self.assertIn("flux_state_cleanup_failed", filesystem)
        self.assertIn("contract.validate_request_id(args.request_id)", adapter)
        self.assertIn("GITHUB_RUN_ATTEMPT", adapter)
        self.assertIn("raw_source.is_symlink()", adapter)
        self.assertNotIn("ignore_errors=True", adapter + filesystem)


if __name__ == "__main__":
    unittest.main()
