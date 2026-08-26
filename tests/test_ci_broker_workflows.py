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
PRIVATE_ACTION = ROOT / "actions/private-ci/action.yml"
PRIVATE_EXECUTOR = ROOT / "src/ci_workflows/ci_private.py"
CHART_ROOT = ROOT / "charts/ci-broker"


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

    def test_public_dispatch_surface_remains_exactly_opaque(self) -> None:
        events = self.document.data["on"]
        self.assertEqual(set(events), {"workflow_dispatch"})
        inputs = events["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"active_key", "ci_run_id"})
        self.assertTrue(all(value["required"] for value in inputs.values()))
        for forbidden in (
            "project_key", "repository", "ref", "is_tag", "workflow_key", "profile",
            "inputs_json", "source_sha", "requested_source_sha", "runner", "workflow_path",
        ):
            self.assertNotIn(forbidden, inputs)
        self.assertEqual(self.document.data["permissions"], {"contents": "read"})
        self.assertEqual(
            self.document.data["concurrency"],
            {"group": "central-ci-${{ inputs.active_key }}", "cancel-in-progress": False},
        )

    def test_dispatch_uses_private_planner_then_one_fixed_hosted_family(self) -> None:
        jobs = self.document.data["jobs"]
        self.assertEqual(set(jobs), {"plan", "private", "private_linux"})
        planner = jobs["plan"]
        self.assertEqual(planner["runs-on"], ["ubuntu-latest"])
        self.assertEqual(set(planner["outputs"]), {"executor_family"})
        plan_steps = [step for step in planner["steps"] if step.get("uses") == "./actions/private-ci"]
        self.assertEqual(len(plan_steps), 1)
        self.assertEqual(plan_steps[0]["with"], {"phase": "plan", "ci_run_id": "${{ inputs.ci_run_id }}"})
        self.assertEqual(
            set(plan_steps[0]["env"]),
            {"AGENT_STATE_SUPABASE_URL", "AGENT_STATE_SUPABASE_SECRET_KEY"},
        )

        macos = jobs["private"]
        linux = jobs["private_linux"]
        self.assertEqual(macos["runs-on"], ["macos-latest"])
        self.assertEqual(linux["runs-on"], ["ubuntu-latest"])
        self.assertEqual(macos["if"], "${{ needs.plan.outputs.executor_family == 'macos' }}")
        self.assertEqual(linux["if"], "${{ needs.plan.outputs.executor_family == 'linux' }}")
        for job in (macos, linux):
            phases = [
                step["with"]["phase"]
                for step in job["steps"]
                if step.get("uses") == "./actions/private-ci"
            ]
            self.assertEqual(phases, ["execute", "recover"])
        java = [step for step in linux["steps"] if step.get("uses", "").startswith("actions/setup-java@")]
        self.assertEqual(len(java), 1)
        self.assertEqual(java[0]["with"]["java-version"], "25")
        self.assertNotIn("self-hosted", self.text)
        self.assertNotIn("./actions/private-apple-ci", self.text)

    def test_private_action_accepts_only_phase_and_opaque_uuid(self) -> None:
        self.assertEqual(set(self.private_action["inputs"]), {"phase", "ci_run_id"})
        self.assertEqual(set(self.private_action["outputs"]), {"executor_family"})
        step = self.private_action["runs"]["steps"][0]
        self.assertEqual(
            set(step["env"]),
            {"PYTHONDONTWRITEBYTECODE", "INPUT_CI_RUN_ID", "CIW_PRIVATE_LOG_PATH"},
        )
        text = PRIVATE_ACTION.read_text(encoding="utf-8")
        self.assertIn("private-ci", text)
        for private_field in ("repository:", "ref:", "project_key:", "workflow_key:", "profile:"):
            self.assertNotIn(private_field, text)

    def test_private_executor_reclaims_then_delegates_to_existing_family_functions(self) -> None:
        self.assertIn("_claim_request", self.private_executor)
        self.assertIn("resolve_profile(", self.private_executor)
        self.assertIn("execute_apple_validate", (ROOT / "src/ci_workflows/ci_private_apple.py").read_text(encoding="utf-8"))
        self.assertIn("execute_android_validate(", self.private_executor)
        self.assertIn("execute_python_validate(", self.private_executor)
        self.assertIn("_r2_upload", self.private_executor)
        self.assertIn("client.evidence(ci_run_id, source_sha)", self.private_executor)
        self.assertIn("client.finish(", self.private_executor)
        self.assertNotIn("reusable-apple.yml", self.private_executor)
        self.assertNotIn("reusable-android.yml", self.private_executor)
        self.assertNotIn("reusable-python.yml", self.private_executor)
        self.assertNotIn("shell_command", self.private_executor)

    def test_contract_defines_closed_multi_family_semantics_and_fixed_hosted_runners(self) -> None:
        self.assertEqual(self.contract["schema_version"], 4)
        self.assertEqual(
            self.contract["supported_capabilities"],
            ["apple-host-test", "android-hosted", "python-hosted"],
        )
        self.assertEqual(
            self.contract["relay"]["supported_semantic_intents"],
            [
                ["validation.apple", "host"],
                ["validation.android", "host"],
                ["validation.python", "host"],
            ],
        )
        central = self.contract["central_execution"]
        self.assertEqual(central["executor_planning_output"], "linux-or-macos-only")
        self.assertEqual(central["hosted_runners"], {"linux": "ubuntu-latest", "macos": "macos-latest"})
        self.assertEqual(
            central["implementations"],
            {
                "validation.apple": "ci_workflows.ciw_apple.execute_apple_validate",
                "validation.android": "ci_workflows.ciw_android.execute_android_validate",
                "validation.python": "ci_workflows.ciw_python.execute_python_validate",
            },
        )
        self.assertFalse(central["caller_selected_runner"])
        self.assertFalse(central["caller_selected_workflow_path"])
        self.assertFalse(central["caller_selected_secret_name"])

    def test_r2_is_private_log_authority_and_http_reader_is_withdrawn(self) -> None:
        self.assertEqual(self.log_contract["schema_version"], 3)
        self.assertEqual(self.log_contract["store"], "cloudflare-r2")
        self.assertTrue(self.log_contract["write_policy"]["read_back_after_upload"])
        self.assertTrue(self.log_contract["write_policy"]["sha256_verify_read_back"])
        self.assertFalse(self.log_contract["retrieval"]["public_http_reader"])
        self.assertEqual(self.log_contract["retrieval"]["mode"], "lowercase-cloudflare-mcp-direct-r2")
        self.assertFalse((CHART_ROOT / "templates/diagnostics-deployment.yaml").exists())
        self.assertFalse((CHART_ROOT / "templates/diagnostics-service.yaml").exists())

    def test_deployed_broker_entrypoint_remains_transport_only(self) -> None:
        self.assertIn("RelayConfig", self.broker_script)
        self.assertIn("ci_relay_server", self.broker_script)
        for forbidden in (
            "execute-apple-host", "execute_android_validate", "execute_python_validate",
            "R2_", "_request_oidc_token", "central_urlopen",
        ):
            self.assertNotIn(forbidden, self.broker_script)
        self.assertIn('self.path == "/healthz"', self.relay_server)
        self.assertIn('self.path != "/hooks/agent-state"', self.relay_server)
        self.assertNotIn("/actions/start", self.relay_server)
        self.assertNotIn("/actions/finish", self.relay_server)
        self.assertNotIn("/diagnostics/", self.relay_server)

    def test_broker_chart_contains_only_real_webhook_service(self) -> None:
        values = yaml.safe_load((CHART_ROOT / "values.yaml").read_text(encoding="utf-8"))
        schema = json.loads((CHART_ROOT / "values.schema.json").read_text(encoding="utf-8"))
        deployment = (CHART_ROOT / "templates/deployment.yaml").read_text(encoding="utf-8")
        service = (CHART_ROOT / "templates/service.yaml").read_text(encoding="utf-8")
        self.assertEqual(values["replicaCount"], 1)
        self.assertEqual(values["service"], {"type": "ClusterIP", "port": 8080})
        self.assertNotIn("diagnostics", values)
        self.assertNotIn("diagnostics", schema["properties"])
        self.assertIn("automountServiceAccountToken: false", deployment)
        self.assertIn("path: /healthz", deployment)
        self.assertNotIn("LoadBalancer", service)

    def test_release_and_repository_policy_remain_bounded(self) -> None:
        dispatch = self.policy["workflow_admission"]["workflows"][".github/workflows/central-ci-dispatch.yml"]
        self.assertEqual(dispatch["trust_class"], "broker-dispatch")
        self.assertEqual(dispatch["allowed_events"], ["workflow_dispatch"])
        events = self.release_document.data["on"]
        self.assertEqual(set(events), {"push", "workflow_dispatch"})
        self.assertNotIn("upload-artifact", self.release_text)


if __name__ == "__main__":
    unittest.main()
