from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/reusable-python.yml"
ACTION_PATH = ROOT / "actions/validate-python/action.yml"


class PythonWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.workflow = yaml.load(cls.workflow_text, Loader=ActionsLoader)
        cls.action_text = ACTION_PATH.read_text(encoding="utf-8")
        cls.action = yaml.load(cls.action_text, Loader=ActionsLoader)
        cls.python_contract = json.loads(
            (ROOT / "contracts/python-validation.json").read_text(encoding="utf-8")
        )
        public = json.loads(
            (ROOT / "contracts/public-workflows/validation.json").read_text(
                encoding="utf-8"
            )
        )
        cls.public_record = next(
            item
            for item in public["workflows"]
            if item["api_name"] == "validation.python"
        )

    def test_workflow_call_only_api_matches_the_implemented_public_record(self) -> None:
        self.assertEqual(set(self.workflow["on"]), {"workflow_call"})
        call = self.workflow["on"]["workflow_call"]
        self.assertEqual(
            set(call["inputs"]),
            {
                "admitted_sha",
                "validation_profile",
                "version_file",
                "working_directory",
                "command_profile",
                "script_path",
                "artifact_exception_id",
            },
        )
        self.assertEqual(
            set(call["outputs"]),
            {"result", "test_summary", "artifact_exception_used"},
        )
        self.assertEqual(call.get("secrets", {}), {})
        self.assertEqual(self.public_record["status"], "implemented")
        self.assertEqual(self.public_record["api_version"], "1.0.0")
        self.assertEqual(
            {item["name"] for item in self.public_record["inputs"]},
            set(call["inputs"]),
        )
        self.assertEqual(
            set(self.public_record["outputs"]),
            set(call["outputs"]),
        )
        self.assertEqual(self.public_record["stable_check_name"], "CI / Python validation")

    def test_workflow_uses_general_linux_planner_and_exact_planner_runner_output(self) -> None:
        jobs = self.workflow["jobs"]
        self.assertEqual(set(jobs), {"plan", "validate"})
        self.assertEqual(
            jobs["plan"]["runs-on"], ["linux", "amd64", "general"]
        )
        self.assertEqual(
            jobs["validate"]["runs-on"],
            "${{ fromJSON(needs.plan.outputs.runs_on_json) }}",
        )
        self.assertEqual(jobs["validate"]["timeout-minutes"], 120)
        self.assertEqual(jobs["validate"]["name"], "CI / Python validation")
        self.assertNotIn("self-hosted", self.workflow_text)
        self.assertNotIn("runs-on: portable", self.workflow_text)
        self.assertNotIn("docker-capable", self.workflow_text)

    def test_exact_central_and_caller_source_are_verified(self) -> None:
        self.assertEqual(self.workflow_text.count("repository: StreamScapeTV/ci-workflows"), 2)
        self.assertEqual(self.workflow_text.count("ref: ${{ github.workflow_sha }}"), 2)
        self.assertEqual(self.workflow_text.count("persist-credentials: false"), 2)
        self.assertEqual(self.workflow_text.count("set-safe-directory: false"), 2)
        self.assertEqual(self.workflow_text.count('test "$(git rev-parse HEAD)" = "${GITHUB_WORKFLOW_SHA}"'), 2)
        self.assertIn("uses: ./.ciw/actions/exact-checkout", self.workflow_text)
        self.assertIn("admitted_sha: ${{ inputs.admitted_sha }}", self.workflow_text)
        self.assertIn('test "$(git rev-parse HEAD)" = "${{ inputs.admitted_sha }}"', self.workflow_text)
        self.assertIn("git status --porcelain --untracked-files=all", self.workflow_text)

    def test_shared_foundation_sequence_is_marker_bound_and_cleanup_is_unconditional(self) -> None:
        source = self.workflow_text
        validate_job = source.index("\n  validate:\n")
        planner_action = source.index("uses: ./.ciw/actions/validate-python")
        self.assertLess(planner_action, validate_job)
        self.assertEqual(
            source.count("uses: ./.ciw/actions/validate-python"),
            2,
        )
        validation_source = source[validate_job:]
        sequence = [
            "uses: ./.ciw/actions/exact-checkout",
            "uses: ./.ciw/actions/prepare-workspace",
            "uses: ./.ciw/actions/verify-toolchain",
            "uses: ./.ciw/actions/validate-python",
            "uses: ./.ciw/actions/render-evidence",
            "uses: ./.ciw/actions/cleanup-workspace",
        ]
        positions = [validation_source.index(value) for value in sequence]
        self.assertEqual(positions, sorted(positions))
        self.assertRegex(
            validation_source,
            r"- id: cleanup\n        name: Remove and verify all registered Python state\n        if: always\(\)",
        )
        self.assertIn("cache_mode: disabled", validation_source)
        self.assertNotIn("actions/upload-artifact", source)
        self.assertNotIn("actions/download-artifact", source)
        self.assertNotIn("secrets: inherit", source)

    def test_action_is_thin_and_exposes_no_generic_control_surface(self) -> None:
        self.assertEqual(self.action["runs"]["using"], "composite")
        self.assertEqual(len(self.action["runs"]["steps"]), 1)
        step = self.action["runs"]["steps"][0]
        self.assertIn("scripts/ci/ciw.py", step["run"])
        self.assertIn("python validate", step["run"])
        self.assertIn("--phase", step["run"])
        self.assertIn("--source-root source", step["run"])
        inputs = set(self.action["inputs"])
        self.assertEqual(
            inputs,
            {
                "phase",
                "admitted_sha",
                "validation_profile",
                "command_profile",
                "working_directory",
                "version_file",
                "script_path",
                "artifact_exception_id",
            },
        )
        forbidden = set(self.python_contract["forbidden_inputs"])
        self.assertTrue(inputs.isdisjoint(forbidden))
        for token in ("eval ", "source ", "curl ", "rm -rf", "docker "):
            self.assertNotIn(token, step["run"])

    def test_runtime_and_postgres_identities_are_exact_and_not_workflow_inputs(self) -> None:
        public_inputs = set(self.workflow["on"]["workflow_call"]["inputs"])
        self.assertTrue(
            public_inputs.isdisjoint(
                {
                    "runtime",
                    "python_image",
                    "postgres_image",
                    "service_image",
                    "database_url",
                    "database_password",
                    "container_engine",
                    "storage_driver",
                    "runner",
                    "runner_labels",
                }
            )
        )
        for identifier, runtime in self.python_contract["runtimes"].items():
            if runtime["kind"] == "host":
                self.assertEqual(runtime["python_version"], "3.12.13")
                continue
            with self.subTest(runtime=identifier):
                self.assertRegex(runtime["digest"], r"^sha256:[0-9a-f]{64}$")
                self.assertNotEqual(runtime["tag"], "latest")

    def test_docs_and_command_registry_cover_the_public_workflow(self) -> None:
        workflow_doc = (ROOT / "docs/workflows/python.md").read_text(encoding="utf-8")
        architecture = (
            ROOT / "docs/architecture/python-validation.md"
        ).read_text(encoding="utf-8")
        ciw_contract = json.loads(
            (ROOT / "contracts/ciw-commands.json").read_text(encoding="utf-8")
        )
        commands = {
            f"{item['domain']} {item['operation']}": item
            for item in ciw_contract["commands"]
        }
        self.assertIn("validation.python", workflow_doc)
        self.assertIn("podman-postgres", workflow_doc)
        self.assertIn("ephemeral", architecture)
        self.assertIn("ciw python validate", architecture)
        self.assertIn("python validate", commands)
        self.assertEqual(
            commands["python validate"]["failure"],
            "PythonValidationError.code",
        )


if __name__ == "__main__":
    unittest.main()
