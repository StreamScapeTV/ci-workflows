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
HELM_CORE_SHA = "2db6c709c3faa4e99a67fe029628284cf0e60f80"
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"


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
        product_contract = json.loads(
            (ROOT / "contracts/public-workflows/products.json").read_text(
                encoding="utf-8"
            )
        )
        cls.public_validate = next(
            item
            for item in product_contract["workflows"]
            if item["api_name"] == "helm.validate"
        )
        cls.public_publish = next(
            item
            for item in product_contract["workflows"]
            if item["api_name"] == "helm.publish"
        )

    def test_workflow_call_shapes_remain_backward_compatible(self) -> None:
        self.assertEqual(set(self.validate["on"]), {"workflow_call"})
        self.assertEqual(set(self.publish["on"]), {"workflow_call"})
        validate_call = self.validate["on"]["workflow_call"]
        publish_call = self.publish["on"]["workflow_call"]
        self.assertEqual(
            {entry["name"] for entry in self.public_validate["inputs"]},
            set(validate_call["inputs"]),
        )
        self.assertEqual(
            set(self.public_validate["outputs"]),
            set(validate_call["outputs"]),
        )
        self.assertEqual(
            {entry["name"] for entry in self.public_publish["inputs"]},
            set(publish_call["inputs"]),
        )
        self.assertEqual(
            set(self.public_publish["outputs"]),
            set(publish_call["outputs"]),
        )
        self.assertEqual(
            set(publish_call["secrets"]),
            {"registry_username", "registry_token"},
        )
        self.assertEqual(self.public_validate["status"], "implemented")
        self.assertEqual(self.public_publish["status"], "implemented")

    def test_reusable_workflows_use_immutable_simple_helm_checkpoint(self) -> None:
        for text in (self.validate_text, self.publish_text):
            self.assertNotIn("actions/checkout@", text)
            self.assertNotIn("${{ job.workflow_repository }}", text)
            self.assertNotIn("${{ job.workflow_sha }}", text)
            self.assertNotIn("path: .ciw", text)
            self.assertNotIn("secrets: inherit", text)
            self.assertIn(
                f"StreamScapeTV/ci-workflows/actions/exact-checkout@{FOUNDATION_SHA}",
                text,
            )
            self.assertIn(
                f"StreamScapeTV/ci-workflows/actions/prepare-workspace@{FOUNDATION_SHA}",
                text,
            )
            self.assertIn(
                f"StreamScapeTV/ci-workflows/actions/cleanup-workspace@{FOUNDATION_SHA}",
                text,
            )
        self.assertIn(
            f"StreamScapeTV/ci-workflows/actions/validate-helm@{HELM_CORE_SHA}",
            self.validate_text,
        )
        self.assertIn(
            f"StreamScapeTV/ci-workflows/actions/publish-helm@{HELM_CORE_SHA}",
            self.publish_text,
        )

    def test_publication_policy_is_caller_owned_but_write_is_tag_push_only(self) -> None:
        plan_steps = self.publish["jobs"]["plan"]["steps"]
        guard = next(
            step for step in plan_steps
            if step.get("name") == "Require an exact product tag push"
        )
        run = guard["run"]
        self.assertIn('test "${EVENT_NAME}" = push', run)
        self.assertIn('test "${REF_TYPE}" = tag', run)
        self.assertIn('test "${EVENT_SHA}" = "${ADMITTED_SHA}"', run)
        self.assertNotIn("resolve-release-tag", self.publish_text)
        self.assertNotIn("workflow_dispatch", self.publish_text)
        self.assertNotIn("existing-tag", self.publish_text)
        self.assertNotIn("release_mode:", self.publish_text)

    def test_core_publication_has_no_mandatory_image_evidence_or_read_back(self) -> None:
        lowered = self.publish_text.casefold()
        self.assertIn("image_digest:", self.publish_text)
        self.assertIn("immutable_references_json:", self.publish_text)
        self.assertNotIn("image_digest: ${{ inputs.image_digest }}", self.publish_text)
        self.assertNotIn(
            "immutable_references_json: ${{ inputs.immutable_references_json }}",
            self.publish_text,
        )
        for retired in (
            "actions/measure-helm",
            "resolve-release-tag",
            "scripts/ci/helm_release.py",
            "skopeo",
            "pull read-back",
            "read_back",
            "remote_manifest",
            "runner evidence",
        ):
            self.assertNotIn(retired, lowered)

    def test_jobs_use_contract_resolved_runner_without_caller_control(self) -> None:
        for workflow, text, job in (
            (self.validate, self.validate_text, "validate"),
            (self.publish, self.publish_text, "publish"),
        ):
            self.assertEqual(workflow["permissions"], {"contents": "read"})
            self.assertEqual(
                workflow["jobs"]["plan"]["runs-on"],
                ["linux", "amd64", "general", "small"],
            )
            self.assertEqual(
                workflow["jobs"][job]["runs-on"],
                "${{ fromJSON(needs.plan.outputs.runs_on_json) }}",
            )
            self.assertNotIn("self-hosted", text)
            self.assertIn("if: always()", text)

    def test_actions_are_thin_and_publication_ignores_legacy_evidence_inputs(self) -> None:
        for action, text, operation in (
            (self.validate_action, self.validate_action_text, "validate"),
            (self.publish_action, self.publish_action_text, "publish"),
        ):
            self.assertEqual(action["runs"]["using"], "composite")
            self.assertEqual(len(action["runs"]["steps"]), 1)
            run = action["runs"]["steps"][0]["run"]
            self.assertIn("scripts/ci/ciw.py", run)
            self.assertIn("helm", run)
            self.assertIn(operation, run)
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
        for token in (
            "upload-artifact",
            "download-artifact",
            "actions/cache",
            "kubectl",
            "kubeconfig",
            "sops",
            "flux reconcile",
        ):
            self.assertNotIn(token, lowered)
        self.assertIn(
            "Remove Helm package, registry, and credential state",
            self.publish_text,
        )
        self.assertIn("Verify zero Helm publication residue", self.publish_text)


if __name__ == "__main__":
    unittest.main()
