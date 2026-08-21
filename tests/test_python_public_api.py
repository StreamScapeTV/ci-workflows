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

    def test_python_api_is_implemented_versioned_and_zero_secret(self) -> None:
        self.assertEqual(self.python["api_version"], "1.0.0")
        self.assertEqual(self.python["status"], "implemented")
        self.assertEqual(self.python["trust_class"], "read-only-validation")
        self.assertEqual(self.python["permission_profile"], "validation-read")
        self.assertEqual(self.python["semantic_runner_profile"], "contract:python")
        self.assertEqual(self.python["secrets"], [])
        self.assertEqual(self.python["stable_check_name"], "CI / Python validation")
        self.assertEqual(self.python["matrix_max_jobs"], 1)
        self.assertEqual(self.python["timeout_minutes"], 120)

    def test_python_inputs_outputs_and_identity_boundary_are_exact(self) -> None:
        self.assertEqual(
            {item["name"] for item in self.python["inputs"]},
            {
                "execution_backend",
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
            set(self.python["outputs"]),
            {"result", "test_summary", "artifact_exception_used"},
        )
        self.assertNotIn("supported_consumers", self.python)
        self.assertNotIn("supported_products", self.python)
        self.assertEqual(
            set(self.python["implementation_components"]),
            {
                "ci_workflows.python.validate",
                "ci_workflows.execution_backends.resolve_execution_backend",
                "actions/validate-python",
            },
        )

    def test_python_caller_fixture_accepts_bounded_intent_and_rejects_runner(self) -> None:
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
                "command_profile": "source-audit",
            },
        }
        self.assertIsNone(
            public_api.validate_caller(base, self.data, self.workflows, self.profiles)
        )
        invalid = json.loads(json.dumps(base))
        invalid["inputs"]["runner"] = "buildah-high"
        self.assertEqual(
            public_api.validate_caller(invalid, self.data, self.workflows, self.profiles),
            "forbidden-caller-field",
        )

    def test_generated_public_reference_contains_the_implemented_python_api(self) -> None:
        rendered = public_api.render(self.data)
        self.assertIn("`validation.python` `1.0.0`", rendered)
        self.assertIn("`.github/workflows/reusable-python.yml`", rendered)
        self.assertIn("`implemented`", rendered)
        self.assertIn("CI / Python validation", rendered)
        self.assertIn("`artifact_exception_id`", rendered)
        section = rendered.split("### `validation.python`", 1)[1].split("### `", 1)[0]
        self.assertNotIn("postgres_enabled", section)
        self.assertNotIn("StreamScapeTV/", section)


if __name__ == "__main__":
    unittest.main()
