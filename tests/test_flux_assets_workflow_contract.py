from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-flux-infrastructure-assets.yml"
INTERNAL = ROOT / ".github/workflows/internal-flux-assets.yml"
ACTION = ROOT / "actions/flux-assets/action.yml"
SCRIPT = ROOT / "scripts/ci/flux_assets.py"
CONTRACT = ROOT / "contracts/flux-infrastructure-products.json"
SCHEMA = ROOT / "contracts/flux-infrastructure-products.schema.json"
PUBLIC = ROOT / "contracts/public-workflows/products.json"
TRUST_FIELDS = {
    "source_event_name",
    "source_ref_type",
    "source_ref_name",
    "source_default_branch",
}


class FluxAssetsWorkflowContractTests(unittest.TestCase):
    def test_registered_public_shape_is_kept_exact(self) -> None:
        public = json.loads(PUBLIC.read_text(encoding="utf-8"))
        record = next(
            item for item in public["workflows"] if item["api_name"] == "flux.assets"
        )
        self.assertEqual(
            record["file"], ".github/workflows/reusable-flux-infrastructure-assets.yml"
        )
        self.assertEqual(
            [item["name"] for item in record["inputs"]],
            [
                "admitted_sha",
                "product_id",
                "release_version",
                "operation",
                "policy_path",
                "request_id",
            ],
        )
        self.assertEqual(record["secrets"], ["registry_username", "registry_token"])
        self.assertEqual(
            record["outputs"],
            [
                "result",
                "immutable_references_json",
                "release_manifest_sha256",
                "request_id",
            ],
        )
        self.assertEqual(record["stable_check_name"], "Release / Flux infrastructure assets")

    def test_public_workflow_is_call_only_bounded_and_review_only(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^on:\n  workflow_call:\n")
        self.assertIn("name: Release / Flux infrastructure assets", text)
        self.assertIn("runs-on: [linux, amd64, general]", text)
        self.assertIn("runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}", text)
        self.assertIn('test "${CALLER_REPOSITORY}" = "StreamScapeTV/flux"', text)
        self.assertNotIn("secrets: inherit", text)
        self.assertNotIn("pull_request_target", text)
        self.assertNotIn("issue_comment", text)
        self.assertNotIn("workflow_run", text)
        for forbidden in (
            "kubectl ",
            "flux reconcile",
            "helm upgrade",
            "buildah push",
            "docker push",
            "actions/upload-artifact",
            "latest",
        ):
            self.assertNotIn(forbidden, text.lower())
        self.assertIn("phase: cleanup", text)
        self.assertIn("phase: residue", text)
        self.assertIn("if: always()", text)

    def test_workflow_does_not_pretend_unmerged_dependencies_ran(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('dependency_evidence_json: "{}"', text)
        self.assertNotIn("reusable-oci-build.yml", text)
        self.assertNotIn("reusable-oci-publish.yml", text)
        self.assertNotIn("reusable-helm-validate.yml", text)
        self.assertNotIn("reusable-helm-publish.yml", text)

    def test_internal_leaf_has_no_spoofable_trust_inputs(self) -> None:
        source = INTERNAL.read_text(encoding="utf-8")
        workflow = yaml.load(source, Loader=ActionsLoader)
        inputs = set(workflow["on"]["workflow_call"]["inputs"])
        self.assertEqual(
            inputs,
            {
                "admitted_sha",
                "product_id",
                "release_version",
                "operation",
                "policy_path",
                "request_id",
                "dependency_evidence_json",
            },
        )
        self.assertTrue(TRUST_FIELDS.isdisjoint(inputs))
        for trust_field in TRUST_FIELDS:
            self.assertNotIn(f"{trust_field}:", source)
        self.assertIn("workflow_call:", source)
        self.assertIn("runs-on: [linux, amd64, general]", source)
        self.assertIn("timeout-minutes: 30", source)
        self.assertIn('test "${CALLER_REPOSITORY}" = "StreamScapeTV/flux"', source)
        self.assertNotIn("runs_on_json", source)
        self.assertNotIn("uses: ./.github/workflows/", source)
        self.assertNotIn("KUBECONFIG", source)
        self.assertNotIn("sops", source.casefold())
        self.assertIn("Confirm zero Actions artifacts", source)

    def test_composite_action_exposes_no_trust_metadata_surface(self) -> None:
        text = ACTION.read_text(encoding="utf-8")
        action = yaml.load(text, Loader=ActionsLoader)
        inputs = set(action["inputs"])
        self.assertTrue(TRUST_FIELDS.isdisjoint(inputs))
        self.assertIn("scripts/ci/flux_assets.py", text)
        for flag in (
            "--source-event-name",
            "--source-ref-type",
            "--source-ref-name",
            "--source-default-branch",
        ):
            self.assertNotIn(flag, text)
        self.assertNotIn("registry_username", text)
        self.assertNotIn("registry_token", text)
        self.assertNotIn("kubectl", text)
        self.assertNotIn("sops", text.lower())
        self.assertNotIn("buildah push", text.lower())
        self.assertNotIn("helm push", text.lower())
        run_blocks = re.findall(r"(?ms)^      run: \|\n(.*?)(?=\n\S|\Z)", text)
        self.assertLessEqual(sum(block.count("\n") for block in run_blocks), 16)

    def test_cli_derives_trust_from_github_runtime(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('os.environ.get("GITHUB_EVENT_NAME", "")', source)
        self.assertIn('os.environ.get("GITHUB_REF_TYPE", "")', source)
        self.assertIn('os.environ.get("GITHUB_REF_NAME", "")', source)
        self.assertIn('os.environ.get("GITHUB_EVENT_PATH", "")', source)
        for flag in (
            "--source-event-name",
            "--source-ref-type",
            "--source-ref-name",
            "--source-default-branch",
        ):
            self.assertNotIn(flag, source)

    def test_inventory_schema_and_contract_are_json_and_forbid_cluster_authority(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["cluster_mutation_authorized"]["const"], False)
        self.assertFalse(contract["cluster_mutation_authorized"])
        self.assertFalse(contract["kubernetes_credentials"])
        self.assertTrue(contract["latest_forbidden"])
        serialized = json.dumps(contract).lower()
        self.assertNotIn("kubeconfig", serialized)
        self.assertNotIn("serviceaccount", serialized)
        self.assertNotIn("namespace", serialized)
        self.assertNotIn("secret_name", serialized)


if __name__ == "__main__":
    unittest.main()
