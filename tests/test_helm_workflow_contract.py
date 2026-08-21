from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader


ROOT = Path(__file__).resolve().parents[1]
VALIDATE_WORKFLOW = ROOT / ".github/workflows/reusable-helm-validate.yml"
PUBLISH_WORKFLOW = ROOT / ".github/workflows/reusable-helm-publish.yml"
VALIDATE_ACTION = ROOT / "actions/validate-helm/action.yml"
PUBLISH_ACTION = ROOT / "actions/publish-helm/action.yml"
HELM_CORE_SHA = "7b17879f21fbf029708d6a404a9dd12d75503a52"
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"
EXECUTION_BACKEND_SHA = "01d1d10bafcc4fc1e4c51663f72b08f694dc4e35"


class HelmWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validate_text = VALIDATE_WORKFLOW.read_text(encoding="utf-8")
        cls.publish_text = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
        cls.validate = yaml.load(cls.validate_text, Loader=ActionsLoader)
        cls.publish = yaml.load(cls.publish_text, Loader=ActionsLoader)
        cls.validate_action_text = VALIDATE_ACTION.read_text(encoding="utf-8")
        cls.publish_action_text = PUBLISH_ACTION.read_text(encoding="utf-8")
        cls.validate_action = yaml.load(cls.validate_action_text, Loader=ActionsLoader)
        cls.publish_action = yaml.load(cls.publish_action_text, Loader=ActionsLoader)
        publication_contract = json.loads(
            (ROOT / "contracts/public-workflows/products.json").read_text(encoding="utf-8")
        )
        cls.public_validate = next(item for item in publication_contract["workflows"] if item["api_name"] == "helm.validate")
        cls.public_publish = next(item for item in publication_contract["workflows"] if item["api_name"] == "helm.publish")

    def test_public_v2_contract_is_identity_free_while_yaml_migration_is_pending(self) -> None:
        self.assertEqual(set(self.validate["on"]), {"workflow_call"})
        self.assertEqual(set(self.publish["on"]), {"workflow_call"})
        self.assertEqual("2.0.0", self.public_validate["api_version"])
        self.assertEqual("2.0.0", self.public_publish["api_version"])
        self.assertEqual("migration-pending", self.public_validate["status"])
        self.assertEqual("migration-pending", self.public_publish["status"])
        validate_inputs = {entry["name"] for entry in self.public_validate["inputs"]}
        publish_inputs = {entry["name"] for entry in self.public_publish["inputs"]}
        self.assertTrue({"chart_name", "chart_path", "values_path", "policy_path"} <= validate_inputs)
        self.assertTrue({"chart_name", "chart_path", "values_path", "policy_path"} <= publish_inputs)
        self.assertNotIn("product_id", validate_inputs)
        self.assertNotIn("product_id", publish_inputs)
        self.assertEqual(set(self.public_validate["outputs"]), set(self.validate["on"]["workflow_call"]["outputs"]))
        self.assertEqual(set(self.public_publish["outputs"]), set(self.publish["on"]["workflow_call"]["outputs"]))
        self.assertEqual(set(self.publish["on"]["workflow_call"]["secrets"]), {"registry_username", "registry_token"})

    def test_reusable_workflows_use_reviewed_simple_helm_checkpoint(self) -> None:
        for text in (self.validate_text, self.publish_text):
            self.assertNotIn("actions/checkout@", text)
            self.assertNotIn("${{ job.workflow_repository }}", text)
            self.assertNotIn("${{ job.workflow_sha }}", text)
            self.assertNotIn("path: .ciw", text)
            self.assertNotIn("secrets: inherit", text)
            self.assertIn(f"StreamScapeTV/ci-workflows/actions/exact-checkout@{FOUNDATION_SHA}", text)
            self.assertIn(f"StreamScapeTV/ci-workflows/actions/prepare-workspace@{FOUNDATION_SHA}", text)
            self.assertIn(f"StreamScapeTV/ci-workflows/actions/cleanup-workspace@{FOUNDATION_SHA}", text)
        self.assertEqual(self.validate_text.count(f"StreamScapeTV/ci-workflows/actions/validate-helm@{HELM_CORE_SHA}"), 4)
        self.assertEqual(self.publish_text.count(f"StreamScapeTV/ci-workflows/actions/publish-helm@{HELM_CORE_SHA}"), 4)

    def test_publication_event_and_version_policy_are_caller_owned(self) -> None:
        self.assertNotIn("Require an exact product tag push", self.publish_text)
        self.assertNotIn("github.ref_type", self.publish_text)
        self.assertNotIn("github.event_name", self.publish_text)
        self.assertNotIn("resolve-release-tag", self.publish_text)
        self.assertNotIn("workflow_dispatch", self.publish_text)
        self.assertNotIn("existing-tag", self.publish_text)
        self.assertNotIn("release_mode:", self.publish_text)
        self.assertIn("release_version: ${{ inputs.release_version }}", self.publish_text)
        self.assertIn("admitted_sha: ${{ inputs.admitted_sha }}", self.publish_text)

    def test_core_publication_has_no_mandatory_image_evidence_or_read_back(self) -> None:
        lowered = self.publish_text.casefold()
        self.assertIn("image_digest:", self.publish_text)
        self.assertIn("immutable_references_json:", self.publish_text)
        self.assertNotIn("image_digest: ${{ inputs.image_digest }}", self.publish_text)
        self.assertNotIn("immutable_references_json: ${{ inputs.immutable_references_json }}", self.publish_text)
        for retired in ("actions/measure-helm", "resolve-release-tag", "scripts/ci/helm_release.py", "skopeo", "pull read-back", "read_back", "remote_manifest", "runner evidence"):
            self.assertNotIn(retired, lowered)

    def test_validation_has_bounded_backend_while_publication_keeps_current_runner(self) -> None:
        validate_inputs = self.validate["on"]["workflow_call"]["inputs"]
        self.assertEqual("organization", validate_inputs["execution_backend"]["default"])
        self.assertEqual(self.validate["permissions"], {"contents": "read"})
        self.assertEqual(self.publish["permissions"], {"contents": "read"})
        self.assertEqual(self.validate["jobs"]["plan"]["runs-on"], ["ubuntu-latest"])
        self.assertEqual(
            self.validate["jobs"]["validate"]["runs-on"],
            "${{ fromJSON(needs.plan.outputs.runs_on_json) }}",
        )
        backend = next(
            step
            for step in self.validate["jobs"]["plan"]["steps"]
            if step.get("id") == "backend"
        )
        self.assertEqual(
            f"StreamScapeTV/ci-workflows/actions/resolve-execution-backend@{EXECUTION_BACKEND_SHA}",
            backend["uses"],
        )
        self.assertEqual("helm.validate", backend["with"]["workflow_api"])
        self.assertEqual("${{ inputs.execution_backend }}", backend["with"]["execution_backend"])
        self.assertEqual("${{ steps.plan.outputs.runner_profile }}", backend["with"]["runner_profile"])
        self.assertEqual(self.publish["jobs"]["plan"]["runs-on"], ["linux", "amd64", "general", "small"])
        self.assertEqual(
            self.publish["jobs"]["publish"]["runs-on"],
            "${{ fromJSON(needs.plan.outputs.runs_on_json) }}",
        )
        for text in (self.validate_text, self.publish_text):
            self.assertNotIn("self-hosted", text)
            self.assertIn("if: always()", text)

    def test_actions_are_thin_and_do_not_require_action_lock_bootstrap(self) -> None:
        for action, text, operation in ((self.validate_action, self.validate_action_text, "validate"), (self.publish_action, self.publish_action_text, "publish")):
            self.assertEqual(action["runs"]["using"], "composite")
            self.assertEqual(len(action["runs"]["steps"]), 1)
            run = action["runs"]["steps"][0]["run"]
            self.assertIn("scripts/ci/ciw.py", run)
            self.assertIn("PYTHONPATH", run)
            self.assertIn("helm", run)
            self.assertIn(operation, run)
            self.assertNotIn("bootstrap_validation_runtime.py", run)
            self.assertNotIn("action-tool-lock.json", run)
            for token in ("eval ", "kubectl", "sops", "docker "):
                self.assertNotIn(token, run.casefold())
            self.assertNotIn("runner", action["inputs"])
            self.assertNotIn("registry_host", action["inputs"])
        self.assertIn("image_digest", self.publish_action["inputs"])
        self.assertIn("immutable_references_json", self.publish_action["inputs"])
        self.assertNotIn("INPUT_IMAGE_DIGEST", self.publish_action_text)
        self.assertNotIn("INPUT_IMMUTABLE_REFERENCES_JSON", self.publish_action_text)
        self.assertNotIn("scripts/ci/helm_release.py", self.publish_action_text)

    def test_publish_workflow_forbids_deployment_cache_and_artifacts(self) -> None:
        lowered = self.publish_text.casefold()
        for token in ("upload-artifact", "download-artifact", "actions/cache", "kubectl", "kubeconfig", "sops", "flux reconcile"):
            self.assertNotIn(token, lowered)
        self.assertIn("Remove Helm package, registry, and credential state", self.publish_text)
        self.assertIn("Verify zero Helm publication residue", self.publish_text)


if __name__ == "__main__":
    unittest.main()
