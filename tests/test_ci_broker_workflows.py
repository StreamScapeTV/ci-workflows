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
BROKER_SCRIPT = ROOT / "scripts/ci/ci_broker.py"
RELAY = ROOT / "src/ci_workflows/ci_relay.py"
RELAY_SERVER = ROOT / "src/ci_workflows/ci_relay_server.py"
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
        cls.broker_script = BROKER_SCRIPT.read_text(encoding="utf-8")
        cls.relay = RELAY.read_text(encoding="utf-8")
        cls.relay_server = RELAY_SERVER.read_text(encoding="utf-8")
        cls.chart = yaml.safe_load(CHART.read_text(encoding="utf-8"))
        cls.values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
        cls.values_schema = json.loads(VALUES_SCHEMA.read_text(encoding="utf-8"))
        cls.deployment_text = DEPLOYMENT.read_text(encoding="utf-8")
        cls.service_text = SERVICE.read_text(encoding="utf-8")

    def test_dispatch_surface_is_ref_based_and_contains_no_broker_execution_envelope(self) -> None:
        events = self.document.data["on"]
        self.assertEqual(set(events), {"workflow_dispatch"})
        inputs = events["workflow_dispatch"]["inputs"]
        self.assertEqual(
            set(inputs),
            {
                "active_key",
                "ci_run_id",
                "project_key",
                "repository",
                "ref",
                "is_tag",
                "workflow_key",
                "profile",
                "inputs_json",
            },
        )
        self.assertTrue(all(value["required"] for value in inputs.values()))
        for forbidden in (
            "dispatch_id",
            "dispatch_token",
            "source_sha",
            "requested_source_sha",
        ):
            self.assertNotIn(forbidden, inputs)
        self.assertEqual(self.document.data["permissions"], {"contents": "read"})
        self.assertNotIn("id-token", self.text)

    def test_active_identity_serializes_relay_recovery_without_sha_authority(self) -> None:
        concurrency = self.document.data["concurrency"]
        self.assertEqual(concurrency["group"], "central-ci-${{ inputs.active_key }}")
        self.assertIs(False, concurrency["cancel-in-progress"])
        self.assertEqual(
            self.contract["relay"]["active_identity_fields"],
            ["repository", "ref", "is_tag", "workflow_key", "profile"],
        )
        self.assertEqual(
            self.contract["relay"]["active_key"],
            "sha256-canonical-active-identity",
        )
        self.assertTrue(self.contract["relay"]["replayed_accepted_request_recoverable"])
        self.assertTrue(self.contract["relay"]["requested_sha_forbidden"])

    def test_control_job_owns_lifecycle_ref_resolution_profile_and_observed_sha(self) -> None:
        jobs = self.document.data["jobs"]
        self.assertEqual(set(jobs), {"control", "apple", "finalize"})
        control = jobs["control"]
        self.assertEqual(control["runs-on"], ["ubuntu-latest"])
        self.assertEqual(control["timeout-minutes"], 15)
        names = [step.get("name", "") for step in control["steps"]]
        ordered = (
            "Attach GitHub run identity to Agent State",
            "Validate thin-relay intent and active identity",
            "Mint exact source repository token",
            "Resolve requested human ref inside Central",
            "Check out exact observed source for product profile",
            "Record observed checkout SHA as evidence",
            "Resolve bounded source-owned Central profile",
            "Require the reviewed Apple host capability",
        )
        positions = [names.index(name) for name in ordered]
        self.assertEqual(positions, sorted(positions))
        by_name = {step.get("name"): step for step in control["steps"]}
        start = by_name["Attach GitHub run identity to Agent State"]
        self.assertEqual(start["uses"], "./actions/ci-lifecycle")
        self.assertEqual(start["with"]["phase"], "start")
        self.assertEqual(start["with"]["ref"], "${{ inputs.ref }}")
        self.assertNotIn("observed_sha", start["with"])
        resolve = by_name["Resolve requested human ref inside Central"]
        self.assertEqual(resolve["uses"], "./actions/resolve-source")
        self.assertEqual(resolve["with"]["source_mode"], "requested-ref")
        self.assertEqual(resolve["with"]["requested_ref"], "${{ inputs.ref }}")
        self.assertEqual(resolve["with"]["caller_repository"], "${{ inputs.repository }}")
        self.assertNotIn("requested_sha", resolve["with"])
        evidence = by_name["Record observed checkout SHA as evidence"]
        self.assertEqual(evidence["with"]["phase"], "evidence")
        self.assertEqual(evidence["with"]["observed_sha"], "${{ steps.resolve.outputs.source_sha }}")
        profile = by_name["Resolve bounded source-owned Central profile"]
        self.assertEqual(profile["uses"], "./actions/resolve-central-profile")
        self.assertEqual(profile["with"]["source_repository"], "${{ inputs.repository }}")
        self.assertEqual(profile["with"]["admitted_sha"], "${{ steps.resolve.outputs.source_sha }}")

    def test_canonical_apple_reusable_is_the_only_product_executor(self) -> None:
        apple = self.document.data["jobs"]["apple"]
        self.assertEqual(apple["uses"], "./.github/workflows/reusable-apple.yml")
        self.assertEqual(apple["needs"], "control")
        self.assertEqual(apple["with"]["source_repository"], "${{ inputs.repository }}")
        self.assertEqual(apple["with"]["admitted_sha"], "${{ needs.control.outputs.source_sha }}")
        self.assertEqual(
            set(apple["secrets"]),
            {"repository_app_id", "repository_app_private_key"},
        )
        for forbidden in (
            "execute-apple-host",
            "xcodebuild ",
            "private_dependency_token:",
            "CI_BROKER_URL",
        ):
            self.assertNotIn(forbidden, self.text)

    def test_finalizer_persists_d1_before_terminal_agent_state(self) -> None:
        finalizer = self.document.data["jobs"]["finalize"]
        self.assertEqual(finalizer["runs-on"], ["ubuntu-latest"])
        self.assertEqual(finalizer["if"], "${{ always() }}")
        steps = finalizer["steps"]
        diagnostics_index = next(
            index for index, step in enumerate(steps)
            if step.get("uses") == "./actions/persist-ci-diagnostics"
        )
        finish_index = next(
            index for index, step in enumerate(steps)
            if step.get("uses") == "./actions/ci-lifecycle"
            and step.get("with", {}).get("phase") == "finish"
        )
        self.assertLess(diagnostics_index, finish_index)
        diagnostics = steps[diagnostics_index]
        self.assertEqual(
            set(diagnostics["env"]),
            {"CIW_D1_ACCOUNT_ID", "CIW_D1_DATABASE_ID", "CIW_D1_API_TOKEN"},
        )
        finish = steps[finish_index]
        self.assertEqual(
            finish["with"]["diagnostic_status"],
            "${{ steps.diagnostics.outputs.diagnostic_status }}",
        )
        self.assertEqual(
            finish["with"]["diagnostic_key"],
            "${{ steps.diagnostics.outputs.diagnostic_key }}",
        )
        for forbidden in ("R2_", "upload-artifact", "download-artifact", "diagnostics_json:"):
            if forbidden == "diagnostics_json:":
                self.assertNotIn(forbidden, json.dumps(finish, sort_keys=True))
            else:
                self.assertNotIn(forbidden, self.text)

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

    def test_broker_contract_assigns_source_lifecycle_and_diagnostics_to_central(self) -> None:
        self.assertEqual(self.contract["schema_version"], 2)
        self.assertEqual(self.contract["relay"]["role"], "authenticated-fire-and-forget-transport")
        self.assertFalse(self.contract["relay"]["run_discovery"])
        self.assertEqual(self.contract["agent_state"]["retention_hours"], 24)
        self.assertTrue(self.contract["agent_state"]["raw_logs_forbidden"])
        central = self.contract["central_execution"]
        self.assertEqual(central["source_mode"], "requested-ref")
        self.assertEqual(central["observed_sha"], "evidence-only")
        self.assertEqual(central["apple_workflow"], ".github/workflows/reusable-apple.yml")
        self.assertEqual(central["hosted_apple_runner"], "macos-latest")
        diagnostics = self.contract["diagnostics"]
        self.assertEqual(diagnostics["store"], "cloudflare-d1")
        self.assertEqual(diagnostics["retention_hours"], 24)
        self.assertEqual(diagnostics["raw_log_source"], "github-actions-only")
        self.assertEqual(
            diagnostics["terminal_order"],
            "persist-and-verify-d1-before-agent-state-terminal",
        )
        forbidden = set(self.contract["forbidden"])
        for required in (
            "broker-source-resolution",
            "broker-product-config-read",
            "broker-dependency-admission",
            "broker-build-or-test",
            "broker-log-or-diagnostic-storage",
            "broker-actions-callback",
            "requested-sha-branch-authority",
        ):
            self.assertIn(required, forbidden)

    def test_broker_chart_is_one_replica_private_service_with_matching_app_version_image(self) -> None:
        self.assertEqual(self.chart["name"], "ci-broker")
        self.assertEqual(self.chart["type"], "application")
        self.assertEqual(self.values["replicaCount"], 1)
        self.assertEqual(self.values_schema["properties"]["replicaCount"]["const"], 1)
        self.assertEqual(
            self.values["image"]["repository"],
            "git.faruqi.dev/mimranfaruqi/ci-workflows/ci-broker",
        )
        self.assertEqual(
            self.values_schema["properties"]["image"]["properties"]["repository"]["const"],
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
        self.assertIn("mountPath: /tmp", self.deployment_text)
        self.assertIn("sizeLimit: 8Mi", self.deployment_text)
        self.assertNotIn("kind: Ingress", self.deployment_text + self.service_text)
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
        self.assertEqual(jobs["chart"]["needs"], ["admit", "image"])
        self.assertIn("REGISTRY_NAMESPACE: mimranfaruqi/ci-workflows", self.release_text)
        self.assertIn("CHART_NAMESPACE: mimranfaruqi/ci-workflows/helm-charts", self.release_text)
        self.assertIn("git.faruqi.dev", self.release_text)
        self.assertIn("secrets.FORGEJO_REGISTRY_USERNAME", self.release_text)
        self.assertIn("secrets.FORGEJO_REGISTRY_TOKEN", self.release_text)
        self.assertIn("helm package", self.release_text)
        self.assertIn('--app-version "${VERSION}"', self.release_text)
        self.assertIn("skopeo inspect", self.release_text)
        self.assertIn("helm pull", self.release_text)
        self.assertNotIn(":latest", self.release_text)
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
