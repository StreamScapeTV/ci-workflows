from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import load_actions_yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/central-ci-dispatch.yml"
RELEASE_WORKFLOW = ROOT / ".github/workflows/ci-broker-image.yml"
POLICY = ROOT / "contracts/repository-policy.json"
CONTRACT = ROOT / "contracts/ci-broker.json"
LOG_CONTRACT = ROOT / "contracts/ci-diagnostics.json"
BROKER_SCRIPT = ROOT / "scripts/ci/ci_broker.py"
RELAY = ROOT / "src/ci_workflows/ci_relay.py"
RELAY_SERVER = ROOT / "src/ci_workflows/ci_relay_server.py"
PRIVATE_ACTION = ROOT / "actions/private-apple-ci/action.yml"
PRIVATE_EXECUTOR = ROOT / "src/ci_workflows/ci_private_apple.py"
CHART_ROOT = ROOT / "charts/ci-broker"
CHART = CHART_ROOT / "Chart.yaml"
VALUES = CHART_ROOT / "values.yaml"
VALUES_SCHEMA = CHART_ROOT / "values.schema.json"
DEPLOYMENT = CHART_ROOT / "templates/deployment.yaml"
SERVICE = CHART_ROOT / "templates/service.yaml"


class BrokerWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = load_actions_yaml(WORKFLOW, ROOT)
        cls.release_document = load_actions_yaml(RELEASE_WORKFLOW, ROOT)
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.release_text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        cls.policy = json.loads(POLICY.read_text(encoding="utf-8"))
        cls.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        cls.log_contract = json.loads(LOG_CONTRACT.read_text(encoding="utf-8"))
        cls.broker_script = BROKER_SCRIPT.read_text(encoding="utf-8")
        cls.relay = RELAY.read_text(encoding="utf-8")
        cls.relay_server = RELAY_SERVER.read_text(encoding="utf-8")
        cls.private_action = yaml.safe_load(PRIVATE_ACTION.read_text(encoding="utf-8"))
        cls.private_executor = PRIVATE_EXECUTOR.read_text(encoding="utf-8")
        cls.chart = yaml.safe_load(CHART.read_text(encoding="utf-8"))
        cls.values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
        cls.values_schema = json.loads(VALUES_SCHEMA.read_text(encoding="utf-8"))
        cls.deployment_text = DEPLOYMENT.read_text(encoding="utf-8")
        cls.service_text = SERVICE.read_text(encoding="utf-8")

    def test_public_dispatch_surface_is_opaque(self) -> None:
        events = self.document.data["on"]
        self.assertEqual(set(events), {"workflow_dispatch"})
        inputs = events["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"active_key", "ci_run_id"})
        self.assertTrue(all(value["required"] for value in inputs.values()))
        for forbidden in (
            "project_key",
            "repository",
            "ref",
            "is_tag",
            "workflow_key",
            "profile",
            "inputs_json",
            "source_sha",
            "requested_source_sha",
            "dispatch_token",
        ):
            self.assertNotIn(forbidden, inputs)
        self.assertEqual(self.document.data["permissions"], {"contents": "read"})
        self.assertEqual(
            self.document.data["concurrency"],
            {"group": "central-ci-${{ inputs.active_key }}", "cancel-in-progress": False},
        )

    def test_private_dispatch_has_one_hosted_apple_job_and_fixed_secrets(self) -> None:
        jobs = self.document.data["jobs"]
        self.assertEqual(set(jobs), {"private"})
        job = jobs["private"]
        self.assertEqual(job["runs-on"], ["macos-latest"])
        self.assertEqual(job["timeout-minutes"], 120)
        private_steps = [
            step for step in job["steps"]
            if step.get("uses") == "./actions/private-apple-ci"
        ]
        self.assertEqual(len(private_steps), 2)
        execute, recover = private_steps
        self.assertEqual(execute["with"], {"phase": "execute", "ci_run_id": "${{ inputs.ci_run_id }}"})
        self.assertEqual(recover["with"], {"phase": "recover", "ci_run_id": "${{ inputs.ci_run_id }}"})
        self.assertEqual(recover["if"], "${{ always() }}")
        self.assertEqual(
            set(execute["env"]),
            {
                "AGENT_STATE_SUPABASE_URL",
                "AGENT_STATE_SUPABASE_SECRET_KEY",
                "SOURCE_APP_ID",
                "SOURCE_APP_PRIVATE_KEY",
                "R2_ACCOUNT_ID",
                "R2_BUCKET",
                "R2_ACCESS_KEY_ID",
                "R2_SECRET_ACCESS_KEY",
            },
        )
        self.assertEqual(
            set(recover["env"]),
            {
                "AGENT_STATE_SUPABASE_URL",
                "AGENT_STATE_SUPABASE_SECRET_KEY",
                "R2_ACCOUNT_ID",
                "R2_BUCKET",
                "R2_ACCESS_KEY_ID",
                "R2_SECRET_ACCESS_KEY",
            },
        )
        for obsolete in (
            "GITHUB_SOURCE_APP_ID",
            "GITHUB_SOURCE_APP_PRIVATE_KEY",
            "CI_D1_",
            "CI_BROKER_URL",
            "R2_ACCESS_KEY_ID: ${{ inputs",
        ):
            self.assertNotIn(obsolete, self.text)

    def test_private_action_accepts_only_phase_and_opaque_uuid(self) -> None:
        self.assertEqual(set(self.private_action["inputs"]), {"phase", "ci_run_id"})
        step = self.private_action["runs"]["steps"][0]
        self.assertEqual(
            set(step["env"]),
            {"PYTHONDONTWRITEBYTECODE", "INPUT_CI_RUN_ID", "CIW_PRIVATE_LOG_PATH"},
        )
        text = PRIVATE_ACTION.read_text(encoding="utf-8")
        self.assertIn("runner.temp", text)
        for private_field in ("repository:", "ref:", "project_key:", "workflow_key:", "profile:"):
            self.assertNotIn(private_field, text)

    def test_private_executor_reclaims_identity_runs_canonical_apple_and_exports_r2(self) -> None:
        self.assertIn('client._rpc("claim_ci_run"', self.private_executor)
        self.assertIn("RelayRequest.from_claimed_run", self.private_executor)
        self.assertIn("resolve_profile(", self.private_executor)
        self.assertIn("execute_apple_validate(", self.private_executor)
        self.assertIn("upload_private_diagnostic(", self.private_executor)
        self.assertIn("client.evidence(ci_run_id, source_sha)", self.private_executor)
        self.assertIn("client.finish(", self.private_executor)
        self.assertIn("SOURCE_APP_ID", self.private_executor)
        self.assertIn("SOURCE_APP_PRIVATE_KEY", self.private_executor)
        self.assertIn("R2_ACCOUNT_ID", self.private_executor)
        self.assertNotIn("D1", self.private_executor)
        self.assertNotIn("GITHUB_SOURCE_APP_", self.private_executor)
        self.assertNotIn("reusable-apple.yml", self.private_executor)

    def test_r2_is_private_log_authority_and_agent_state_keeps_only_receipt(self) -> None:
        self.assertEqual(self.log_contract["schema_version"], 2)
        self.assertEqual(self.log_contract["store"], "cloudflare-r2")
        self.assertEqual(self.log_contract["format"], "gzip-private-runner-log")
        self.assertTrue(self.log_contract["write_policy"]["read_back_after_upload"])
        self.assertTrue(self.log_contract["write_policy"]["sha256_verify_read_back"])
        self.assertFalse(self.log_contract["write_policy"]["github_actions_artifact"])
        public = self.log_contract["github_public_log"]
        self.assertFalse(public["private_repository_identity_in_dispatch_inputs"])
        self.assertFalse(public["private_ref_in_dispatch_inputs"])
        self.assertFalse(public["private_command_stdout_stderr"])
        agent_state = self.log_contract["agent_state"]
        self.assertTrue(agent_state["raw_logs_forbidden"])
        self.assertEqual(agent_state["uploaded_status"], "uploaded")
        self.assertEqual(
            agent_state["terminal_order"],
            "upload-readback-verify-r2-before-terminal-transition",
        )

    def test_broker_contract_matches_opaque_dispatch_and_r2_boundary(self) -> None:
        self.assertEqual(self.contract["schema_version"], 3)
        relay = self.contract["relay"]
        self.assertEqual(relay["dispatch_inputs"], ["active_key", "ci_run_id"])
        self.assertFalse(relay["private_identity_in_public_dispatch"])
        self.assertFalse(relay["run_discovery"])
        self.assertEqual(
            relay["active_identity_fields"],
            ["repository", "ref", "is_tag", "workflow_key", "profile"],
        )
        self.assertEqual(self.contract["agent_state"]["retention_hours"], 24)
        self.assertTrue(self.contract["agent_state"]["raw_logs_forbidden"])
        central = self.contract["central_execution"]
        self.assertEqual(central["request_lookup"], "claim-ci-run-by-opaque-uuid")
        self.assertEqual(central["source_mode"], "requested-ref-internal")
        self.assertEqual(central["observed_sha"], "evidence-only")
        self.assertEqual(central["apple_implementation"], "ci_workflows.ciw_apple.execute_apple_validate")
        self.assertEqual(central["hosted_apple_runner"], "macos-latest")
        private_logs = self.contract["private_logs"]
        self.assertEqual(private_logs["store"], "cloudflare-r2")
        self.assertEqual(private_logs["private_command_stdout_stderr"], "runner-local-only")
        self.assertTrue(private_logs["readback_required"])
        self.assertEqual(
            private_logs["terminal_order"],
            "upload-readback-verify-r2-before-agent-state-terminal",
        )
        self.assertEqual(
            self.contract["fixed_environment"]["central_workflow_secrets"],
            [
                "AGENT_STATE_SUPABASE_URL",
                "AGENT_STATE_SUPABASE_SECRET_KEY",
                "SOURCE_APP_ID",
                "SOURCE_APP_PRIVATE_KEY",
                "R2_ACCOUNT_ID",
                "R2_BUCKET",
                "R2_ACCESS_KEY_ID",
                "R2_SECRET_ACCESS_KEY",
            ],
        )
        forbidden = set(self.contract["forbidden"])
        for required in (
            "broker-source-resolution",
            "broker-build-or-test",
            "broker-log-storage",
            "private-repository-in-public-dispatch-input",
            "private-ref-in-public-dispatch-input",
            "private-command-output-in-public-github-log",
            "cloudflare-d1-diagnostic-store",
            "raw-log-storage-in-agent-state",
        ):
            self.assertIn(required, forbidden)

    def test_deployed_broker_entrypoint_is_thin_relay_only(self) -> None:
        self.assertIn("RelayConfig", self.broker_script)
        self.assertIn("ci_relay_server", self.broker_script)
        self.assertIn('choices=("server", "self-check")', self.broker_script)
        for forbidden in (
            "execute-apple-host",
            "fail-if-active",
            "cancel-if-active",
            "R2_",
            "_request_oidc_token",
            "central_urlopen",
        ):
            self.assertNotIn(forbidden, self.broker_script)
        self.assertIn('self.path == "/healthz"', self.relay_server)
        self.assertIn('self.path != "/hooks/agent-state"', self.relay_server)
        self.assertNotIn("/hooks/github", self.relay_server)
        self.assertNotIn("/actions/start", self.relay_server)
        self.assertNotIn("/actions/finish", self.relay_server)

    def test_broker_chart_is_one_replica_private_service_with_matching_app_version_image(self) -> None:
        self.assertEqual(self.chart["name"], "ci-broker")
        self.assertEqual(self.chart["type"], "application")
        self.assertEqual(self.values["replicaCount"], 1)
        self.assertEqual(self.values_schema["properties"]["replicaCount"]["const"], 1)
        self.assertEqual(
            self.values["image"]["repository"],
            "git.faruqi.dev/mimranfaruqi/ci-workflows/ci-broker",
        )
        self.assertEqual(self.values["image"]["tag"], "")
        self.assertEqual(self.values["image"]["pullSecrets"], ["private-registry"])
        self.assertEqual(self.values["existingSecret"]["name"], "ci-broker-secrets")
        self.assertEqual(self.values["service"], {"type": "ClusterIP", "port": 8080})
        self.assertIn("default .Chart.AppVersion .Values.image.tag", self.deployment_text)
        self.assertIn("automountServiceAccountToken: false", self.deployment_text)
        self.assertIn("readOnlyRootFilesystem: true", self.deployment_text)
        self.assertIn("runAsUser: 65532", self.deployment_text)
        self.assertIn("path: /healthz", self.deployment_text)
        self.assertNotIn("LoadBalancer", self.service_text)

    def test_broker_release_uses_private_arc_image_then_helm_publication(self) -> None:
        events = self.release_document.data["on"]
        self.assertEqual(set(events), {"push", "workflow_dispatch"})
        self.assertEqual(events["push"]["tags"], ["ci-broker-*"])
        self.assertEqual(set(events["workflow_dispatch"]["inputs"]), {"release_tag"})
        self.assertEqual(self.release_document.data["permissions"], {"contents": "read"})
        jobs = self.release_document.data["jobs"]
        self.assertEqual(set(jobs), {"admit", "image", "chart"})
        self.assertEqual(jobs["admit"]["runs-on"], ["linux", "amd64", "general", "tiny"])
        self.assertEqual(jobs["image"]["runs-on"], ["linux", "amd64", "buildah", "small"])
        self.assertEqual(jobs["chart"]["runs-on"], ["linux", "amd64", "general", "small"])
        self.assertIn("secrets.FORGEJO_REGISTRY_USERNAME", self.release_text)
        self.assertIn("secrets.FORGEJO_REGISTRY_TOKEN", self.release_text)
        self.assertIn("helm package", self.release_text)
        self.assertIn("skopeo inspect", self.release_text)
        self.assertIn("helm pull", self.release_text)
        self.assertNotIn("upload-artifact", self.release_text)

    def test_repository_policy_keeps_dispatch_and_release_event_classes_bounded(self) -> None:
        dispatch = self.policy["workflow_admission"]["workflows"][
            ".github/workflows/central-ci-dispatch.yml"
        ]
        self.assertEqual(dispatch["trust_class"], "broker-dispatch")
        self.assertEqual(dispatch["allowed_events"], ["workflow_dispatch"])
        release = self.policy["workflow_admission"]["workflows"][
            ".github/workflows/ci-broker-image.yml"
        ]
        self.assertEqual(
            release,
            {"trust_class": "tag-release", "allowed_events": ["push", "workflow_dispatch"]},
        )


if __name__ == "__main__":
    unittest.main()
