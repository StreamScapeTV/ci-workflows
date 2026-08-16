from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows.runtime_primitives import ProcessResult, RuntimePrimitiveError
from ci_workflows.service_compose_primitives import (
    ComposeProject,
    ComposeReadinessCheck,
    ServiceComposeError,
    capture_compose_logs,
    cleanup_compose_stack,
    cleanup_compose_temporary_state,
    compose_exec,
    compose_ps,
    compose_run,
    compose_up,
    start_compose_stack,
    validate_compose_project,
    wait_for_compose_services,
)
from ci_workflows.service_primitives import ServiceReadinessResult

SUCCESS = ProcessResult(0, "", "", False)
FAILURE = ProcessResult(2, "", "failed", False)


class RecordingRunner:
    def __init__(self, results: list[ProcessResult] | None = None) -> None:
        self.results = list(results or [SUCCESS])
        self.calls: list[dict[str, object]] = []

    def __call__(self, argv, *, cwd, environment, timeout_seconds):
        self.calls.append(
            {
                "argv": tuple(argv),
                "cwd": Path(cwd),
                "environment": dict(environment),
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.results.pop(0) if self.results else SUCCESS


class ComposeFixture(unittest.TestCase):
    def make_project(self, root: Path, *, tool: str = "docker", env_file: bool = False):
        compose = root / "compose.yml"
        compose.write_text("services: {}\n", encoding="utf-8")
        env_files = ()
        if env_file:
            env = root / "ci.env"
            env.write_text("SECRET=value\n", encoding="utf-8")
            env_files = (env,)
        return validate_compose_project(
            project_root=root,
            compose_file=compose,
            project_name="ci-stack-42",
            tool=tool,
            env_files=env_files,
        )


class ValidationTests(ComposeFixture):
    def test_project_validation_is_bounded_and_hides_runner_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = self.make_project(root, env_file=True)
            projection = project.public_projection()
        self.assertEqual(projection["project_name"], "ci-stack-42")
        self.assertEqual(projection["tool"], "docker")
        self.assertEqual(projection["compose_file"], "compose.yml")
        self.assertEqual(projection["env_file_count"], 1)
        self.assertNotIn(str(root), repr(project))
        self.assertNotIn("ci.env", repr(project))

    def test_validation_rejects_tool_name_escape_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            compose = root / "compose.yml"
            compose.write_text("services: {}\n", encoding="utf-8")
            with self.assertRaisesRegex(ServiceComposeError, "compose_tool_invalid"):
                validate_compose_project(
                    project_root=root,
                    compose_file=compose,
                    project_name="ci-stack",
                    tool="docker --host remote",
                )
            with self.assertRaisesRegex(ServiceComposeError, "compose_project_name_invalid"):
                validate_compose_project(
                    project_root=root,
                    compose_file=compose,
                    project_name="../other",
                    tool="docker",
                )
            outside = root.parent / "outside-compose.yml"
            outside.write_text("services: {}\n", encoding="utf-8")
            link = root / "link.yml"
            try:
                link.symlink_to(outside)
                with self.assertRaisesRegex(ServiceComposeError, "compose_file_invalid"):
                    validate_compose_project(
                        project_root=root,
                        compose_file=link,
                        project_name="ci-stack",
                        tool="docker",
                    )
            finally:
                outside.unlink(missing_ok=True)

    def test_reserved_identity_options_cannot_override_validated_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory).resolve())
            for option in (
                "-f",
                "-fother.yml",
                "--file=other.yml",
                "-p",
                "-pother",
                "--project-name=other",
                "--env-file=x",
            ):
                with self.subTest(option=option):
                    with self.assertRaisesRegex(ServiceComposeError, "compose_options_reserved"):
                        compose_up(project, environment={}, options=(option,), runner=RecordingRunner())

    def test_direct_project_construction_cannot_bypass_path_fencing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            compose = root / "compose.yml"
            compose.write_text("services: {}\n", encoding="utf-8")
            outside = root.parent / "forged-compose.yml"
            outside.write_text("services: {}\n", encoding="utf-8")
            try:
                forged = ComposeProject(
                    project_name="ci-stack",
                    tool="docker",
                    compose_relative="compose.yml",
                    env_file_count=0,
                    root=root,
                    compose_file=outside,
                    env_files=(),
                )
                with self.assertRaisesRegex(ServiceComposeError, "compose_project_invalid"):
                    compose_up(forged, environment={}, runner=RecordingRunner())
            finally:
                outside.unlink(missing_ok=True)


