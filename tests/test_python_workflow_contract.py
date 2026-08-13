from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/reusable-python.yml"
ACTION_PATH = ROOT / "actions/validate-python/action.yml"
FOUNDATION_HELPER_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"
PYTHON_ACTION_SHA = "e972aa49ad7eb38257711327387894bb44c472f4"
FOUNDATION_HELPERS = (
    "exact-checkout",
    "prepare-workspace",
    "verify-toolchain",
    "render-evidence",
    "cleanup-workspace",
)


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
            (ROOT / "contracts/public-workflows/validation.json").read_text(encoding="utf-8")
        )
        cls.public_record = next(
            item for item in public["workflows"] if item["api_name"] == "validation.python"
        )

    def test_public_api_contract(self) -> None:
        self.assertEqual(set(self.workflow["on"]), {"workflow_call"})
        call = self.workflow["on"]["workflow_call"]
        self.assertEqual(
            set(call["inputs"]),
            {
                "admitted_sha", "validation_profile", "version_file",
                "working_directory", "command_profile", "script_path",
                "artifact_exception_id",
            },
        )
        self.assertEqual(set(call["outputs"]), {"result", "test_summary", "artifact_exception_used"})
        self.assertEqual(call.get("secrets", {}), {})
        self.assertEqual(self.public_record["status"], "implemented")
        self.assertEqual(self.public_record["api_version"], "1.0.0")
        self.assertEqual({item["name"] for item in self.public_record["inputs"]}, set(call["inputs"]))
        self.assertEqual(set(self.public_record["outputs"]), set(call["outputs"]))
        self.assertEqual(self.public_record["stable_check_name"], "CI / Python validation")

    def test_planner_and_validation_runner_contract(self) -> None:
        jobs = self.workflow["jobs"]
        self.assertEqual(set(jobs), {"plan", "validate"})
        self.assertEqual(jobs["plan"]["runs-on"], ["linux", "amd64", "general"])
        self.assertEqual(jobs["validate"]["runs-on"], "${{ fromJSON(needs.plan.outputs.runs_on_json) }}")
        self.assertEqual(jobs["validate"]["timeout-minutes"], 120)
        self.assertEqual(jobs["validate"]["name"], "CI / Python validation")

    def test_private_helpers_are_immutable(self) -> None:
        self.assertEqual(
            self.workflow_text.count(
                f"StreamScapeTV/ci-workflows/actions/validate-python@{PYTHON_ACTION_SHA}"
            ),
            2,
        )
        for helper in FOUNDATION_HELPERS:
            self.assertIn(
                f"StreamScapeTV/ci-workflows/actions/{helper}@{FOUNDATION_HELPER_SHA}",
                self.workflow_text,
            )
        for forbidden in (
            "actions/checkout@", "path: .ciw", "./.ciw/actions/",
            "secrets: inherit", "private_dependency_token",
        ):
            self.assertNotIn(forbidden, self.workflow_text)

    def test_cleanup_and_exact_source_guards_remain_present(self) -> None:
        validate = self.workflow["jobs"]["validate"]
        steps = validate["steps"]
        ids = [step.get("id") for step in steps]
        self.assertIn("workspace", ids)
        self.assertIn("python", ids)
        self.assertIn("cleanup", ids)
        cleanup = next(step for step in steps if step.get("id") == "cleanup")
        self.assertEqual(cleanup["if"], "always()")
        self.assertIn("admitted_sha", self.workflow_text)
        self.assertIn("untracked-files=all", self.workflow_text)
        self.assertIn("cache_mode: disabled", self.workflow_text)

    def test_action_is_thin_and_public_inputs_remain_bounded(self) -> None:
        self.assertEqual(self.action["runs"]["using"], "composite")
        self.assertEqual(len(self.action["runs"]["steps"]), 1)
        step = self.action["runs"]["steps"][0]
        self.assertIn("scripts/ci/ciw.py", step["run"])
        self.assertIn("python validate", step["run"])
        self.assertIn("--phase", step["run"])
        inputs = set(self.action["inputs"])
        self.assertEqual(
            inputs,
            {
                "phase", "admitted_sha", "validation_profile", "command_profile",
                "working_directory", "version_file", "script_path", "artifact_exception_id",
            },
        )
        self.assertTrue(inputs.isdisjoint(set(self.python_contract["forbidden_inputs"])))

    def test_runtime_identities_are_exact_and_not_public_inputs(self) -> None:
        public_inputs = set(self.workflow["on"]["workflow_call"]["inputs"])
        self.assertTrue(
            public_inputs.isdisjoint(
                {
                    "runtime", "python_image", "postgres_image", "service_image",
                    "database_url", "database_password", "container_engine",
                    "storage_driver", "runner", "runner_labels",
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

    def test_docs_and_command_registry_cover_python_validation(self) -> None:
        workflow_doc = (ROOT / "docs/workflows/python.md").read_text(encoding="utf-8")
        architecture = (ROOT / "docs/architecture/python-validation.md").read_text(encoding="utf-8")
        ciw_contract = json.loads((ROOT / "contracts/ciw-commands.json").read_text(encoding="utf-8"))
        commands = {f"{item['domain']} {item['operation']}": item for item in ciw_contract["commands"]}
        self.assertIn("validation.python", workflow_doc)
        self.assertIn("podman-postgres", workflow_doc)
        self.assertIn("ephemeral", architecture)
        self.assertIn("ciw python validate", architecture)
        self.assertEqual(commands["python validate"]["failure"], "PythonValidationError.code")


if __name__ == "__main__":
    unittest.main()
