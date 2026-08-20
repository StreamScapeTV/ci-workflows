from __future__ import annotations

import json
import unittest
from pathlib import Path

from ci_workflows import public_api

ROOT = Path(__file__).resolve().parents[1]


class NodePublicApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = public_api.validate(ROOT)
        cls.profiles = public_api.permission_profiles(cls.data)
        cls.workflows = public_api.validate_workflows(cls.data, cls.profiles)
        cls.node = cls.workflows["validation.node"]

    def test_node_api_is_implemented_versioned_read_only_and_zero_secret(self) -> None:
        self.assertEqual(self.node["api_version"], "1.0.0")
        self.assertEqual(self.node["status"], "implemented")
        self.assertEqual(self.node["trust_class"], "read-only-validation")
        self.assertEqual(self.node["permission_profile"], "validation-read")
        self.assertEqual(self.node["semantic_runner_profile"], "contract:node")
        self.assertEqual(self.node["secrets"], [])
        self.assertEqual(self.node["stable_check_name"], "CI / Node validation")
        self.assertEqual(self.node["matrix_max_jobs"], 1)
        self.assertEqual(self.node["timeout_minutes"], 90)

    def test_node_inputs_outputs_identity_boundary_and_components_are_exact(self) -> None:
        self.assertEqual(
            {item["name"] for item in self.node["inputs"]},
            {
                "execution_backend",
                "admitted_sha",
                "validation_profile",
                "version_file",
                "node_version",
                "working_directory",
                "install_profile",
                "command_profile",
                "script_path",
                "static_output_directory",
                "output_verifier_path",
                "public_environment",
                "artifact_exception_id",
            },
        )
        self.assertEqual(
            set(self.node["outputs"]),
            {
                "result",
                "node_version",
                "npm_version",
                "install_result",
                "test_summary",
                "build_result",
                "output_verified",
                "output_digest",
                "clean_tree",
                "cleanup_result",
                "artifact_exception_used",
                "evidence_id",
            },
        )
        self.assertNotIn("supported_consumers", self.node)
        self.assertNotIn("supported_products", self.node)
        self.assertEqual(
            set(self.node["implementation_components"]),
            {"ci_workflows.node.validate", "actions/validate-node"},
        )

    def test_node_caller_accepts_bounded_intent_and_rejects_infrastructure(self) -> None:
        base = {
            "api_name": "validation.node",
            "trust_class": "read-only-validation",
            "reference": "main",
            "event": "pull_request",
            "permissions": {"contents": "read"},
            "secrets": [],
            "inputs": {
                "admitted_sha": "0" * 40,
                "validation_profile": "locked-node",
                "version_file": ".nvmrc",
                "install_profile": "npm-ci",
                "command_profile": "quality-test",
            },
        }
        self.assertIsNone(
            public_api.validate_caller(base, self.data, self.workflows, self.profiles)
        )
        for forbidden in (
            "runner",
            "container_engine",
            "registry_host",
            "secret_name",
            "cluster",
        ):
            invalid = json.loads(json.dumps(base))
            invalid["inputs"][forbidden] = "caller-selected"
            self.assertEqual(
                public_api.validate_caller(
                    invalid, self.data, self.workflows, self.profiles
                ),
                "forbidden-caller-field",
            )

    def test_generated_reference_contains_the_complete_node_api(self) -> None:
        rendered = public_api.render(self.data)
        section = rendered.split("### `validation.node`", 1)[1].split("### `", 1)[0]
        self.assertIn("`validation.node` `1.0.0`", rendered)
        self.assertIn("`.github/workflows/reusable-node.yml`", rendered)
        self.assertIn("`implemented`", rendered)
        self.assertIn("CI / Node validation", rendered)
        self.assertIn("`node_version`", section)
        self.assertIn("`public_environment`", section)
        self.assertIn("`output_digest`", section)
        self.assertNotIn("runner_labels", section)
        self.assertNotIn("registry_host", section)
        self.assertNotIn("StreamScapeTV/", section)


if __name__ == "__main__":
    unittest.main()
