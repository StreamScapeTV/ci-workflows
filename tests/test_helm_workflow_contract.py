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
MEASURE_ACTION = ROOT / "actions/measure-helm/action.yml"
HELM_SHA = "f867827a41174ea5a9ad554eeea91dbb2c2c0bfa"
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"
RELEASE_TAG_SHA = "2b0443fdad002d47625386a959ebe68545cfe022"


class HelmWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validate_text = VALIDATE_WORKFLOW.read_text(encoding="utf-8")
        cls.publish_text = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
        cls.validate = yaml.load(cls.validate_text, Loader=ActionsLoader)
        cls.publish = yaml.load(cls.publish_text, Loader=ActionsLoader)
        cls.validate_action = yaml.load(
            VALIDATE_ACTION.read_text(encoding="utf-8"), Loader=ActionsLoader
        )
        cls.publish_action = yaml.load(
            PUBLISH_ACTION.read_text(encoding="utf-8"), Loader=ActionsLoader
        )
        cls.measure_action = yaml.load(
            MEASURE_ACTION.read_text(encoding="utf-8"), Loader=ActionsLoader
        )
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

    def test_workflow_call_shapes_match_public_registration(self) -> None:
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
        self.assertNotIn("runner_evidence_json", publish_call["outputs"])
        self.assertEqual(
            set(publish_call["secrets"]),
            {"registry_username", "registry_token"},
        )
        self.assertEqual(self.public_validate["status"], "implemented")
        self.assertEqual(self.public_publish["status"], "implemented")

    def test_reusable_workflows_use_only_immutable_central_actions(self) -> None:
        for text in (self.validate_text, self.publish_text):
            self.assertNotIn("actions/checkout@", text)
            self.assertNotIn("${{ job.workflow_repository }}", text)
            self.assertNotIn("${{ job.workflow_sha }}", text)
            self.assertNotIn("github.workflow_sha", text)
            self.assertNotIn("GITHUB_WORKFLOW_SHA", text)
            self.assertNotIn("path: .ciw", text)
            self.assertNotIn("./.ciw/actions/", text)
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
            f"StreamScapeTV/ci-workflows/actions/validate-helm@{HELM_SHA}",
            self.validate_text,
        )
        self.assertIn(
            f"StreamScapeTV/ci-workflows/actions/publish-helm@{HELM_SHA}",
            self.publish_text,
        )
        self.assertIn(
            f"StreamScapeTV/ci-workflows/actions/measure-helm@{HELM_SHA}",
            self.publish_text,
        )
        self.assertIn(
            f"StreamScapeTV/ci-workflows/actions/resolve-release-tag@{RELEASE_TAG_SHA}",
            self.publish_text,
        )

    def test_jobs_use_contract_resolved_runner_without_caller_control(self) -> None:
        for workflow, text, job in (
            (self.validate, self.validate_text, "validate"),
            (self.publish, self.publish_text, "publish"),
        ):
            self.assertEqual(workflow["permissions"], {"contents": "read"})
            self.assertEqual(
                workflow["jobs"]["plan"]["runs-on"],
                ["linux", "amd64", "general"],
            )
            self.assertEqual(
                workflow["jobs"][job]["runs-on"],
                "${{ fromJSON(needs.plan.outputs.runs_on_json) }}",
            )
            self.assertNotIn("self-hosted", text)
            self.assertNotIn("runs-on: mobile", text)
            self.assertIn("if: always()", text)

    def test_actions_are_thin_and_have_no_generic_privileged_surface(self) -> None:
        for action, text, operation in (
            (
                self.validate_action,
                VALIDATE_ACTION.read_text(encoding="utf-8"),
                "validate",
            ),
            (
                self.publish_action,
                PUBLISH_ACTION.read_text(encoding="utf-8"),
                "publish",
            ),
        ):
            self.assertEqual(action["runs"]["using"], "composite")
            self.assertEqual(len(action["runs"]["steps"]), 1)
            run = action["runs"]["steps"][0]["run"]
            self.assertIn("helm", run)
            self.assertIn(operation, run)
            for token in (
                "eval ",
                "source ",
                "curl ",
                "kubectl",
                "sops",
                "docker ",
            ):
                self.assertNotIn(token, run.casefold())
            self.assertNotIn("runner", action["inputs"])
            self.assertNotIn("registry_host", action["inputs"])
        self.assertEqual(
            set(self.publish_action["inputs"])
            & {"registry_username", "registry_token"},
            {"registry_username", "registry_token"},
        )
        self.assertIn("image_digest", self.publish_action["inputs"])
        self.assertIn("immutable_references_json", self.publish_action["inputs"])
        self.assertNotIn(
            "required_image_references_json", self.publish_action["inputs"]
        )

    def test_measurement_action_is_internal_bounded_and_secret_free(self) -> None:
        self.assertEqual(self.measure_action["runs"]["using"], "composite")
        self.assertEqual(
            set(self.measure_action["inputs"]),
            {"phase", "admitted_sha", "product_id"},
        )
        self.assertEqual(
            set(self.measure_action["outputs"]),
            {
                "result",
                "peak_memory_bytes",
                "peak_local_storage_bytes",
                "runner_evidence_json",
                "selected_profile",
            },
        )
        text = MEASURE_ACTION.read_text(encoding="utf-8").casefold()
        for token in ("registry", "token", "password", "kubectl", "sops", "docker "):
            self.assertNotIn(token, text)

    def test_measurement_brackets_publication_and_is_terminal_evidence(self) -> None:
        publish_job = self.publish["jobs"]["publish"]
        ids = [step.get("id") for step in publish_job["steps"]]
        self.assertLess(ids.index("measurement_start"), ids.index("helm"))
        self.assertLess(ids.index("helm"), ids.index("measurement_stop"))
        self.assertLess(ids.index("measurement_stop"), ids.index("helm_cleanup"))
        result = publish_job["outputs"]["result"]
        self.assertIn("steps.measurement_start.outcome == 'success'", result)
        self.assertIn("steps.measurement_stop.outcome == 'success'", result)
        self.assertIn("Helm runner evidence:", self.publish_text)
        self.assertIn("actions/measure-helm", self.publish_text)

    def test_publish_workflow_forbids_deployment_and_artifacts(self) -> None:
        lowered = self.publish_text.casefold()
        for token in (
            "upload-artifact",
            "download-artifact",
            "kubectl",
            "kubeconfig",
            "sops",
            "latest",
        ):
            self.assertNotIn(token, lowered)
        docs = (ROOT / "docs/architecture/helm-validation.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "pull-compare-before-push",
            (ROOT / "contracts/helm-publication.json").read_text(
                encoding="utf-8"
            ),
        )
        self.assertIn("zero", docs)
        self.assertIn("Kubernetes", docs)


if __name__ == "__main__":
    unittest.main()
