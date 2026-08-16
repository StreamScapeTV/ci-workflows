from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows.ciw_compose import configure_compose, execute_compose
from ci_workflows.ciw_types import CIWContext, CIWError
from ci_workflows.service_compose_primitives import (
    ComposeCommandResult,
    ComposeReadinessStatus,
    ComposeStackResult,
)


class ComposeCIWAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.workspace = root / "workspace"
        self.project = self.workspace / "source"
        self.project.mkdir(parents=True)
        (self.project / "compose.yml").write_text("services: {}\n", encoding="utf-8")
        self.context = CIWContext(
            root=self.workspace,
            environment={
                "GITHUB_WORKSPACE": str(self.workspace),
                "GITHUB_REPOSITORY": "Example/Repository",
                "GITHUB_RUN_ID": "123",
                "GITHUB_RUN_ATTEMPT": "2",
                "GITHUB_JOB": "service-test",
                "CI_COMPOSE_TOOL": "podman",
                "PGPASSWORD": "secret-not-an-output",
            },
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def args(**values: object) -> argparse.Namespace:
        defaults: dict[str, object] = {
            "phase": "start",
            "project_root": "source",
            "compose_file": "compose.yml",
            "env_files_json": None,
            "services_json": None,
            "readiness_json": None,
            "service": None,
            "command_json": None,
            "options_json": None,
            "tail_lines": None,
            "max_log_bytes": None,
        }
        defaults.update(values)
        return argparse.Namespace(**defaults)

    def test_parser_does_not_expose_container_engine_or_project_identity(self) -> None:
        parser = argparse.ArgumentParser()
        configure_compose(parser)
        parsed = parser.parse_args(["--phase", "cleanup", "--compose-file", "compose.yml"])
        self.assertEqual(parsed.phase, "cleanup")
        self.assertFalse(hasattr(parsed, "tool"))
        self.assertFalse(hasattr(parsed, "project_name"))
        self.assertFalse(hasattr(parsed, "runner"))

    def test_start_uses_derived_project_and_typed_readiness_without_secret_output(self) -> None:
        command = ComposeCommandResult("up", 0, False, "a" * 64, 12)
        stack = ComposeStackResult(
            "ciw-derived",
            command,
            (ComposeReadinessStatus("database", "postgres", True, 2, 0),),
        )
        readiness = (
            '[{"service":"database","kind":"postgres","host":"127.0.0.1",'
            '"port":5432,"database":"app_test","username":"ci"}]'
        )
        with patch("ci_workflows.ciw_compose.start_compose_stack", return_value=stack) as primitive:
            result = execute_compose(
                self.args(phase="start", readiness_json=readiness, services_json='["database"]'),
                self.context,
            )
        project = primitive.call_args.args[0]
        self.assertEqual(project.tool, "podman")
        self.assertTrue(project.project_name.startswith("ciw-"))
        check = primitive.call_args.kwargs["readiness"][0]
        self.assertEqual(check.environment["PGHOST"], "127.0.0.1")
        self.assertEqual(check.environment["PGPASSWORD"], "secret-not-an-output")
        self.assertNotIn("secret-not-an-output", result.outputs["compose_result_json"])
        self.assertIn('"kind":"postgres"', result.outputs["compose_result_json"])

    def test_exec_delegates_argv_without_shell_string(self) -> None:
        command = ComposeCommandResult("exec", 0, False, "b" * 64, 7, service="api")
        with patch("ci_workflows.ciw_compose.compose_exec", return_value=command) as primitive:
            result = execute_compose(
                self.args(
                    phase="exec",
                    service="api",
                    command_json='["python","-m","pytest","-q"]',
                    options_json='["--no-TTY"]',
                ),
                self.context,
            )
        self.assertEqual(primitive.call_args.kwargs["command"], ("python", "-m", "pytest", "-q"))
        self.assertEqual(primitive.call_args.kwargs["service"], "api")
        self.assertEqual(result.outputs["result"], "success")

    def test_cleanup_targets_only_the_derived_compose_project(self) -> None:
        command = ComposeCommandResult("down", 0, False, "c" * 64, 4)
        with patch("ci_workflows.ciw_compose.cleanup_compose_stack", return_value=command) as primitive:
            result = execute_compose(self.args(phase="cleanup"), self.context)
        project = primitive.call_args.args[0]
        self.assertTrue(project.project_name.startswith("ciw-"))
        self.assertEqual(project.compose_relative, "compose.yml")
        self.assertIn('"operation":"cleanup"', result.outputs["compose_result_json"])

    def test_readiness_rejects_secret_or_unknown_fields(self) -> None:
        bad = (
            '[{"service":"database","kind":"postgres","host":"127.0.0.1",'
            '"database":"app_test","password":"do-not-accept"}]'
        )
        with self.assertRaises(CIWError) as raised:
            execute_compose(self.args(phase="start", readiness_json=bad), self.context)
        self.assertEqual(raised.exception.code, "readiness_invalid")

    def test_compose_file_escape_fails_closed_before_start(self) -> None:
        with self.assertRaises(CIWError):
            execute_compose(
                self.args(
                    phase="start",
                    compose_file="../compose.yml",
                    readiness_json='[{"service":"api","kind":"tcp","host":"127.0.0.1","port":8080}]',
                ),
                self.context,
            )


if __name__ == "__main__":
    unittest.main()
