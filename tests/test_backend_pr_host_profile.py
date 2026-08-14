from __future__ import annotations

import unittest
from pathlib import Path

from ci_workflows import python as python_validation

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


class BackendPullRequestHostProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = python_validation.load_python_contract(ROOT)

    def request(self, *, profile: str, command: str, trust: str) -> python_validation.PythonValidationRequest:
        return python_validation.PythonValidationRequest(
            repository="StreamScapeTV/iptv-backend",
            admitted_sha=SHA,
            validation_profile=profile,
            command_profile=command,
            working_directory=".",
            version_file=None,
            script_path="scripts/run_release_gates.sh" if command in {"locked-test", "full-test"} else None,
            artifact_exception_id=None,
            source_trust=trust,
        )

    def test_same_repository_pr_resolves_only_to_portable_host_validation(self) -> None:
        plan = python_validation.resolve_validation_plan(
            self.contract,
            self.request(profile="host", command="locked-test", trust="trusted-pr"),
        )
        self.assertEqual("portable", plan.runner_profile)
        self.assertEqual("host-cpython-3.12.13", plan.runtime_id)
        self.assertIsNone(plan.postgres_runtime_reference)
        self.assertEqual(
            [command.argv for command in plan.commands],
            [
                ("./scripts/run_release_gates.sh",),
                ("python3", "-m", "pytest", "-q"),
            ],
        )

    def test_pr_source_cannot_escalate_to_privileged_backend_profiles(self) -> None:
        for profile, command in (
            ("podman", "full-test"),
            ("podman-postgres", "postgres-test"),
        ):
            with self.subTest(profile=profile):
                with self.assertRaisesRegex(
                    python_validation.PythonValidationError,
                    "unsupported_profile",
                ):
                    python_validation.resolve_validation_plan(
                        self.contract,
                        self.request(profile=profile, command=command, trust="trusted-pr"),
                    )

    def test_fork_source_cannot_use_backend_host_validation(self) -> None:
        with self.assertRaisesRegex(
            python_validation.PythonValidationError,
            "unsupported_profile",
        ):
            python_validation.resolve_validation_plan(
                self.contract,
                self.request(profile="host", command="locked-test", trust="untrusted-fork"),
            )


if __name__ == "__main__":
    unittest.main()
