from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-script.yml"
PUBLIC = ROOT / "contracts/public-workflows/validation.json"
EXECUTION_BACKEND_REF = "main"


class SimpleScriptWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.load(cls.source, Loader=ActionsLoader)
        cls.public = json.loads(PUBLIC.read_text(encoding="utf-8"))

    def test_public_surface_is_bounded_and_generic(self) -> None:
        self.assertEqual(set(self.workflow["on"]), {"workflow_call"})
        call = self.workflow["on"]["workflow_call"]
        self.assertEqual(
            set(call["inputs"]),
            {
                "execution_backend",
                "admitted_sha",
                "validation_profile",
                "working_directory",
                "script_path",
            },
        )
        self.assertEqual(call.get("secrets", {}), {})
        self.assertEqual(set(call["outputs"]), {"result"})
        self.assertEqual(call["inputs"]["execution_backend"]["default"], "organization")
        self.assertEqual(call["inputs"]["working_directory"]["default"], ".")
        for forbidden in (
            "runner",
            "runner_labels",
            "runs_on",
            "shell",
            "arguments",
            "environment_json",
            "container_engine",
            "registry_host",
            "secret_name",
        ):
            self.assertNotIn(forbidden, call["inputs"])
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})

    def test_runner_selection_is_semantic_and_backend_bounded(self) -> None:
        jobs = self.workflow["jobs"]
        self.assertEqual(set(jobs), {"plan", "plan_organization", "validate"})
        self.assertEqual(jobs["plan"]["runs-on"], ["ubuntu-latest"])
        self.assertEqual(jobs["plan_organization"]["runs-on"], ["linux", "amd64", "general", "small"])
        self.assertEqual(
            jobs["validate"]["runs-on"],
            "${{ fromJSON(needs.plan.outputs.runs_on_json || needs.plan_organization.outputs.runs_on_json) }}",
        )
        self.assertEqual(jobs["validate"]["needs"], ["plan", "plan_organization"])
        self.assertEqual(
            self.source.count(
                f"StreamScapeTV/ci-workflows/actions/resolve-execution-backend@{EXECUTION_BACKEND_REF}"
            ),
            2,
        )
        self.assertIn("validation_profile must be general, mobile, or apple", self.source)
        self.assertIn("specialized script validation rejects fork pull requests", self.source)
        self.assertIn("workflow_api: validation.script", self.source)
        self.assertNotIn("runs-on: ${{ inputs", self.source)
        self.assertNotIn("self-hosted", self.source)

    def test_exact_source_script_boundary_and_clean_tree_are_enforced(self) -> None:
        validate = self.workflow["jobs"]["validate"]
        steps = validate["steps"]
        checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
        self.assertEqual(checkout["with"]["ref"], "${{ inputs.admitted_sha }}")
        self.assertEqual(checkout["with"]["fetch-depth"], 1)
        self.assertFalse(checkout["with"]["persist-credentials"])
        self.assertFalse(checkout["with"]["set-safe-directory"])
        execute = next(step for step in steps if step.get("id") == "execute")
        run = execute["run"]
        self.assertIn('[[ "${SCRIPT_PATH}" =~ ^[A-Za-z0-9._/-]+$ && "${WORKING_DIRECTORY}" =~ ^[A-Za-z0-9._/-]+$ ]]', run)
        self.assertIn('[[ "${SCRIPT_PATH}" != /*', run)
        self.assertIn('test -f "${script}" && test ! -L "${script}" && test -x "${script}"', run)
        self.assertIn('[[ "${work_real}" == "${root}" || "${work_real}" == "${root}/"* ]]', run)
        self.assertIn('[[ "${script_real}" == "${root}/"* ]]', run)
        self.assertIn('"${script_real}"', run)
        self.assertNotIn("eval ", run)
        self.assertNotIn("bash -c", run)
        self.assertNotIn("sh -c", run)
        clean = next(step for step in steps if step.get("id") == "clean")
        self.assertEqual(clean["if"], "always()")
        self.assertIn("git status --porcelain", clean["run"])
        result = validate["outputs"]["result"]
        self.assertIn("steps.execute.outcome", result)
        self.assertIn("steps.clean.outcome", result)

    def test_required_path_avoids_retired_security_ceremony_and_keeps_cleanup(self) -> None:
        for forbidden in (
            "action-tool-lock",
            "attestation",
            "provenance",
            "id-token:",
            "actions/cache",
            "registry",
            "release-manifest",
            "evidence-manifest",
            "artifact_manifest",
        ):
            self.assertNotIn(forbidden, self.source)
        self.assertIn("clean", self.workflow["jobs"]["validate"]["outputs"]["result"])

    def test_registry_keeps_generic_script_api_without_retired_conformance(self) -> None:
        row = next(
            item
            for item in self.public["workflows"]
            if item["api_name"] == "validation.script"
        )
        self.assertEqual(row["file"], ".github/workflows/reusable-script.yml")
        self.assertEqual(row["permission_profile"], "validation-read")
        self.assertEqual(row["semantic_runner_profile"], "portable")
        self.assertEqual(row["secrets"], [])
        self.assertEqual(row["outputs"], ["result"])
        self.assertEqual(row["repository_owned_hooks"], ["script_path"])
        self.assertEqual(
            [item["name"] for item in row["inputs"]],
            [
                "execution_backend",
                "admitted_sha",
                "validation_profile",
                "working_directory",
                "script_path",
            ],
        )
        self.assertNotIn("conformance", row["api_name"])
        self.assertNotIn("repository", row)


if __name__ == "__main__":
    unittest.main()
