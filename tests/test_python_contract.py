from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Any, Mapping

from ci_workflows.python import load_python_contract

ROOT = Path(__file__).resolve().parents[1]
IMAGE = re.compile(
    r"^[a-z0-9.-]+(?:/[a-z0-9._-]+)+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$"
)


def immutable_image(runtime: Mapping[str, Any]) -> str:
    return f"{runtime['repository']}:{runtime['tag']}@{runtime['digest']}"


class PythonValidationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_python_contract(ROOT)
        cls.fixtures = json.loads(
            (ROOT / "tests/fixtures/python-validation/cases.json").read_text(
                encoding="utf-8"
            )
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

    def test_profiles_are_exactly_the_reviewed_bounded_set(self) -> None:
        profiles = self.contract["profiles"]
        self.assertEqual(
            set(profiles),
            {"audit", "host", "podman", "podman-postgres"},
        )
        expected = {
            "audit": ("portable", "minimal"),
            "host": ("portable", "minimal"),
            "podman": ("buildah-high", "container"),
            "podman-postgres": ("buildah-medium", "container"),
        }
        for identifier, (runner, workspace) in expected.items():
            with self.subTest(profile=identifier):
                profile = profiles[identifier]
                self.assertEqual(runner, profile["runner_profile"])
                self.assertEqual(workspace, profile["workspace_profile"])
                self.assertLess(profile["timeout_minutes"], 120)
        self.assertFalse(profiles["audit"]["postgres"])
        self.assertFalse(profiles["host"]["postgres"])
        self.assertFalse(profiles["podman"]["postgres"])
        self.assertTrue(profiles["podman-postgres"]["postgres"])

    def test_python_and_postgres_images_are_exact_immutable_identities(self) -> None:
        runtimes = self.contract["runtimes"]
        self.assertEqual(
            set(runtimes),
            {
                "host-cpython-3.12.3",
                "python-3.12.8-slim-amd64",
                "python-3.12.13-slim-amd64",
                "postgres-16.11-alpine-amd64",
            },
        )
        host = runtimes["host-cpython-3.12.3"]
        self.assertEqual("host", host["kind"])
        self.assertEqual("cpython", host["implementation"])
        self.assertEqual("3.12.3", host["python_version"])
        self.assertEqual(["linux/x64"], host["platforms"])
        container_runtimes = {
            identifier: runtime
            for identifier, runtime in runtimes.items()
            if runtime["kind"] != "host"
        }
        self.assertEqual(3, len(container_runtimes))
        for identifier, runtime in container_runtimes.items():
            with self.subTest(runtime=identifier):
                image = immutable_image(runtime)
                self.assertRegex(image, IMAGE)
                self.assertNotEqual("latest", runtime["tag"])
                self.assertRegex(runtime["digest"], r"^sha256:[0-9a-f]{64}$")
                self.assertEqual("linux/amd64", runtime["platform"])
        self.assertEqual(
            "3.12.8",
            runtimes["python-3.12.8-slim-amd64"]["python_version"],
        )
        self.assertEqual(
            "3.12.13",
            runtimes["python-3.12.13-slim-amd64"]["python_version"],
        )
        self.assertEqual(
            "16.11",
            runtimes["postgres-16.11-alpine-amd64"]["postgres_version"],
        )

    def test_postgres_service_is_ephemeral_bounded_and_has_no_remote_fallback(self) -> None:
        postgres = self.contract["postgres"]
        self.assertFalse(postgres["remote_fallback"])
        self.assertEqual("ephemeral-per-execution", postgres["credentials"])
        self.assertEqual(30, postgres["readiness_attempts"])
        self.assertEqual(2, postgres["readiness_interval_seconds"])
        self.assertEqual(60, postgres["readiness_timeout_seconds"])
        self.assertEqual("ci_python", postgres["username"])
        self.assertEqual("ci_python", postgres["database"])
        self.assertNotIn("password", postgres)
        serialized = json.dumps(postgres, sort_keys=True)
        self.assertNotRegex(serialized, r"https?://")
        self.assertNotIn("production", serialized.casefold())

    def test_public_surface_has_no_command_runner_engine_service_or_deployment_input(self) -> None:
        public_inputs = {
            item["name"] for item in self.public_record["inputs"]
        }
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
        self.assertEqual(
            set(self.public_record["outputs"]),
            {"result", "test_summary", "artifact_exception_used"},
        )
        self.assertEqual([], self.public_record["secrets"])
        for name in (
            "arbitrary_command",
            "shell",
            "callback",
            "runner",
            "runs_on",
            "runner_labels",
            "container_engine",
            "storage_driver",
            "service_image",
            "database_url",
            "database_password",
            "registry_host",
            "secret_name",
            "release_version",
            "helm_chart",
            "flux_target",
            "cluster",
            "namespace",
            "deployment_operation",
        ):
            self.assertIn(name, forbidden)

    def test_command_profiles_and_consumer_stages_are_data_bounded(self) -> None:
        command_profiles = self.contract["command_profiles"]
        self.assertEqual(
            set(command_profiles),
            {
                "source-audit",
                "locked-test",
                "full-test",
                "postgres-test",
                "release-contract",
            },
        )
        profiles = set(self.contract["profiles"])
        for identifier, command in command_profiles.items():
            with self.subTest(command_profile=identifier):
                self.assertTrue(set(command["allowed_profiles"]) <= profiles)
                self.assertIsInstance(command["dependency_required"], bool)
                self.assertIsInstance(command["postgres_required"], bool)
                self.assertEqual(
                    "contract-fixed-only",
                    command["script_path_mode"],
                )
                self.assertTrue(command["stages"])
                self.assertLessEqual(len(command["stages"]), 16)

        for repository, consumer in self.contract["consumers"].items():
            self.assertRegex(repository, r"^StreamScapeTV/[A-Za-z0-9_.-]+$")
            for command_profile, shape in consumer["profiles"].items():
                with self.subTest(
                    repository=repository,
                    command_profile=command_profile,
                ):
                    self.assertIn(command_profile, command_profiles)
                    self.assertIn(
                        shape["validation_profile"],
                        command_profiles[command_profile]["allowed_profiles"],
                    )
                    commands = shape["commands"]
                    self.assertTrue(commands)
                    self.assertLessEqual(len(commands), 16)
                    for command in commands:
                        self.assertIn(
                            command["stage"],
                            command_profiles[command_profile]["stages"],
                        )
                        argv = command["argv"]
                        self.assertIsInstance(argv, list)
                        self.assertTrue(argv)
                        joined = " ".join(argv).casefold()
                        self.assertNotIn("${{", joined)
                        self.assertNotIn("secrets.", joined)
                        self.assertNotIn("--privileged", joined)
                        self.assertNotIn("docker.sock", joined)
                        self.assertNotIn("kubeconfig", joined)

    def test_current_consumer_shapes_are_recorded_without_repository_mutation(self) -> None:
        actual = {
            (repository, shape["validation_profile"], command_profile)
            for repository, consumer in self.contract["consumers"].items()
            for command_profile, shape in consumer["profiles"].items()
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
            set(self.contract["consumers"]),
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
