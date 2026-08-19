from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows.ciw_compose import (
    _readiness_checks,
    _sanitize_diagnostic,
    execute_compose_validate,
)
from ci_workflows.ciw_types import CIWContext
from ci_workflows.runtime_primitives import ProcessResult
from ci_workflows.service_compose_primitives import (
    ComposeReadinessStatus,
    ServiceComposeError,
)


class ComposeAdapterFixture(unittest.TestCase):
    def make_context(self, root: Path, **overrides: str) -> CIWContext:
        (root / "compose.yml").write_text("services: {}\n", encoding="utf-8")
        script = root / "validate.sh"
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        script.chmod(0o755)
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(root),
            "GITHUB_WORKSPACE": str(root),
            "GITHUB_RUN_ID": "4242",
            "GITHUB_RUN_ATTEMPT": "3",
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": "StreamScapeTV/example",
            "INPUT_ADMITTED_SHA": "a" * 40,
            "INPUT_WORKING_DIRECTORY": ".",
            "INPUT_COMPOSE_FILE": "compose.yml",
            "INPUT_COMPOSE_TOOL": "podman",
            "INPUT_SERVICES_JSON": json.dumps(["api", "db"]),
            "INPUT_ENV_FILES_JSON": "[]",
            "INPUT_READINESS_JSON": json.dumps(
                [
                    {
                        "service": "api",
                        "kind": "http",
                        "url": "http://127.0.0.1:18080/ready",
                        "expected_statuses": [200],
                    },
                    {
                        "service": "db",
                        "kind": "tcp",
                        "host": "127.0.0.1",
                        "port": 15432,
                    },
                ]
            ),
            "INPUT_VALIDATION_SCRIPT_PATH": "validate.sh",
            "INPUT_VALIDATION_TIMEOUT_SECONDS": "30",
            "TOP_SECRET": "must-not-be-forwarded",
        }
        environment.update(overrides)
        return CIWContext(
            root=root,
            environment=environment,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )


class ComposeRequestTests(ComposeAdapterFixture):
    def test_readiness_json_maps_only_bounded_tcp_http_and_postgres_fields(self) -> None:
        checks = _readiness_checks(
            json.dumps(
                [
                    {"service": "cache", "kind": "tcp", "host": "cache", "port": "6379"},
                    {
                        "service": "api",
                        "kind": "http",
                        "url": "http://api:8080/health",
                        "expected_statuses": [200, 204],
                    },
                    {
                        "service": "db",
                        "kind": "postgres",
                        "host": "db",
                        "port": 5432,
                        "database": "tests",
                        "user": "postgres",
                    },
                ]
            )
        )
        self.assertEqual(tuple(item.kind for item in checks), ("tcp", "http", "postgres"))
        self.assertEqual(checks[0].environment, {"SERVICE_HOST": "cache", "SERVICE_PORT": "6379"})
        self.assertEqual(checks[1].environment, {"SERVICE_HTTP_URL": "http://api:8080/health"})
        self.assertEqual(
            checks[2].environment,
            {"PGHOST": "db", "PGPORT": "5432", "PGDATABASE": "tests", "PGUSER": "postgres"},
        )

    def test_readiness_rejects_arbitrary_environment_and_url_credentials(self) -> None:
        for payload in (
            [{"service": "api", "kind": "tcp", "host": "api", "port": 1, "environment": {"SECRET": "x"}}],
            [{"service": "api", "kind": "http", "url": "https://user:password@example.test/ready"}],
        ):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ServiceComposeError, "compose_readiness_input_invalid"):
                    _readiness_checks(json.dumps(payload))

    def test_failure_diagnostic_redaction_hides_paths_credentials_and_common_secret_assignments(self) -> None:
        root = Path("/tmp/private-project")
        rendered = _sanitize_diagnostic(
            f"{root}/state password=hunter2 token=abc https://user:pw@example.test/api",
            root=root,
        )
        self.assertNotIn(str(root), rendered)
        self.assertNotIn("hunter2", rendered)
        self.assertNotIn("token=abc", rendered)
        self.assertNotIn("user:pw", rendered)
        self.assertIn("<project>", rendered)
        self.assertIn("<redacted>", rendered)