class CommandTests(ComposeFixture):
    def test_docker_and_podman_prefixes_keep_environment_values_out_of_argv(self) -> None:
        secret = "never-print-this"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for tool in ("docker", "podman"):
                with self.subTest(tool=tool):
                    project = self.make_project(root, tool=tool, env_file=True)
                    runner = RecordingRunner()
                    result = compose_up(
                        project,
                        environment={"CI_SECRET": secret},
                        services=("api", "db"),
                        runner=runner,
                    )
                    argv = runner.calls[0]["argv"]
                    self.assertEqual(argv[:2], (tool, "compose"))
                    self.assertIn(("--project-name", "ci-stack-42"), tuple(zip(argv, argv[1:])))
                    self.assertEqual(argv[-2:], ("api", "db"))
                    self.assertNotIn(secret, " ".join(argv))
                    self.assertNotIn(secret, repr(result))
                    self.assertEqual(runner.calls[0]["environment"]["CI_SECRET"], secret)

    def test_exec_and_run_are_argv_only_and_service_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory).resolve())
            runner = RecordingRunner([SUCCESS, SUCCESS])
            executed = compose_exec(
                project,
                service="api",
                command=("python", "-m", "unittest"),
                environment={},
                runner=runner,
            )
            ran = compose_run(
                project,
                service="worker",
                command=("tool", "check"),
                environment={},
                options=("--no-deps",),
                runner=runner,
            )
        self.assertIn(("exec", "-T", "api", "python", "-m", "unittest"), self._window(runner.calls[0]["argv"], 6))
        self.assertIn(("run", "--rm", "--no-deps", "worker", "tool", "check"), self._window(runner.calls[1]["argv"], 6))
        self.assertEqual(executed.service, "api")
        self.assertEqual(ran.service, "worker")
        self.assertNotIn("bash", runner.calls[0]["argv"])
        self.assertNotIn("sh", runner.calls[0]["argv"])

    @staticmethod
    def _window(values, length):
        return tuple(tuple(values[index:index + length]) for index in range(len(values) - length + 1))

    def test_ps_returns_only_bounded_service_container_metadata(self) -> None:
        stdout = json.dumps(
            [
                {"Service": "db", "Name": "ci-stack-42-db-1", "State": "running", "Health": "healthy", "ExitCode": 0, "ID": "private-container-id"},
                {"Service": "api", "Name": "ci-stack-42-api-1", "State": "running", "Health": "", "ExitCode": "0", "Labels": "secret=value"},
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory).resolve())
            result = compose_ps(
                project,
                environment={},
                runner=RecordingRunner([ProcessResult(0, stdout, "", False)]),
            )
        self.assertEqual(tuple(item.service for item in result.services), ("api", "db"))
        rendered = repr(result)
        self.assertNotIn("private-container-id", rendered)
        self.assertNotIn("secret=value", rendered)
        self.assertEqual(result.services[1].health, "healthy")

    def test_ps_accepts_valid_empty_stack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory).resolve())
            result = compose_ps(
                project,
                environment={},
                runner=RecordingRunner([ProcessResult(0, "[]", "", False)]),
            )
        self.assertEqual(result.services, ())

    def test_logs_are_line_and_byte_bounded_and_raw_text_is_hidden_from_repr(self) -> None:
        secret_log = "prefix-" + "sensitive-log-text" * 20
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory).resolve())
            runner = RecordingRunner([ProcessResult(0, secret_log, "", False)])
            result = capture_compose_logs(
                project,
                environment={},
                service="api",
                tail_lines=25,
                max_bytes=32,
                runner=runner,
            )
        self.assertTrue(result.truncated)
        self.assertLessEqual(result.size_bytes, 32)
        self.assertEqual(runner.calls[0]["argv"][-5:], ("logs", "--no-color", "--tail", "25", "api"))
        self.assertNotIn("sensitive-log-text", repr(result))
        self.assertTrue(result.text)


