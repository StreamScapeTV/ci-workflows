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

    def test_workflow_call_shapes_match_or_await_only_serialized_publish_registration(
        self,
    ) -> None:
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

        desired_publish_inputs = {
            "admitted_sha",
            "product_id",
            "release_version",
            "values_profile",
            "policy_path",
            "image_digest",
            "immutable_references_json",
        }
        self.assertEqual(set(publish_call["inputs"]), desired_publish_inputs)
        registered_publish_inputs = {
            entry["name"] for entry in self.public_publish["inputs"]
        }
        self.assertTrue(registered_publish_inputs <= desired_publish_inputs)
        self.assertIn(
            desired_publish_inputs - registered_publish_inputs,
            (set(), {"image_digest", "immutable_references_json"}),
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

    def test_called_workflow_source_identity_is_not_caller_workflow_identity(self) -> None:
        for text in (self.validate_text, self.publish_text):
            self.assertIn("${{ job.workflow_repository }}", text)
            self.assertIn("${{ job.workflow_sha }}", text)
            self.assertIn('test "${CALLED_WORKFLOW_REPOSITORY}" = "StreamScapeTV/ci-workflows"', text)
            self.assertNotIn("github.workflow_sha", text)
            self.assertNotIn("GITHUB_WORKFLOW_SHA", text)
            self.assertEqual(text.count("persist-credentials: false"), 2)

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
            self.assertNotIn("secrets: inherit", text)
            self.assertIn(
                "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
                text,
            )
            self.assertIn("actions/cleanup-workspace", text)
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
