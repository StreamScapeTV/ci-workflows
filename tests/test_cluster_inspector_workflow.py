import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/cluster-inspector.yml"
DISPATCH = ROOT / ".github/workflows/central-ci-dispatch.yml"
INVENTORY = ROOT / "INVENTORY.yaml"


class ClusterInspectorWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        cls.dispatch = yaml.safe_load(DISPATCH.read_text(encoding="utf-8"))
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.steps = cls.workflow["jobs"]["inspect"]["steps"]
        cls.by_name = {step.get("name"): step for step in cls.steps if step.get("name")}

    def test_workflow_surface_is_fixed_and_read_only(self) -> None:
        inputs = self.workflow["on"]["workflow_call"]["inputs"]
        self.assertEqual(
            set(inputs),
            {"repository", "ref", "source_is_tag", "test_profile", "ci_run_id", "upload_private_log"},
        )
        for forbidden in ("command", "script", "script_path", "kubeconfig", "runner", "runner_label", "namespace"):
            self.assertNotIn(forbidden, inputs)
        secrets = self.workflow["on"]["workflow_call"]["secrets"]
        self.assertTrue(secrets["CLUSTER_INSPECTOR_KUBECONFIG_B64"]["required"])
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        for command in (
            "kubectl get", "kubectl describe", "kubectl logs", "kubectl apply",
            "kubectl patch", "kubectl delete", "helm ", "flux reconcile",
        ):
            self.assertNotIn(command, self.text)

    def test_profiles_map_only_to_fixed_repository_owned_scripts(self) -> None:
        validate = self.by_name["Validate fixed cluster inspection request"]["run"]
        execute = self.by_name["Run fixed repository-owned cluster inspection profile"]["run"]
        self.assertIn("longhorn|cnpg|flux", validate)
        for profile in ("longhorn", "cnpg", "flux"):
            self.assertIn(f"{profile}) script=.ci/cluster-inspector/{profile}.sh", execute)
        self.assertIn("git -C source ls-files --error-unmatch", execute)
        self.assertIn("100644|100755", execute)
        self.assertIn("test ! -L", execute)
        self.assertIn('bash "${path}"', execute)
        for unsafe in ("eval ", "bash -c", "source ${", "chmod +x"):
            self.assertNotIn(unsafe, execute)


    def test_kubernetes_identity_runs_only_default_branch_source(self) -> None:
        guard = self.by_name["Require repository default-branch source"]
        self.assertEqual(guard["env"]["SOURCE_REPOSITORY"], "${{ inputs.repository }}")
        self.assertEqual(guard["env"]["SOURCE_REF"], "${{ inputs.ref }}")
        self.assertEqual(guard["env"]["SOURCE_TOKEN"], "${{ steps.source.outputs.token }}")
        self.assertIn("https://api.github.com/repos/${SOURCE_REPOSITORY}", guard["run"])
        self.assertIn(".default_branch // empty", guard["run"])
        self.assertIn('test "${SOURCE_REF}" = "${default_branch}"', guard["run"])
        self.assertIn("restricted to the repository default branch", guard["run"])
        self.assertIn("refs/*", guard["run"])
        names = [step.get("name") for step in self.steps]
        self.assertLess(names.index("Require repository default-branch source"), names.index("Check out requested source"))
        self.assertLess(names.index("Check out requested source"), names.index("Prepare ephemeral cluster inspector kubeconfig"))

    def test_kubeconfig_is_ephemeral_and_never_an_input(self) -> None:
        prepare = self.by_name["Prepare ephemeral cluster inspector kubeconfig"]
        cleanup = self.by_name["Remove ephemeral cluster inspector kubeconfig"]
        self.assertIn("${RUNNER_TEMP}/cluster-inspector-kubeconfig", prepare["run"])
        self.assertIn("base64 --decode", prepare["run"])
        self.assertIn("chmod 600", prepare["run"])
        self.assertIn("KUBECONFIG=", prepare["run"])
        self.assertEqual(cleanup["if"], "${{ always() }}")
        self.assertIn('rm -f -- "${path}"', cleanup["run"])
        self.assertIn("KUBECONFIG=\\n", cleanup["run"])
        self.assertIn("kubeconfig cleanup failed", cleanup["run"])
        finish = self.by_name["Finish Agent State run"]
        self.assertIn("steps.kubeconfig_cleanup.outcome == 'success'", finish["with"]["status"])

    def test_private_network_is_fixed_and_conditional(self) -> None:
        require = self.by_name["Require private network credentials on GitHub-hosted runner"]
        connect = self.by_name["Connect reviewed private network"]
        hosted = "${{ runner.environment == 'github-hosted' }}"
        self.assertEqual(require["if"], hosted)
        self.assertEqual(connect["if"], hosted)
        self.assertEqual(connect["uses"], "tailscale/github-action@v4")
        self.assertEqual(connect["with"]["tags"], "tag:github-ci")
        self.assertEqual(connect["with"]["oauth-client-id"], "${{ secrets.TS_OAUTH_CLIENT_ID }}")
        self.assertEqual(connect["with"]["oauth-secret"], "${{ secrets.TS_OAUTH_SECRET }}")

    def test_evidence_is_scrubbed_private_and_cleanup_precedes_finish(self) -> None:
        scrub = self.by_name["Scrub configured CI secrets from private inspection log"]
        drive = self.by_name["Upload cluster inspection evidence to Google Drive"]
        finish = self.by_name["Finish Agent State run"]
        self.assertIn("CI_SECRET_CLUSTER_INSPECTOR_KUBECONFIG_B64", scrub["env"])
        self.assertIn("16 * 1024 * 1024", scrub["run"])
        self.assertIn("[REDACTED]", scrub["run"])
        self.assertEqual(drive["uses"], "StreamScapeTV/ci-workflows/actions/google-drive@main")
        self.assertEqual(drive["with"]["mime_type"], "text/plain")
        names = [step.get("name") for step in self.steps]
        self.assertLess(names.index("Remove ephemeral cluster inspector kubeconfig"), names.index("Upload cluster inspection evidence to Google Drive"))
        self.assertLess(names.index("Upload cluster inspection evidence to Google Drive"), names.index("Finish Agent State run"))
        self.assertIn("steps.drive.outcome == 'success'", finish["with"]["status"])

    def test_dispatch_is_bounded_and_settlement_aware(self) -> None:
        request_steps = self.dispatch["jobs"]["request"]["steps"]
        admission = next(step for step in request_steps if step.get("name") == "Validate cluster inspection request")
        self.assertEqual(admission["if"], "${{ steps.claim.outputs.workflow_key == 'inspection.cluster' }}")
        self.assertIn("longhorn|cnpg|flux", admission["run"])
        self.assertIn("accepts no semantic inputs", admission["run"])
        self.assertIn("accepts branch source only", admission["run"])

        job = self.dispatch["jobs"]["cluster_inspector"]
        self.assertEqual(job["if"], "${{ needs.request.outputs.workflow_key == 'inspection.cluster' }}")
        self.assertEqual(job["uses"], "./.github/workflows/cluster-inspector.yml")
        self.assertEqual(job["with"]["test_profile"], "${{ needs.request.outputs.test_profile }}")
        self.assertEqual(
            job["concurrency"]["group"],
            "central-ci-${{ needs.request.outputs.validation_concurrency_key }}",
        )
        self.assertNotIn("inherit", json.dumps(job["secrets"]))
        self.assertEqual(
            set(job["secrets"]),
            {
                "SOURCE_APP_ID",
                "SOURCE_APP_PRIVATE_KEY",
                "AGENT_STATE_SUPABASE_URL",
                "AGENT_STATE_SUPABASE_SECRET_KEY",
                "GOOGLE_DRIVE_CI_LOGS_FOLDER_ID",
                "GOOGLE_DRIVE_CLIENT_ID",
                "GOOGLE_DRIVE_CLIENT_SECRET",
                "GOOGLE_DRIVE_REFRESH_TOKEN",
                "TS_OAUTH_CLIENT_ID",
                "TS_OAUTH_SECRET",
                "CLUSTER_INSPECTOR_KUBECONFIG_B64",
            },
        )
        settlement = self.dispatch["jobs"]["settle_cancelled"]
        self.assertIn("cluster_inspector", settlement["needs"])
        self.assertIn("needs.cluster_inspector.result == 'cancelled'", settlement["if"])
        self.assertIn("needs.cluster_inspector.result == 'failure'", settlement["if"])

    def test_inventory_exposes_cluster_inspector(self) -> None:
        inventory = yaml.safe_load(INVENTORY.read_text(encoding="utf-8"))
        self.assertEqual(
            inventory["workflows"]["cluster_inspector"],
            ".github/workflows/cluster-inspector.yml",
        )


if __name__ == "__main__":
    unittest.main()
