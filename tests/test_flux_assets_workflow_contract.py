from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-flux-infrastructure-assets.yml"
ACTION = ROOT / "actions/flux-assets/action.yml"
CONTRACT = ROOT / "contracts/flux-infrastructure-products.json"
SCHEMA = ROOT / "contracts/flux-infrastructure-products.schema.json"
PUBLIC = ROOT / "contracts/public-workflows/products.json"


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

    def test_composite_action_is_thin_and_has_no_infrastructure_authority(self) -> None:
        text = ACTION.read_text(encoding="utf-8")
        self.assertIn("scripts/ci/flux_assets.py", text)
        self.assertNotIn("registry_username", text)
        self.assertNotIn("registry_token", text)
        self.assertNotIn("kubectl", text)
        self.assertNotIn("sops", text.lower())
        self.assertNotIn("buildah push", text.lower())
        self.assertNotIn("helm push", text.lower())
        run_blocks = re.findall(r"(?ms)^      run: \|\n(.*?)(?=\n\S|\Z)", text)
        self.assertLessEqual(sum(block.count("\n") for block in run_blocks), 16)

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
