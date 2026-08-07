from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import python as python_validation
from ci_workflows import python_execution

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def request(
    repository: str,
    profile: str,
    command_profile: str,
    *,
    trust: str = "trusted-exact",
    artifact_exception_id: str | None = None,
) -> python_validation.PythonValidationRequest:
    return python_validation.PythonValidationRequest(
        repository=repository,
        admitted_sha=SHA,
        validation_profile=profile,
        command_profile=command_profile,
        working_directory=".",
        version_file=None,
        script_path=None,
        artifact_exception_id=artifact_exception_id,
        source_trust=trust,
    )


class PythonValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = python_validation.load_python_contract(ROOT)
        cls.cases = json.loads(
            (ROOT / "tests/fixtures/python/cases.json").read_text(encoding="utf-8")
        )
        cls.consumers = json.loads(
            (ROOT / "tests/fixtures/python/consumers.json").read_text(
                encoding="utf-8"
            )
        )

    def test_contract_exposes_exact_profiles_and_immutable_runtimes(self) -> None:
        self.assertEqual(
            set(self.contract["profiles"]),
            {"audit", "host", "podman", "podman-postgres"},
        )
        self.assertEqual(
            {
                name: value["runner_profile"]
                for name, value in self.contract["profiles"].items()
            },
            {
                "audit": "portable",
                "host": "portable",
                "podman": "buildah-high",
                "podman-postgres": "buildah-medium",
            },
        )
        for identifier, runtime in self.contract["runtimes"].items():
            with self.subTest(runtime=identifier):
                if runtime["kind"] == "host":
                    self.assertEqual(runtime["python_version"], "3.12.13")
                else:
                    reference = (
                        f"{runtime['repository']}:{runtime['tag']}"
                        f"@{runtime['digest']}"
                    )
                    self.assertRegex(
                        reference,
                        r"^docker\.io/library/[a-z0-9._-]+:[a-z0-9._-]+"
                        r"@sha256:[0-9a-f]{64}$",
                    )
                    self.assertNotIn("latest", reference)

    def test_positive_fixture_covers_all_current_consumer_shapes(self) -> None:
        positive = {row["id"] for row in self.cases["positive"]}
        self.assertEqual(
            positive,
            {
                "flux-audit",
                "agent-state-host",
                "backend-podman",
                "backend-postgres",
                "agent-state-postgres",
            },
        )
        repositories = {
            row["repository"] for row in self.consumers["repositories"]
        }
        self.assertEqual(
            repositories,
            {
                "StreamScapeTV/iptv-backend",
                "StreamScapeTV/agent-state",
                "StreamScapeTV/flux",
            },
        )

    def test_current_consumer_plans_resolve_only_contract_owned_behavior(self) -> None:
        cases = (
            (
                "StreamScapeTV/flux",
                "audit",
                "source-audit",
                "portable",
                "host-cpython-3.12.13",
                False,
            ),
            (
                "StreamScapeTV/agent-state",
                "host",
                "source-audit",
                "portable",
                "host-cpython-3.12.13",
                False,
            ),
            (
                "StreamScapeTV/iptv-backend",
                "podman",
                "full-test",
                "buildah-high",
                "python-3.12.8-slim-amd64",
                False,
            ),
            (
                "StreamScapeTV/iptv-backend",
                "podman-postgres",
                "postgres-test",
                "buildah-medium",
                "python-3.12.8-slim-amd64",
                True,
            ),
            (
                "StreamScapeTV/agent-state",
                "podman-postgres",
                "postgres-test",
                "buildah-medium",
                "python-3.12.13-slim-amd64",
                True,
            ),
        )
        for repository, profile, command, runner, runtime, postgres in cases:
            with self.subTest(repository=repository, profile=profile):
                plan = python_validation.resolve_validation_plan(
                    self.contract,
                    request(repository, profile, command),
                )
                self.assertEqual(plan.runner_profile, runner)
                self.assertEqual(plan.runtime_id, runtime)
                self.assertEqual(bool(plan.postgres_runtime_reference), postgres)
                self.assertTrue(plan.commands)
                self.assertNotIn("callback", json.dumps(plan.planning_outputs()))

    def test_profile_and_consumer_cross_selection_fail_closed(self) -> None:
        invalid = (
            request(
                "StreamScapeTV/flux",
                "podman",
                "source-audit",
            ),
            request(
                "StreamScapeTV/iptv-backend",
                "audit",
                "full-test",
            ),
            request(
                "StreamScapeTV/agent-state",
                "podman-postgres",
                "full-test",
            ),
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    python_validation.PythonValidationError,
                    "unsupported_profile",
                ):
                    python_validation.resolve_validation_plan(
                        self.contract,
                        value,
                    )

    def test_fork_source_is_audit_only(self) -> None:
        audit = request(
            "StreamScapeTV/flux",
            "audit",
            "source-audit",
            trust="untrusted-fork",
        )
        self.assertEqual(
            python_validation.resolve_validation_plan(
                self.contract,
                audit,
            ).runner_profile,
            "portable",
        )
        with self.assertRaisesRegex(
            python_validation.PythonValidationError,
            "unsupported_profile",
        ):
            python_validation.resolve_validation_plan(
                self.contract,
                request(
                    "StreamScapeTV/iptv-backend",
                    "podman",
                    "full-test",
                    trust="untrusted-fork",
                ),
            )

    def test_event_source_trust_is_derived_not_caller_selected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "event.json"
            for head, expected in (
                ("StreamScapeTV/iptv-backend", "trusted-pr"),
                ("fork-user/iptv-backend", "untrusted-fork"),
            ):
                path.write_text(
                    json.dumps(
                        {
                            "pull_request": {
                                "head": {"repo": {"full_name": head}}
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                environment = {
                    "GITHUB_EVENT_NAME": "pull_request",
                    "GITHUB_EVENT_PATH": str(path),
                    "GITHUB_REPOSITORY": "StreamScapeTV/iptv-backend",
                }
                self.assertEqual(
                    python_validation.source_trust_from_environment(
                        environment
                    ),
                    expected,
                )
            self.assertEqual(
                python_validation.source_trust_from_environment(
                    {"GITHUB_EVENT_NAME": "push"}
                ),
                "trusted-exact",
            )

    def test_request_rejects_artifacts_and_infrastructure_inputs_are_absent(self) -> None:
        environment = {
            "GITHUB_REPOSITORY": "StreamScapeTV/flux",
            "GITHUB_EVENT_NAME": "push",
            "INPUT_ADMITTED_SHA": SHA,
            "INPUT_VALIDATION_PROFILE": "audit",
            "INPUT_COMMAND_PROFILE": "source-audit",
            "INPUT_WORKING_DIRECTORY": ".",
            "INPUT_ARTIFACT_EXCEPTION_ID": "unreviewed",
            "INPUT_RUNNER_LABELS": "self-hosted",
        }
        with self.assertRaisesRegex(
            python_validation.PythonValidationError,
            "artifact_policy_failed",
        ):
            python_validation.request_from_environment(environment)
        forbidden = set(self.contract["forbidden_inputs"])
        self.assertTrue(
            {
                "runner",
                "runner_labels",
                "container_engine",
                "storage_driver",
                "service_image",
                "database_url",
                "database_password",
                "secret_name",
            }
            <= forbidden
        )

    def test_strict_version_file_matches_exact_or_family(self) -> None:
        plan = python_validation.resolve_validation_plan(
            self.contract,
            request(
                "StreamScapeTV/iptv-backend",
                "podman",
                "full-test",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version = root / "python-version"
            version.write_text("3.12\n", encoding="utf-8")
            family = plan.__class__(
                **{
                    **plan.__dict__,
                    "version_file": "python-version",
                }
            )
            self.assertEqual(
                python_validation.resolve_python_version(root, family),
                "3.12.8",
            )
            version.write_text("3.13\n", encoding="utf-8")
            with self.assertRaisesRegex(
                python_validation.PythonValidationError,
                "python_version_drift",
            ):
                python_validation.resolve_python_version(root, family)

    def test_dependency_lock_accepts_bounded_includes_and_rejects_ranges(self) -> None:
        plan = python_validation.resolve_validation_plan(
            self.contract,
            request(
                "StreamScapeTV/agent-state",
                "podman-postgres",
                "postgres-test",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "backend").mkdir()
            (root / "backend/requirements-dev.txt").write_text(
                "-r requirements.txt\npytest==9.0.3\n",
                encoding="utf-8",
            )
            (root / "backend/requirements.txt").write_text(
                "fastapi==0.115.6\n",
                encoding="utf-8",
            )
            first = python_validation.validate_dependency_lock(root, plan)
            second = python_validation.validate_dependency_lock(root, plan)
            self.assertEqual(first, second)
            (root / "backend/requirements.txt").write_text(
                "fastapi>=0.115.6\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                python_validation.PythonValidationError,
                "dependency_lock_drift",
            ):
                python_validation.validate_dependency_lock(root, plan)

    def test_plan_phase_is_side_effect_free_and_deterministic(self) -> None:
        value = request(
            "StreamScapeTV/flux",
            "audit",
            "source-audit",
        )
        first = python_validation.validate(
            contract_root=ROOT,
            source_root=None,
            state_root=None,
            request=value,
            phase="plan",
            environment={},
        )
        second = python_validation.validate(
            contract_root=ROOT,
            source_root=None,
            state_root=None,
            request=value,
            phase="plan",
            environment={},
        )
        self.assertEqual(first, second)
        self.assertEqual(first.planning_outputs()["result"], "planned")

    def test_container_script_uses_only_reviewed_command_argv(self) -> None:
        plan = python_validation.resolve_validation_plan(
            self.contract,
            request(
                "StreamScapeTV/iptv-backend",
                "podman-postgres",
                "postgres-test",
            ),
        )
        script = python_validation._container_script(plan)
        for command in plan.commands:
            for value in command.argv:
                self.assertIn(value, script)
        for token in (
            "${{",
            "INPUT_COMMAND",
            "eval ",
            "docker ",
            "latest",
        ):
            self.assertNotIn(token, script)

    def test_cleanup_verification_rejects_container_residue(self) -> None:
        completed = subprocess.CompletedProcess(["podman"], 0, "", "")
        residue = subprocess.CompletedProcess(["podman"], 0, "", "")
        with mock.patch.object(
            python_execution,
            "run_command",
            side_effect=[
                completed,
                completed,
                completed,
                completed,
                residue,
                subprocess.CompletedProcess(["podman"], 1, "", ""),
                subprocess.CompletedProcess(["podman"], 1, "", ""),
            ],
        ):
            with self.assertRaisesRegex(
                python_validation.PythonValidationError,
                "cleanup_failed",
            ):
                python_validation._cleanup_podman(
                    ["podman"],
                    containers=["validation"],
                    network="network",
                    volume="volume",
                    images=["image"],
                    cwd=ROOT,
                    environment={},
                )

    def test_public_outputs_are_bounded_and_do_not_contain_credentials(self) -> None:
        result = python_validation.PythonValidationResult(
            source_sha=SHA,
            resolved_python_version="3.12.8",
            validation_profile="podman-postgres",
            command_profile="postgres-test",
            stage_count=1,
            cleanup_result="registered",
            evidence_id="python-" + "b" * 28,
        )
        values = result.output_values()
        serialized = json.dumps(values, sort_keys=True)
        self.assertEqual(values["failure_code"], "")
        self.assertNotIn("password", serialized.casefold())
        self.assertNotIn("postgresql://", serialized)
        self.assertNotIn("token", serialized.casefold())


if __name__ == "__main__":
    unittest.main()
