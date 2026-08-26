from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest

import yaml

from ci_workflows.validation_model import load_actions_yaml

ROOT = Path(__file__).resolve().parents[1]
BROKER = ROOT / "ci-broker"
WORKFLOW = ROOT / ".github/workflows/central-ci-dispatch.yml"
RELEASE_WORKFLOW = ROOT / ".github/workflows/ci-broker-image.yml"
POLICY = ROOT / "contracts/repository-policy.json"
CONTRACT = ROOT / "contracts/ci-broker.json"
LOG_CONTRACT = ROOT / "contracts/ci-diagnostics.json"
PRIVATE_ACTION = ROOT / "actions/private-ci/action.yml"
PRIVATE_EXECUTOR = ROOT / "src/ci_workflows/ci_private.py"
CIW_SCRIPT = ROOT / "scripts/ci/ciw.py"


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
        cls.broker_app = (BROKER / "app.py").read_text(encoding="utf-8")
        cls.private_action = yaml.safe_load(PRIVATE_ACTION.read_text(encoding="utf-8"))
        cls.private_executor = PRIVATE_EXECUTOR.read_text(encoding="utf-8")
        cls.ciw_script = CIW_SCRIPT.read_text(encoding="utf-8")

        spec = importlib.util.spec_from_file_location(
            "ci_broker_app_test", BROKER / "app.py"
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("unable to load broker module")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        cls.broker_module = module

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
            {
                "group": "central-ci-${{ inputs.active_key }}",
                "cancel-in-progress": True,
            },
        )
        self.assertIn(
            'CENTRAL_WORKFLOW = ".github/workflows/central-ci-dispatch.yml"',
            self.broker_app,
        )
        self.assertIn('CENTRAL_REF = "main"', self.broker_app)
        self.assertIn(
            '_require(set(inputs) == {"active_key", "ci_run_id"}', self.broker_app
        )

    def test_active_key_is_source_repository_and_logical_ref_only(self) -> None:
        active_key = self.broker_module.active_identity_key
        first = active_key(
            repository="ExampleOrg/private-app",
            ref="develop",
            is_tag=False,
            workflow_key="validation.apple",
            profile="host",
        )
        different_intent = active_key(
            repository="ExampleOrg/private-app",
            ref="develop",
            is_tag=False,
            workflow_key="validation.python",
            profile="alternate",
        )
        self.assertEqual(first, different_intent)
        self.assertRegex(first, r"^[0-9a-f]{64}$")

        self.assertNotEqual(
            first,
            active_key(
                repository="ExampleOrg/other-app",
                ref="develop",
                is_tag=False,
                workflow_key="validation.apple",
                profile="host",
            ),
        )
        self.assertNotEqual(
            first,
            active_key(
                repository="ExampleOrg/private-app",
                ref="feature/new-run",
                is_tag=False,
                workflow_key="validation.apple",
                profile="host",
            ),
        )
        self.assertNotEqual(
            first,
            active_key(
                repository="ExampleOrg/private-app",
                ref="develop",
                is_tag=True,
                workflow_key="validation.apple",
                profile="host",
            ),
        )
        self.assertEqual(
            self.contract["relay"]["active_identity_fields"],
            ["repository", "ref", "is_tag"],
        )

    def test_active_key_dispatch_does_not_expose_private_identity(self) -> None:
        request = self.broker_module.RelayRequest(
            ci_run_id="00000000-0000-4000-8000-000000000608",
            project_key="synthetic-project",
            repository="ExampleOrg/private-app",
            ref="feature/new-run",
            is_tag=False,
            workflow_key="validation.apple",
            profile="host",
        )
        inputs = request.workflow_inputs()
        self.assertEqual(set(inputs), {"active_key", "ci_run_id"})
        rendered = json.dumps(inputs, sort_keys=True)
        for private in (
            "ExampleOrg/private-app",
            "feature/new-run",
            "synthetic-project",
            "validation.apple",
            "host",
        ):
            self.assertNotIn(private, rendered)

    def test_dispatch_uses_private_planner_then_one_fixed_hosted_family(self) -> None:
        jobs = self.document.data["jobs"]
        self.assertEqual(set(jobs), {"plan", "private", "private_linux"})
        self.assertEqual(jobs["plan"]["runs-on"], ["ubuntu-latest"])
        self.assertEqual(jobs["private"]["runs-on"], ["macos-latest"])
        self.assertEqual(jobs["private_linux"]["runs-on"], ["ubuntu-latest"])
        self.assertEqual(jobs["private"]["if"], "${{ needs.plan.outputs.executor_family == 'macos' }}")
        self.assertEqual(jobs["private_linux"]["if"], "${{ needs.plan.outputs.executor_family == 'linux' }}")
        self.assertNotIn("self-hosted", self.text)

    def test_private_action_accepts_only_phase_and_opaque_uuid(self) -> None:
        self.assertEqual(set(self.private_action["inputs"]), {"phase", "ci_run_id"})
        self.assertEqual(set(self.private_action["outputs"]), {"executor_family"})
        text = PRIVATE_ACTION.read_text(encoding="utf-8")
        for private_field in ("repository:", "ref:", "project_key:", "workflow_key:", "profile:"):
            self.assertNotIn(private_field, text)

    def test_private_executor_is_one_generic_family_transaction(self) -> None:
        self.assertIn("_claim_request", self.private_executor)
        self.assertIn("resolve_profile(", self.private_executor)
        self.assertIn("exact_checkout(", self.private_executor)
        self.assertIn("execute_apple_validate(", self.private_executor)
        self.assertIn("execute_android_validate(", self.private_executor)
        self.assertIn("execute_python_validate(", self.private_executor)
        self.assertIn("upload_private_diagnostic(", self.private_executor)
        self.assertIn("client.finish(", self.private_executor)
        self.assertNotIn("ci_private_apple", self.private_executor)
        self.assertNotIn("shell_command", self.private_executor)

    def test_retired_private_apple_compatibility_surface_cannot_reappear(self) -> None:
        self.assertFalse((ROOT / "src/ci_workflows/ci_private_apple.py").exists())
        self.assertFalse((ROOT / "actions/private-apple-ci/action.yml").exists())
        self.assertNotIn("ci_private_apple", self.ciw_script)
        self.assertNotIn('"private-apple"', self.ciw_script)

    def test_contract_defines_closed_multi_family_semantics_and_fixed_hosted_runners(self) -> None:
        self.assertEqual(self.contract["schema_version"], 4)
        self.assertEqual(
            self.contract["relay"]["supported_semantic_intents"],
            [["validation.apple", "host"], ["validation.android", "host"], ["validation.python", "host"]],
        )
        self.assertEqual(
            self.contract["relay"]["active_identity_fields"],
            ["repository", "ref", "is_tag"],
        )
        central = self.contract["central_execution"]
        self.assertEqual(central["hosted_runners"], {"linux": "ubuntu-latest", "macos": "macos-latest"})
        self.assertFalse(central["caller_selected_runner"])
        self.assertFalse(central["caller_selected_workflow_path"])
        self.assertFalse(central["caller_selected_secret_name"])

    def test_standalone_broker_is_transport_only_and_central_package_has_no_broker_runtime(self) -> None:
        self.assertIn('self.path == "/healthz"', self.broker_app)
        self.assertIn('self.path != "/hooks/agent-state"', self.broker_app)
        self.assertIn("claim_ci_run", self.broker_app)
        self.assertIn("transition_ci_run", self.broker_app)
        for forbidden in (
            "/actions/start", "/actions/finish", "/hooks/github", "/diagnostics/",
            ".github/central-ci.json", "execute_apple_validate", "execute_android_validate",
            "execute_python_validate", "R2_", "workflow_run_id", "get_private_config",
        ):
            self.assertNotIn(forbidden, self.broker_app)
        self.assertFalse((ROOT / "src/ci_workflows/ci_broker.py").exists())
        self.assertFalse((ROOT / "src/ci_workflows/ci_relay.py").exists())
        self.assertFalse((ROOT / "src/ci_workflows/ci_relay_server.py").exists())

    def test_broker_chart_contains_only_real_webhook_service(self) -> None:
        chart = BROKER / "chart"
        values = yaml.safe_load((chart / "values.yaml").read_text(encoding="utf-8"))
        schema = json.loads((chart / "values.schema.json").read_text(encoding="utf-8"))
        deployment = (chart / "templates/deployment.yaml").read_text(encoding="utf-8")
        service = (chart / "templates/service.yaml").read_text(encoding="utf-8")
        self.assertEqual(values["replicaCount"], 1)
        self.assertEqual(values["service"], {"type": "ClusterIP", "port": 8080})
        self.assertNotIn("diagnostics", values)
        self.assertNotIn("diagnostics", schema["required"])
        self.assertEqual(schema["properties"]["diagnostics"]["properties"]["enabled"]["const"], False)
        self.assertFalse((chart / "templates/diagnostics-deployment.yaml").exists())
        self.assertFalse((chart / "templates/diagnostics-service.yaml").exists())
        self.assertIn("automountServiceAccountToken: false", deployment)
        self.assertIn("path: /healthz", deployment)
        self.assertNotIn("LoadBalancer", service)

    def test_r2_is_central_private_log_authority_not_broker_authority(self) -> None:
        self.assertEqual(self.log_contract["store"], "cloudflare-r2")
        self.assertTrue(self.log_contract["write_policy"]["read_back_after_upload"])
        self.assertFalse(self.log_contract["retrieval"]["public_http_reader"])
        self.assertNotIn("R2_", self.broker_app)

    def test_release_and_repository_policy_remain_bounded(self) -> None:
        dispatch = self.policy["workflow_admission"]["workflows"][".github/workflows/central-ci-dispatch.yml"]
        self.assertEqual(dispatch["trust_class"], "broker-dispatch")
        self.assertEqual(dispatch["allowed_events"], ["workflow_dispatch"])
        events = self.release_document.data["on"]
        self.assertEqual(set(events), {"push", "workflow_dispatch"})
        self.assertNotIn("upload-artifact", self.release_text)


if __name__ == "__main__":
    unittest.main()
