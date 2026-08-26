from __future__ import annotations

import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import python as python_validation
from ci_workflows import python_docker_execution as docker_execution
from ci_workflows.python_types import PythonValidationError, PythonValidationPlan, PythonValidationRequest, PythonValidationResult

ROOT = Path(__file__).resolve().parents[1]


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def committed_source(root: Path, script_body: str) -> tuple[Path, str]:
    source = root / "source"
    source.mkdir()
    git(source, "init", "-q")
    git(source, "config", "user.email", "ci@example.invalid")
    git(source, "config", "user.name", "CI Test")
    script = source / "ci" / "validate.py"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/env python3\n" + script_body, encoding="utf-8")
    script.chmod(0o755)
    git(source, "add", ".")
    git(source, "commit", "-qm", "fixture")
    return source, git(source, "rev-parse", "HEAD")


def request(sha: str, *, version: str = "3.12") -> PythonValidationRequest:
    return PythonValidationRequest(
        repository="ExampleOrg/example-service",
        admitted_sha=sha,
        validation_profile="host",
        python_version=version,
        working_directory=".",
        version_file=None,
        dependency_file=None,
        script_path="ci/validate.py",
        artifact_exception_id=None,
        source_trust="trusted-exact",
    )


def container_request(sha: str, *, profile: str = "podman") -> PythonValidationRequest:
    return PythonValidationRequest(
        repository="ExampleOrg/example-service",
        admitted_sha=sha,
        validation_profile=profile,
        python_version="3.12.8",
        working_directory=".",
        version_file=None,
        dependency_file=None,
        script_path="ci/validate.py",
        artifact_exception_id=None,
        source_trust="trusted-exact",
    )


