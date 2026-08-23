from __future__ import annotations

import json
import unittest
from pathlib import Path

from ci_workflows import public_api

ROOT = Path(__file__).resolve().parents[1]


class PythonPublicApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = public_api.validate(ROOT)
        cls.profiles = public_api.permission_profiles(cls.data)
        cls.workflows = public_api.validate_workflows(cls.data, cls.profiles)
        cls.python = cls.workflows["validation.python"]

    def test_python_api_is_v2_implemented_and_zero_secret(self) -> None:
        self.assertEqual("2.0.0", self.python["api_version"])
        self.assertEqual("implemented", self.python["status"])
        self.assertEqual("read-only-validation", self.python["trust_class"])
        self.assertEqual("validation-read", self.python["permission_profile"])
        self.assertEqual("contract:python", self.python["semantic_runner_profile"])
        self.assertEqual([], self.python["secrets"])
        self.assertEqual("CI / Python validation", self.python["stable_check_name"])
        self.assertEqual(1, self.python["matrix_max_jobs"])
        self.assertEqual(120, self.python["timeout_minutes"])

    def test_python_inputs_expose_only_product_neutral_intent(self) -> None:
        self.assertEqual(
            {
                "execution_backend",
                "admitted_sha",
                "validation_profile",
                "python_version",
                "version_file",
                "dependency_file",
                "working_directory",
                "script_path",
                "artifact_exception_id",
            },
            {item["name"] for item in self.python["inputs"]},
        )
        self.assertEqual({"result", "test_summary", "artifact_exception_used"}, set(self.python["outputs"]))
        self.assertEqual(["script_path"], self.python["repository_owned_hooks"])
        self.assertNotIn("supported_consumers", self.python)
        self.assertNotIn("supported_products", self.python)
        self.assertNotIn("command_profile", {item["name"] for item in self.python["inputs"]})
        self.assertEqual(
            {
                "ci_workflows.python.validate",
                "ci_workflows.execution_backends.resolve_execution_backend",
                "actions/validate-python",
            },
            set(self.python["implementation_components"]),
        )

    def test_python_caller_fixture_accepts_script_and_rejects_runner_or_inline_command(self) -> None:
        base = {
            "api_name": "validation.python",
            "trust_class": "read-only-validation",
            "reference": "main",
            "event": "pull_request",
            "permissions": {"contents": "read"},
            "secrets": [],
            "inputs": {
                "admitted_sha": "0" * 40,
                "validation_profile": "audit",
                "python_version": "3.12",
                "script_path": "ci/validate.sh",
            },
        }
        self.assertIsNone(public_api.validate_caller(base, self.data, self.workflows, self.profiles))
        for forbidden, value in (
            ("runner", "buildah-high"),
            ("arbitrary_command", "pytest -q"),
        ):
            invalid = json.loads(json.dumps(base))
            invalid["inputs"][forbidden] = value
            self.assertEqual(
                "forbidden-caller-field",
                public_api.validate_caller(invalid, self.data, self.workflows, self.profiles),
            )

    def test_generated_reference_contains_v2_without_product_identity(self) -> None:
        rendered = public_api.render(self.data)
        self.assertIn("`validation.python` `2.0.0`", rendered)
        self.assertIn("`.github/workflows/reusable-python.yml`", rendered)
        self.assertIn("CI / Python validation", rendered)
        section = rendered.split("### `validation.python`", 1)[1].split("### `", 1)[0]
        self.assertIn("`python_version`", section)
        self.assertIn("`dependency_file`", section)
        self.assertIn("`script_path`", section)
        self.assertNotIn("command_profile", section)
        self.assertNotIn("postgres_enabled", section)
        self.assertNotIn("StreamScapeTV/", section)


if __name__ == "__main__":
    unittest.main()
