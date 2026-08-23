from __future__ import annotations

import unittest
from pathlib import Path

from ci_workflows import python as python_validation

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


class TrustedPullRequestHostProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = python_validation.load_python_contract(ROOT)

    def request(
        self,
        *,
        profile: str,
        trust: str,
        python_version: str = "3.12",
    ) -> python_validation.PythonValidationRequest:
        return python_validation.PythonValidationRequest(
            repository="ExampleOrg/example-service",
            admitted_sha=SHA,
            validation_profile=profile,
            python_version=python_version,
            working_directory=".",
            version_file=None,
            dependency_file="requirements.lock" if profile != "audit" else None,
            script_path="ci/validate.sh",
            artifact_exception_id=None,
            source_trust=trust,
        )

    def test_same_repository_pr_resolves_to_portable_host_validation(self) -> None:
        plan = python_validation.resolve_validation_plan(
            self.contract,
            self.request(profile="host", trust="trusted-pr"),
        )
        self.assertEqual("portable", plan.runner_profile)
        self.assertEqual("host-cpython-3.12", plan.runtime_id)
        self.assertEqual("3.12", plan.python_version)
        self.assertIsNone(plan.postgres_runtime_reference)
        self.assertEqual("requirements.lock", plan.dependency_file)
        self.assertEqual("ci/validate.sh", plan.script_path)
        self.assertFalse(hasattr(plan, "commands"))

    def test_pr_source_cannot_escalate_to_container_profiles(self) -> None:
        for profile in ("podman", "podman-postgres"):
            with self.subTest(profile=profile):
                with self.assertRaises(PythonValidationError) as caught:
                    python_validation.resolve_validation_plan(
                        self.contract,
                        self.request(
                            profile=profile,
                            trust="trusted-pr",
                            python_version="3.12.13",
                        ),
                    )
                self.assertEqual("unsupported_profile", caught.exception.code)

    def test_fork_source_cannot_use_host_validation(self) -> None:
        with self.assertRaises(PythonValidationError) as caught:
            python_validation.resolve_validation_plan(
                self.contract,
                self.request(profile="host", trust="untrusted-fork"),
            )
        self.assertEqual("unsupported_profile", caught.exception.code)

    def test_fork_source_can_use_bounded_audit_script(self) -> None:
        plan = python_validation.resolve_validation_plan(
            self.contract,
            self.request(profile="audit", trust="untrusted-fork"),
        )
        self.assertEqual("portable", plan.runner_profile)
        self.assertEqual("copied-host-source", plan.isolation)
        self.assertIsNone(plan.dependency_file)


PythonValidationError = python_validation.PythonValidationError


if __name__ == "__main__":
    unittest.main()