class PythonValidationTests(unittest.TestCase):
    def test_plan_phase_is_product_neutral_and_does_not_require_source_checkout(self) -> None:
        plan = python_validation.validate(
            contract_root=ROOT,
            source_root=None,
            state_root=None,
            request=request("a" * 40),
            phase="plan",
            environment={},
        )
        self.assertIsInstance(plan, PythonValidationPlan)
        self.assertEqual("ExampleOrg/example-service", plan.repository)
        self.assertEqual("ci/validate.py", plan.script_path)
        self.assertEqual("3.12", plan.python_version)
        self.assertFalse(hasattr(plan, "command_profile"))
        self.assertFalse(hasattr(plan, "commands"))
        self.assertFalse(hasattr(plan, "environment"))

    def test_host_execution_runs_only_checked_in_script_in_isolated_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, sha = committed_source(
                root,
                "import os, pathlib\n"
                "assert os.environ['CI'] == 'true'\n"
                "assert 'PRODUCT_SECRET' not in os.environ\n"
                "pathlib.Path(os.environ['HOME']).joinpath('proof').write_text('ok')\n",
            )
            state = root / "state"
            state.mkdir()
            with mock.patch.object(python_validation, "_verify_policy"):
                result = python_validation.validate(
                    contract_root=ROOT,
                    source_root=source,
                    state_root=state,
                    request=request(sha),
                    phase="execute",
                    environment={**os.environ, "PRODUCT_SECRET": "must-not-cross-boundary"},
                )
            self.assertIsInstance(result, PythonValidationResult)
            self.assertEqual("host", result.validation_profile)
            self.assertTrue(result.resolved_python_version.startswith("3.12."))
            self.assertEqual("registered", result.cleanup_result)
            self.assertEqual("", git(source, "status", "--porcelain=v1", "--untracked-files=all"))
            self.assertFalse((source / "proof").exists())
            isolated_home = state / "python-validation" / "host" / "home"
            self.assertEqual("ok", (isolated_home / "proof").read_text(encoding="utf-8"))

    def test_github_hosted_container_profile_uses_docker_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, sha = committed_source(root, "pass\n")
            state = root / "state"
            state.mkdir()
            environment = {**os.environ, "INPUT_EXECUTION_BACKEND": "github-hosted"}
            with (
                mock.patch.object(python_validation, "_verify_policy"),
                mock.patch.object(python_validation, "execute_docker_plan", return_value=1) as docker,
                mock.patch.object(python_validation, "execute_podman_plan", return_value=1) as podman,
            ):
                result = python_validation.validate(
                    contract_root=ROOT,
                    source_root=source,
                    state_root=state,
                    request=container_request(sha),
                    phase="execute",
                    environment=environment,
                )
            self.assertIsInstance(result, PythonValidationResult)
            self.assertEqual("podman", result.validation_profile)
            docker.assert_called_once()
            podman.assert_not_called()

    def test_organization_container_profile_preserves_podman_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, sha = committed_source(root, "pass\n")
            state = root / "state"
            state.mkdir()
            environment = {**os.environ, "INPUT_EXECUTION_BACKEND": "organization"}
            with (
                mock.patch.object(python_validation, "_verify_policy"),
                mock.patch.object(python_validation, "execute_docker_plan", return_value=1) as docker,
                mock.patch.object(python_validation, "execute_podman_plan", return_value=1) as podman,
            ):
                result = python_validation.validate(
                    contract_root=ROOT,
                    source_root=source,
                    state_root=state,
                    request=container_request(sha, profile="podman-postgres"),
                    phase="execute",
                    environment=environment,
                )
            self.assertIsInstance(result, PythonValidationResult)
            self.assertEqual("podman-postgres", result.validation_profile)
            podman.assert_called_once()
            docker.assert_not_called()

    def test_hosted_docker_partial_pull_failure_cleans_already_pulled_image(self) -> None:
        plan = python_validation.validate(
            contract_root=ROOT,
            source_root=None,
            state_root=None,
            request=container_request("a" * 40, profile="podman-postgres"),
            phase="plan",
            environment={},
        )
        self.assertIsInstance(plan, PythonValidationPlan)
        contract = python_validation.load_python_contract(ROOT)
        calls: list[list[str]] = []
        pull_count = 0

        def run(
            argv: list[str],
            *,
            cwd: Path,
            environment: dict[str, str],
            timeout_seconds: int,
            code: str = "command_failed",
            allow_failure: bool = False,
            failure_stage: str | None = None,
        ) -> subprocess.CompletedProcess[str]:
            del cwd, environment, timeout_seconds, code, allow_failure, failure_stage
            nonlocal pull_count
            calls.append(list(argv))
            if argv[:2] == ["docker", "pull"]:
                pull_count += 1
                if pull_count == 2:
                    raise PythonValidationError("toolchain_mismatch")
                return subprocess.CompletedProcess(argv, 0, "", "")
            if "inspect" in argv:
                return subprocess.CompletedProcess(argv, 1, "", "Error: No such object: absent")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            state = root / "state"
            state.mkdir()
            with (
                mock.patch.object(docker_execution.shutil, "which", return_value="/usr/bin/docker"),
                mock.patch.object(docker_execution, "run_command", side_effect=run),
                self.assertRaises(PythonValidationError) as caught,
            ):
                docker_execution.execute_docker_plan(
                    source,
                    state,
                    plan,
                    contract,
                    {
                        "INPUT_EXECUTION_BACKEND": "github-hosted",
                        "GITHUB_RUN_ID": "42",
                        "GITHUB_RUN_ATTEMPT": "1",
                    },
                )
        self.assertEqual("toolchain_mismatch", caught.exception.code)
        self.assertIn(
            ["docker", "image", "rm", "-f", str(plan.runtime_reference)],
            calls,
        )

    def test_hosted_docker_cleanup_fails_when_absence_cannot_be_proven(self) -> None:
        def run(
            argv: list[str],
            *,
            cwd: Path,
            environment: dict[str, str],
            timeout_seconds: int,
            code: str = "command_failed",
            allow_failure: bool = False,
            failure_stage: str | None = None,
        ) -> subprocess.CompletedProcess[str]:
            del cwd, environment, timeout_seconds, code, allow_failure, failure_stage
            if "inspect" in argv:
                return subprocess.CompletedProcess(
                    argv,
                    1,
                    "",
                    "Cannot connect to the Docker daemon",
                )
            return subprocess.CompletedProcess(argv, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                mock.patch.object(docker_execution, "run_command", side_effect=run),
                self.assertRaises(PythonValidationError) as caught,
            ):
                docker_execution._cleanup_docker(
                    ["docker"],
                    containers=["validation"],
                    network=None,
                    volume=None,
                    images=[],
                    cwd=root,
                    environment={},
                )
        self.assertEqual("cleanup_failed", caught.exception.code)

    def test_hosted_docker_child_output_stays_on_current_private_streams(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        completed = subprocess.CompletedProcess(
            ["docker", "run"],
            0,
            "consumer stdout",
            "consumer stderr",
        )
        with (
            mock.patch.object(docker_execution.sys, "stdout", stdout),
            mock.patch.object(docker_execution.sys, "stderr", stderr),
        ):
            docker_execution._emit_validation_output(completed)
        self.assertEqual("consumer stdout\n", stdout.getvalue())
        self.assertEqual("consumer stderr\n", stderr.getvalue())

    def test_nonzero_consumer_script_is_stable_command_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, sha = committed_source(root, "raise SystemExit(7)\n")
            state = root / "state"
            state.mkdir()
            with (
                mock.patch.object(python_validation, "_verify_policy"),
                self.assertRaises(PythonValidationError) as caught,
            ):
                python_validation.validate(
                    contract_root=ROOT,
                    source_root=source,
                    state_root=state,
                    request=request(sha),
                    phase="execute",
                    environment=os.environ,
                )
            self.assertEqual("command_failed", caught.exception.code)
            self.assertEqual("", git(source, "status", "--porcelain=v1", "--untracked-files=all"))

    def test_dirty_or_wrong_source_is_rejected_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, sha = committed_source(root, "pass\n")
            state = root / "state"
            state.mkdir()
            (source / "dirty.txt").write_text("dirty", encoding="utf-8")
            with self.assertRaises(PythonValidationError) as caught:
                python_validation.validate(
                    contract_root=ROOT,
                    source_root=source,
                    state_root=state,
                    request=request(sha),
                    phase="execute",
                    environment=os.environ,
                )
            self.assertEqual("dirty_tree", caught.exception.code)

    def test_version_file_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, sha = committed_source(root, "pass\n")
            (source / ".python-version").write_text("3.13\n", encoding="utf-8")
            git(source, "add", ".python-version")
            git(source, "commit", "-qm", "version")
            sha = git(source, "rev-parse", "HEAD")
            state = root / "state"
            state.mkdir()
            request_with_file = PythonValidationRequest(
                **{
                    **request(sha).__dict__,
                    "version_file": ".python-version",
                }
            )
            with self.assertRaises(PythonValidationError) as caught:
                python_validation.validate(
                    contract_root=ROOT,
                    source_root=source,
                    state_root=state,
                    request=request_with_file,
                    phase="execute",
                    environment=os.environ,
                )
            self.assertEqual("python_version_drift", caught.exception.code)

    def test_invalid_phase_is_rejected(self) -> None:
        with self.assertRaises(PythonValidationError) as caught:
            python_validation.validate(
                contract_root=ROOT,
                source_root=None,
                state_root=None,
                request=request("a" * 40),
                phase="caller-selected-handler",
                environment={},
            )
        self.assertEqual("invalid_input", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
