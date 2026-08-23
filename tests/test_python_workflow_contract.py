from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/reusable-python.yml"
ACTION_PATH = ROOT / "actions/validate-python/action.yml"
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"
PYTHON_ACTION_SHA = "203aaf1efcf28ff5c99a402301718f22e20ecb58"
PRIVATE_HELPERS = {
    "validate-python": PYTHON_ACTION_SHA,
    "exact-checkout": FOUNDATION_SHA,
    "prepare-workspace": FOUNDATION_SHA,
    "cleanup-workspace": FOUNDATION_SHA,
}


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

    def test_workflow_call_api_is_script_owned_and_public_record_matches(self) -> None:
        self.assertEqual(set(self.workflow["on"]), {"workflow_call"})
        call = self.workflow["on"]["workflow_call"]
        expected_inputs = {
            "execution_backend",
            "admitted_sha",
            "validation_profile",
            "python_version",
            "version_file",
            "dependency_file",
            "working_directory",
            "script_path",
            "artifact_exception_id",
        }
        self.assertEqual(set(call["inputs"]), expected_inputs)
        self.assertTrue(call["inputs"]["script_path"]["required"])
        self.assertTrue(call["inputs"]["python_version"]["required"])
        self.assertFalse(call["inputs"]["dependency_file"]["required"])
        backend = call["inputs"]["execution_backend"]
        self.assertFalse(backend["required"])
        self.assertEqual("organization", backend["default"])
        self.assertEqual("string", backend["type"])
        self.assertEqual(set(call["outputs"]), {"result", "test_summary", "artifact_exception_used"})
        self.assertEqual({}, call.get("secrets", {}))
        self.assertEqual("implemented", self.public_record["status"])
        self.assertEqual("2.0.0", self.public_record["api_version"])
        self.assertEqual(
            {item["name"] for item in self.public_record["inputs"]},
            expected_inputs,
        )
        self.assertEqual(set(self.public_record["outputs"]), set(call["outputs"]))
        self.assertEqual(["script_path"], self.public_record["repository_owned_hooks"])
        self.assertEqual("CI / Python validation", self.public_record["stable_check_name"])

    def test_old_command_registry_surface_is_absent(self) -> None:
        for token in (
            "command_profile",
            "arguments_json",
            "environment_json",
            "database_environment_variable",
            "service_image",
            "python_image",
            "runner_labels",
            "secret_name",
        ):
            self.assertNotIn(token, self.workflow_text.casefold())
        self.assertNotIn("consumers", self.python_contract)
        self.assertNotIn("command_profiles", self.python_contract)

    def test_workflow_uses_hosted_planner_and_exact_backend_runner_output(self) -> None:
        jobs = self.workflow["jobs"]
        self.assertEqual({"plan", "validate"}, set(jobs))
        self.assertEqual(["ubuntu-latest"], jobs["plan"]["runs-on"])
        self.assertEqual(
            "${{ fromJSON(needs.plan.outputs.runs_on_json) }}",
            jobs["validate"]["runs-on"],
        )
        planner = jobs["plan"]["steps"][0]
        self.assertEqual("${{ inputs.execution_backend }}", planner["with"]["execution_backend"])
        self.assertEqual("${{ inputs.python_version }}", planner["with"]["python_version"])
        self.assertEqual("${{ inputs.dependency_file }}", planner["with"]["dependency_file"])
        self.assertEqual("${{ inputs.script_path }}", planner["with"]["script_path"])
        self.assertEqual(120, jobs["validate"]["timeout-minutes"])
        self.assertEqual("CI / Python validation", jobs["validate"]["name"])
        self.assertNotIn("self-hosted", self.workflow_text)
        self.assertNotIn("docker-capable", self.workflow_text)

    def test_private_helpers_are_immutable_without_central_clone(self) -> None:
        self.assertNotIn("actions/checkout@", self.workflow_text)
        self.assertNotIn("path: .ciw", self.workflow_text)
        self.assertNotIn("./.ciw/actions/", self.workflow_text)
        self.assertNotIn("secrets: inherit", self.workflow_text)
        for helper, sha in PRIVATE_HELPERS.items():
            self.assertIn(
                f"StreamScapeTV/ci-workflows/actions/{helper}@{sha}",
                self.workflow_text,
            )

    def test_exact_source_cleanup_and_zero_artifact_contract_are_preserved(self) -> None:
        source = self.workflow_text
        validate_job = source.index("\n  validate:\n")
        validation_source = source[validate_job:]
        sequence = [
            f"uses: StreamScapeTV/ci-workflows/actions/exact-checkout@{FOUNDATION_SHA}",
            f"uses: StreamScapeTV/ci-workflows/actions/prepare-workspace@{FOUNDATION_SHA}",
            f"uses: StreamScapeTV/ci-workflows/actions/validate-python@{PYTHON_ACTION_SHA}",
            f"uses: StreamScapeTV/ci-workflows/actions/cleanup-workspace@{FOUNDATION_SHA}",
            "name: Verify exact source remained clean after cleanup",
        ]
        positions = [validation_source.index(value) for value in sequence]
        self.assertEqual(sorted(positions), positions)
        self.assertIn("if: always()", validation_source)
        self.assertIn('test "$(git rev-parse HEAD)" = "${{ inputs.admitted_sha }}"', validation_source)
        self.assertIn("git status --porcelain --untracked-files=all", validation_source)
        self.assertIn("cache_mode: disabled", validation_source)
        result = self.workflow["jobs"]["validate"]["outputs"]["result"]
        self.assertIn("steps.python.outcome", result)
        self.assertIn("steps.cleanup.outcome", result)
        self.assertIn("steps.clean.outcome", result)
        self.assertNotIn("actions/upload-artifact", source)
        self.assertNotIn("actions/download-artifact", source)

    def test_action_is_thin_and_exposes_only_bounded_inputs(self) -> None:
        self.assertEqual("composite", self.action["runs"]["using"])
        self.assertEqual(1, len(self.action["runs"]["steps"]))
        step = self.action["runs"]["steps"][0]
        script = step["run"]
        self.assertIn("type -P python3.12", script)
        self.assertIn("scripts/ci/ciw.py", script)
        self.assertIn("python validate", script)
        self.assertIn("--source-root source", script)
        inputs = set(self.action["inputs"])
        self.assertEqual(
            {
                "phase",
                "execution_backend",
                "admitted_sha",
                "validation_profile",
                "python_version",
                "working_directory",
                "version_file",
                "dependency_file",
                "script_path",
                "artifact_exception_id",
            },
            inputs,
        )
        forbidden = set(self.python_contract["forbidden_inputs"])
        self.assertTrue(inputs.isdisjoint(forbidden))
        for token in (
            "actions/setup-python",
            "setup-python",
            "apt-get",
            "sudo ",
            "eval ",
            "curl ",
            "docker ",
        ):
            self.assertNotIn(token, script)

    def test_runtime_and_postgres_identity_remain_contract_owned(self) -> None:
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
                    "database_environment_variable",
                    "container_engine",
                    "storage_driver",
                    "runner",
                    "runner_labels",
                }
            )
        )
        self.assertEqual("CIW_POSTGRES_URL", self.python_contract["postgres"]["connection_environment_variable"])
        for identifier, runtime in self.python_contract["runtimes"].items():
            if runtime["kind"] == "host":
                self.assertEqual("host-cpython-3.12", identifier)
                self.assertEqual("3.12", runtime["python_version"])
                continue
            with self.subTest(runtime=identifier):
                self.assertRegex(runtime["digest"], r"^sha256:[0-9a-f]{64}$")
                self.assertNotEqual("latest", runtime["tag"])

    def test_docs_and_ciw_registry_describe_script_contract(self) -> None:
        workflow_doc = (ROOT / "docs/workflows/python.md").read_text(encoding="utf-8")
        architecture = (ROOT / "docs/architecture/python-validation.md").read_text(encoding="utf-8")
        ciw_contract = json.loads((ROOT / "contracts/ciw-commands.json").read_text(encoding="utf-8"))
        commands = {
            f"{item['domain']} {item['operation']}": item
            for item in ciw_contract["commands"]
        }
        python_command = commands["python validate"]
        self.assertIn("validation.python", workflow_doc)
        self.assertIn("consumer-owned", workflow_doc.casefold())
        self.assertIn("CIW_POSTGRES_URL", workflow_doc)
        self.assertIn("execution_backend", workflow_doc)
        self.assertIn("consumer-owned", architecture.casefold())
        self.assertIn("ciw python validate", architecture)
        self.assertNotIn("command_profile", python_command["inputs"])
        self.assertIn("script_path", python_command["inputs"])
        self.assertIn("dependency_file", python_command["inputs"])
        self.assertEqual("PythonValidationError.code", python_command["failure"])


if __name__ == "__main__":
    unittest.main()