class ComposeLifecycleTests(ComposeAdapterFixture):
    def test_success_uses_run_owned_project_multi_service_readiness_and_terminal_cleanup(self) -> None:
        ready = (
            ComposeReadinessStatus("api", "http", True, 2, 200),
            ComposeReadinessStatus("db", "tcp", True, 1, None),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            context = self.make_context(root)
            with (
                mock.patch("ci_workflows.ciw_compose.compose_up") as up,
                mock.patch("ci_workflows.ciw_compose.wait_for_compose_services", return_value=ready) as wait,
                mock.patch(
                    "ci_workflows.ciw_compose.run_process",
                    return_value=ProcessResult(0, "tests passed", "", False),
                ) as run,
                mock.patch("ci_workflows.ciw_compose.cleanup_compose_stack") as cleanup,
            ):
                result = execute_compose_validate(argparse.Namespace(), context)

        self.assertEqual(result.outputs["result"], "success")
        self.assertEqual(result.outputs["cleanup_result"], "success")
        self.assertEqual(result.outputs["project_name"], "ciw-4242-3")
        summary = json.loads(result.outputs["test_summary"])
        self.assertEqual(summary["services"], ["api", "db"])
        self.assertEqual(len(summary["readiness"]), 2)
        project = up.call_args.args[0]
        self.assertEqual(project.project_name, "ciw-4242-3")
        self.assertEqual(project.tool, "podman")
        self.assertEqual(tuple(item.kind for item in wait.call_args.args[1]), ("http", "tcp"))
        self.assertEqual(up.call_args.kwargs["services"], ("api", "db"))
        validation_environment = run.call_args.kwargs["environment"]
        self.assertEqual(validation_environment["CIW_COMPOSE_PROJECT_NAME"], "ciw-4242-3")
        self.assertNotIn("TOP_SECRET", validation_environment)
        cleanup.assert_called_once()
        self.assertIs(cleanup.call_args.args[0], project)

    def test_readiness_failure_never_runs_validation_and_still_tears_down(self) -> None:
        not_ready = (ComposeReadinessStatus("api", "http", False, 8, 503),)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            context = self.make_context(root)
            with (
                mock.patch("ci_workflows.ciw_compose.compose_up"),
                mock.patch("ci_workflows.ciw_compose.wait_for_compose_services", return_value=not_ready),
                mock.patch("ci_workflows.ciw_compose.run_process") as run,
                mock.patch("ci_workflows.ciw_compose._emit_failure_diagnostics") as diagnostics,
                mock.patch("ci_workflows.ciw_compose.cleanup_compose_stack") as cleanup,
            ):
                with self.assertRaises(ServiceComposeError) as caught:
                    execute_compose_validate(argparse.Namespace(), context)

        self.assertEqual(caught.exception.code, "compose_readiness_failed")
        self.assertEqual(caught.exception.cleanup_code, "")
        run.assert_not_called()
        diagnostics.assert_called_once()
        cleanup.assert_called_once()

    def test_validation_failure_preserves_primary_result_and_always_tears_down(self) -> None:
        ready = (ComposeReadinessStatus("api", "http", True, 1, 200),)
        failed = ProcessResult(7, "partial output", "assertion failed", False)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            context = self.make_context(root)
            with (
                mock.patch("ci_workflows.ciw_compose.compose_up"),
                mock.patch("ci_workflows.ciw_compose.wait_for_compose_services", return_value=ready),
                mock.patch("ci_workflows.ciw_compose.run_process", return_value=failed),
                mock.patch("ci_workflows.ciw_compose._emit_failure_diagnostics") as diagnostics,
                mock.patch("ci_workflows.ciw_compose.cleanup_compose_stack") as cleanup,
            ):
                with self.assertRaises(ServiceComposeError) as caught:
                    execute_compose_validate(argparse.Namespace(), context)

        self.assertEqual(caught.exception.code, "compose_validation_failed")
        diagnostics.assert_called_once()
        self.assertIs(diagnostics.call_args.kwargs["validation"], failed)
        cleanup.assert_called_once()

    def test_cleanup_failure_does_not_erase_validation_failure(self) -> None:
        ready = (ComposeReadinessStatus("api", "http", True, 1, 200),)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            context = self.make_context(root)
            with (
                mock.patch("ci_workflows.ciw_compose.compose_up"),
                mock.patch("ci_workflows.ciw_compose.wait_for_compose_services", return_value=ready),
                mock.patch(
                    "ci_workflows.ciw_compose.run_process",
                    return_value=ProcessResult(4, "", "failed", False),
                ),
                mock.patch("ci_workflows.ciw_compose._emit_failure_diagnostics"),
                mock.patch(
                    "ci_workflows.ciw_compose.cleanup_compose_stack",
                    side_effect=ServiceComposeError("compose_down_failed"),
                ),
            ):
                with self.assertRaises(ServiceComposeError) as caught:
                    execute_compose_validate(argparse.Namespace(), context)

        self.assertEqual(caught.exception.code, "compose_validation_failed")
        self.assertEqual(caught.exception.cleanup_code, "compose_down_failed")

    def test_partial_up_failure_still_attempts_exact_project_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            context = self.make_context(root)
            with (
                mock.patch(
                    "ci_workflows.ciw_compose.compose_up",
                    side_effect=ServiceComposeError("compose_up_failed"),
                ),
                mock.patch("ci_workflows.ciw_compose._emit_failure_diagnostics"),
                mock.patch("ci_workflows.ciw_compose.cleanup_compose_stack") as cleanup,
            ):
                with self.assertRaises(ServiceComposeError) as caught:
                    execute_compose_validate(argparse.Namespace(), context)

        self.assertEqual(caught.exception.code, "compose_up_failed")
        cleanup.assert_called_once()
        self.assertEqual(cleanup.call_args.args[0].project_name, "ciw-4242-3")

    def test_validation_script_must_be_checked_in_style_relative_non_symlink_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            context = self.make_context(root, INPUT_VALIDATION_SCRIPT_PATH="/tmp/outside.sh")
            with mock.patch("ci_workflows.ciw_compose.compose_up") as up:
                with self.assertRaisesRegex(ServiceComposeError, "compose_validation_script_invalid"):
                    execute_compose_validate(argparse.Namespace(), context)
        up.assert_not_called()


if __name__ == "__main__":
    unittest.main()
