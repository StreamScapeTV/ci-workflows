from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from ci_workflows.python import load_python_contract

ROOT = Path(__file__).resolve().parents[1]
IMAGE = re.compile(
    r"^[a-z0-9.-]+(?:/[a-z0-9._-]+)+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$"
)


class PythonValidationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_python_contract(ROOT)
        cls.fixtures = json.loads(
            (ROOT / "tests/fixtures/python-validation/cases.json").read_text(
                encoding="utf-8"
            )
        )

    def test_profiles_are_exactly_the_reviewed_bounded_set(self) -> None:
        self.assertEqual(
            set(self.contract["profiles"]),
            {"audit", "host", "podman", "podman-postgres"},
        )
        self.assertEqual(
            self.contract["profiles"]["audit"]["runner_profile"],
            "portable",
        )
        self.assertEqual(
            self.contract["profiles"]["host"]["runner_profile"],
            "portable",
        )
        self.assertEqual(
            self.contract["profiles"]["podman"]["runner_profile"],
            "buildah-high",
        )
        self.assertEqual(
            self.contract["profiles"]["podman-postgres"]["runner_profile"],
            "buildah-medium",
        )
        for profile in self.contract["profiles"].values():
            self.assertLess(profile["timeout_minutes"], 120)
            self.assertEqual("minimal", profile["workspace_profile"])

    def test_python_and_postgres_images_are_exact_immutable_identities(self) -> None:
        images = [
            runtime["image"]
            for runtime in self.contract["runtimes"].values()
            if runtime["kind"] != "host"
        ]
        self.assertEqual(2, len(images))
        for image in images:
            self.assertRegex(image, IMAGE)
            self.assertNotRegex(image, r":(?:latest|3\.12|16)(?:@|$)")
        self.assertEqual(
            "3.12.13",
            self.contract["runtimes"]["python-3.12.13-slim"]["version"],
        )
        self.assertEqual(
            "16.11",
            self.contract["runtimes"]["postgres-16.11-alpine"]["version"],
        )

    def test_postgres_service_is_ephemeral_bounded_and_has_no_remote_fallback(self) -> None:
        postgres = self.contract["postgres"]
        self.assertFalse(postgres["remote_fallback"])
        self.assertEqual(30, postgres["readiness"]["attempts"])
        self.assertEqual(2, postgres["readiness"]["interval_seconds"])
        self.assertGreaterEqual(postgres["ephemeral_password_bytes"], 24)
        self.assertNotIn("password", postgres)
        serialized = json.dumps(postgres, sort_keys=True)
        self.assertNotRegex(serialized, r"https?://")
        self.assertNotIn("production", serialized.casefold())

    def test_public_surface_has_no_command_runner_engine_service_or_deployment_input(self) -> None:
        public_inputs = set(self.contract["public_inputs"])
        forbidden = set(self.contract["forbidden_inputs"])
        self.assertTrue(public_inputs.isdisjoint(forbidden))
        self.assertEqual(
            public_inputs,
            {
                "admitted_sha",
                "validation_profile",
                "command_profile",
                "working_directory",
                "version_file",
                "script_path",
                "artifact_exception_id",
            },
        )
        self.assertEqual(len(self.contract["public_outputs"]), 9)
        for name in (
            "command",
            "shell",
            "runner",
            "container_engine",
            "python_image",
            "postgres_image",
            "database_url",
            "secret_name",
            "release_version",
            "helm_chart",
            "flux_target",
            "cluster",
            "namespace",
        ):
            self.assertIn(name, forbidden)

    def test_command_profiles_and_consumer_stages_are_data_bounded(self) -> None:
        self.assertEqual(
            set(self.contract["command_profiles"]),
            {
                "source-audit",
                "locked-test",
                "full-test",
                "postgres-test",
                "release-contract",
            },
        )
        for command in self.contract["command_profiles"].values():
            self.assertFalse(command["source_mutation"])
        for consumer in self.contract["consumers"]:
            self.assertLessEqual(len(consumer["stages"]), 16)
            for stage in consumer["stages"]:
                self.assertIsInstance(stage, list)
                self.assertTrue(stage)
                joined = " ".join(stage).casefold()
                self.assertNotIn("${{", joined)
                self.assertNotIn("secrets.", joined)
                self.assertNotIn("--privileged", joined)
                self.assertNotIn("docker.sock", joined)
                self.assertNotIn("kubeconfig", joined)

    def test_current_consumer_shapes_are_recorded_without_repository_mutation(self) -> None:
        actual = {
            (
                item["repository"],
                item["validation_profile"],
                item["command_profile"],
            )
            for item in self.contract["consumers"]
        }
        expected = {
            (
                item["repository"],
                item["validation_profile"],
                item["command_profile"],
            )
            for item in self.fixtures["positive"]
        }
        self.assertEqual(expected, actual)
        self.assertEqual(
            {item["repository"] for item in self.contract["consumers"]},
            {
                "StreamScapeTV/iptv-backend",
                "StreamScapeTV/agent-state",
                "StreamScapeTV/flux",
            },
        )

    def test_fixture_manifest_covers_required_fail_closed_cases(self) -> None:
        self.assertEqual(1, self.fixtures["schema_version"])
        positive = {item["id"] for item in self.fixtures["positive"]}
        negative = {item["id"] for item in self.fixtures["negative"]}
        self.assertEqual(
            positive,
            {
                "flux-source-audit",
                "agent-state-host-automation",
                "agent-state-postgres",
                "backend-full-isolation",
                "backend-postgres-focus",
            },
        )
        self.assertEqual(
            negative,
            {
                "unknown-profile",
                "unmapped-consumer-profile",
                "fork-podman-escalation",
                "mutable-python-image",
                "mutable-postgres-image",
                "caller-selected-command",
                "caller-selected-runner",
                "caller-selected-database-url",
                "unpinned-dependency",
                "postgres-readiness-timeout",
                "cleanup-residue",
                "source-dirty-after-validation",
            },
        )


if __name__ == "__main__":
    unittest.main()
