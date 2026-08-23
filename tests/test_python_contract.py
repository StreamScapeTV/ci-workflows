from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from ci_workflows.python_contract import (
    bounded_path,
    load_python_contract,
    request_from_environment,
    resolve_validation_plan,
    safe_relative,
    validate_dependency_lock,
    validate_script_entrypoint,
)
from ci_workflows.python_types import PythonValidationError, PythonValidationRequest

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


class PythonContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_python_contract(ROOT)

    def request(
        self,
        *,
        profile: str = "host",
        python_version: str = "3.12",
        dependency_file: str | None = None,
        source_trust: str = "trusted-exact",
    ) -> PythonValidationRequest:
        return PythonValidationRequest(
            repository="ExampleOrg/example-service",
            admitted_sha=SHA,
            validation_profile=profile,
            python_version=python_version,
            working_directory=".",
            version_file=None,
            dependency_file=dependency_file,
            script_path="ci/validate.sh",
            artifact_exception_id=None,
            source_trust=source_trust,
        )

    def test_contract_contains_no_repository_or_command_registry(self) -> None:
        contract = self.contract
        self.assertEqual("2.0.0", contract["contract_version"])
        self.assertNotIn("consumers", contract)
        self.assertNotIn("command_profiles", contract)
        serialized = (ROOT / "contracts/python-validation.json").read_text(encoding="utf-8")
        for product in ("iptv-backend", "agent-state", "flux"):
            self.assertNotIn(product, serialized.casefold())
        self.assertEqual(
            {
                "path_mode": "repository-relative-executable",
                "arguments": "none",
                "environment": "generic-only",
                "maximum_path_length": 240,
            },
            contract["script_contract"],
        )

    def test_forbidden_escape_hatches_are_explicit(self) -> None:
        forbidden = set(self.contract["forbidden_inputs"])
        self.assertTrue(
            {
                "arbitrary_command",
                "arguments_json",
                "command",
                "command_profile",
                "environment_json",
                "runner",
                "runs_on",
                "container_engine",
                "secret_name",
                "database_url",
                "database_environment_variable",
            }
            <= forbidden
        )

    def test_host_plan_is_repository_agnostic(self) -> None:
        plan = resolve_validation_plan(self.contract, self.request())
        self.assertEqual("ExampleOrg/example-service", plan.repository)
        self.assertEqual("portable", plan.runner_profile)
        self.assertEqual("copied-host-source", plan.isolation)
        self.assertEqual("host-cpython-3.12", plan.runtime_id)
        self.assertIsNone(plan.runtime_reference)
        self.assertEqual("ci/validate.sh", plan.script_path)
        self.assertIsNone(plan.dependency_file)

    def test_postgres_plan_uses_only_generic_connection_handoff(self) -> None:
        plan = resolve_validation_plan(
            self.contract,
            self.request(
                profile="podman-postgres",
                python_version="3.12.13",
                dependency_file="requirements.lock",
            ),
        )
        self.assertEqual("buildah-medium", plan.runner_profile)
        self.assertEqual("podman-vfs-postgres", plan.isolation)
        self.assertIn("python:3.12.13-slim@sha256:", plan.runtime_reference or "")
        self.assertIn("postgres:16.11-alpine@sha256:", plan.postgres_runtime_reference or "")
        self.assertEqual("CIW_POSTGRES_URL", self.contract["postgres"]["connection_environment_variable"])
        self.assertEqual("postgresql", self.contract["postgres"]["connection_url_scheme"])
        self.assertFalse(self.contract["postgres"]["remote_fallback"])

    def test_untrusted_fork_is_limited_to_audit_profile(self) -> None:
        audit = resolve_validation_plan(
            self.contract,
            self.request(profile="audit", source_trust="untrusted-fork"),
        )
        self.assertEqual("portable", audit.runner_profile)
        with self.assertRaises(PythonValidationError) as caught:
            resolve_validation_plan(
                self.contract,
                self.request(profile="host", source_trust="untrusted-fork"),
            )
        self.assertEqual("unsupported_profile", caught.exception.code)

    def test_environment_request_requires_script_and_python_version(self) -> None:
        base = {
            "GITHUB_REPOSITORY": "ExampleOrg/example-service",
            "GITHUB_EVENT_NAME": "push",
            "INPUT_ADMITTED_SHA": SHA,
            "INPUT_VALIDATION_PROFILE": "host",
            "INPUT_PYTHON_VERSION": "3.12",
            "INPUT_SCRIPT_PATH": "ci/validate.sh",
        }
        request = request_from_environment(base)
        self.assertEqual("ci/validate.sh", request.script_path)
        self.assertEqual("3.12", request.python_version)
        for key in ("INPUT_PYTHON_VERSION", "INPUT_SCRIPT_PATH"):
            broken = dict(base)
            broken[key] = ""
            with self.assertRaises(PythonValidationError) as caught:
                request_from_environment(broken)
            self.assertEqual("invalid_input", caught.exception.code)

    def test_paths_reject_absolute_parent_and_symlink_escape(self) -> None:
        for value in ("/tmp/script.sh", "../script.sh", "ci/../script.sh", "ci//script.sh"):
            with self.subTest(value=value):
                with self.assertRaises(PythonValidationError):
                    safe_relative(value)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "outside"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(PythonValidationError):
                bounded_path(root, "link/script.sh")

    def test_script_must_be_checked_in_style_executable_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            script = source / "ci" / "validate.sh"
            script.parent.mkdir()
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            plan = resolve_validation_plan(self.contract, self.request())
            with self.assertRaises(PythonValidationError):
                validate_script_entrypoint(source, plan)
            script.chmod(0o755)
            self.assertEqual(script, validate_script_entrypoint(source, plan))
            replacement = source / "real.sh"
            replacement.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            replacement.chmod(0o755)
            script.unlink()
            script.symlink_to(replacement)
            with self.assertRaises(PythonValidationError):
                validate_script_entrypoint(source, plan)

    def test_dependency_restore_requires_pinned_repository_relative_lock(self) -> None:
        plan = resolve_validation_plan(
            self.contract,
            self.request(profile="host", dependency_file="requirements.lock"),
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            lock = source / "requirements.lock"
            lock.write_text("pytest==8.4.2\n", encoding="utf-8")
            digest = validate_dependency_lock(source, plan)
            self.assertIsNotNone(digest)
            lock.write_text("pytest>=8\n", encoding="utf-8")
            with self.assertRaises(PythonValidationError) as caught:
                validate_dependency_lock(source, plan)
            self.assertEqual("dependency_lock_drift", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