class ReadinessAndLifecycleTests(ComposeFixture):
    def test_multi_service_readiness_reuses_generic_tcp_http_postgres_primitives(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory).resolve())
            checks = (
                ComposeReadinessCheck("cache", "tcp", {"SERVICE_HOST": "cache", "SERVICE_PORT": "1001"}),
                ComposeReadinessCheck("api", "http", {"SERVICE_HTTP_URL": "http://api:1002/ready"}, expected_statuses=(204,)),
                ComposeReadinessCheck("db", "postgres", {"PGHOST": "db", "PGDATABASE": "tests", "PGPASSWORD": "top-secret"}),
            )
            with (
                mock.patch("ci_workflows.service_compose_primitives.wait_for_tcp_service", return_value=ServiceReadinessResult("tcp", True, 2)),
                mock.patch("ci_workflows.service_compose_primitives.wait_for_http_service", return_value=ServiceReadinessResult("http", True, 3, 204)),
                mock.patch("ci_workflows.service_compose_primitives.normalize_postgres_connection") as normalize,
                mock.patch("ci_workflows.service_compose_primitives.wait_for_postgres", return_value=ServiceReadinessResult("postgres", True, 1)) as wait_postgres,
            ):
                statuses = wait_for_compose_services(project, checks, environment={"PATH": "/usr/bin"})
        self.assertEqual(tuple(item.service for item in statuses), ("cache", "api", "db"))
        self.assertTrue(all(item.ready for item in statuses))
        self.assertEqual(wait_postgres.call_args.kwargs["environment"]["PGPASSWORD"], "top-secret")
        self.assertNotIn("top-secret", repr(checks))
        normalize.assert_called_once()

    def test_runtime_readiness_failure_is_projected_as_compose_boundary_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory).resolve())
            check = ComposeReadinessCheck("api", "tcp", {"SERVICE_HOST": "api", "SERVICE_PORT": "1001"})
            with mock.patch(
                "ci_workflows.service_compose_primitives.wait_for_tcp_service",
                side_effect=RuntimePrimitiveError("process_start_failed"),
            ):
                with self.assertRaises(ServiceComposeError) as caught:
                    wait_for_compose_services(project, (check,), environment={})
        self.assertEqual(caught.exception.code, "compose_readiness_boundary_failed")

    def test_failed_readiness_immediately_runs_down_for_exact_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory).resolve())
            runner = RecordingRunner([SUCCESS, SUCCESS])
            check = ComposeReadinessCheck("api", "tcp", {"SERVICE_HOST": "api", "SERVICE_PORT": "1001"})
            with mock.patch(
                "ci_workflows.service_compose_primitives.wait_for_compose_services",
                return_value=(mock.Mock(ready=False),),
            ):
                with self.assertRaisesRegex(ServiceComposeError, "compose_readiness_failed"):
                    start_compose_stack(project, environment={}, readiness=(check,), runner=runner)
        self.assertEqual(len(runner.calls), 2)
        self.assertIn("up", runner.calls[0]["argv"])
        down = runner.calls[1]["argv"]
        self.assertIn("down", down)
        self.assertIn("--remove-orphans", down)
        self.assertIn("ci-stack-42", down)

    def test_partial_up_failure_also_runs_down_and_preserves_primary_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory).resolve())
            runner = RecordingRunner([FAILURE, SUCCESS])
            check = ComposeReadinessCheck("api", "tcp", {"SERVICE_HOST": "api", "SERVICE_PORT": "1001"})
            with self.assertRaises(ServiceComposeError) as caught:
                start_compose_stack(project, environment={}, readiness=(check,), runner=runner)
        self.assertEqual(caught.exception.code, "compose_up_failed")
        self.assertEqual(caught.exception.cleanup_code, "")
        self.assertEqual(len(runner.calls), 2)
        self.assertIn("down", runner.calls[1]["argv"])

    def test_cleanup_failure_does_not_erase_readiness_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory).resolve())
            runner = RecordingRunner([SUCCESS, FAILURE])
            check = ComposeReadinessCheck("api", "tcp", {"SERVICE_HOST": "api", "SERVICE_PORT": "1001"})
            with mock.patch(
                "ci_workflows.service_compose_primitives.wait_for_compose_services",
                return_value=(mock.Mock(ready=False),),
            ):
                with self.assertRaises(ServiceComposeError) as caught:
                    start_compose_stack(project, environment={}, readiness=(check,), runner=runner)
        self.assertEqual(caught.exception.code, "compose_readiness_failed")
        self.assertEqual(caught.exception.cleanup_code, "compose_down_failed")

    def test_terminal_cleanup_is_exact_down_remove_orphans_for_validated_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory).resolve())
            runner = RecordingRunner()
            result = cleanup_compose_stack(project, environment={}, runner=runner)
        self.assertEqual(result.operation, "down")
        argv = runner.calls[0]["argv"]
        self.assertEqual(argv[-2:], ("down", "--remove-orphans"))
        self.assertIn(("--project-name", "ci-stack-42"), tuple(zip(argv, argv[1:])))

    def test_temporary_state_cleanup_delegates_to_root_bounded_runtime_finalizer(self) -> None:
        paths = [Path("/tmp/compose/state"), Path("/tmp/compose/auth")]
        with mock.patch(
            "ci_workflows.service_compose_primitives.finalize_temporary_paths",
            return_value=2,
        ) as finalize:
            removed = cleanup_compose_temporary_state(paths, root=Path("/tmp/compose"))
        self.assertEqual(removed, 2)
        finalize.assert_called_once_with(paths, root=Path("/tmp/compose"))

        with mock.patch(
            "ci_workflows.service_compose_primitives.finalize_temporary_paths",
            side_effect=RuntimePrimitiveError("cleanup_failed"),
        ):
            with self.assertRaisesRegex(ServiceComposeError, "compose_state_cleanup_failed"):
                cleanup_compose_temporary_state(paths, root=Path("/tmp/compose"))


class SourceBoundaryTests(unittest.TestCase):
    def test_module_has_no_product_image_port_or_infrastructure_identity(self) -> None:
        source = (
            Path(__file__)
            .parents[1]
            .joinpath("src/ci_workflows/service_compose_primitives.py")
            .read_text(encoding="utf-8")
            .casefold()
        )
        for forbidden in ("ffmpeg", "vlc", "mpv", "streamscape", "localhost:", "image:", "self-hosted"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
