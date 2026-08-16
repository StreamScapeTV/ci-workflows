from __future__ import annotations

import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from ci_workflows.runtime_primitives import ProcessResult
from ci_workflows.service_primitives import (
    PostgreSQLConnection,
    RunOwnedPostgreSQLTarget,
    ServicePrimitiveError,
    cleanup_run_owned_postgres_target,
    cleanup_temporary_service_state,
    connection_for_postgres_target,
    create_run_owned_postgres_target,
    execute_psql,
    normalize_postgres_connection,
    run_migration_command,
    run_owned_postgres_target,
    run_setup_command,
    run_test_command,
    wait_for_http_service,
    wait_for_postgres,
    wait_for_tcp_service,
)

SUCCESS = ProcessResult(0, "", "", False)
FAILURE = ProcessResult(1, "", "", False)


class PostgreSQLConnectionTests(unittest.TestCase):
    def test_dsn_normalization_hides_password_and_pins_libpq_environment(self) -> None:
        connection = normalize_postgres_connection(
            {
                "POSTGRES_DSN": (
                    "postgresql://ci-user:p%40ssword@db.internal:5544/ci_db"
                    "?sslmode=require"
                )
            }
        )
        self.assertEqual(connection.host, "db.internal")
        self.assertEqual(connection.port, 5544)
        self.assertEqual(connection.database, "ci_db")
        self.assertEqual(connection.username, "ci-user")
        self.assertEqual(connection.password, "p@ssword")
        self.assertEqual(connection.sslmode, "require")
        self.assertNotIn("p@ssword", repr(connection))

        environment = connection.process_environment(
            {
                "OTHER": "kept",
                "PGHOST": "stale",
                "PGPASSWORD": "stale-secret",
            }
        )
        self.assertEqual(environment["OTHER"], "kept")
        self.assertEqual(environment["PGHOST"], "db.internal")
        self.assertEqual(environment["PGPASSWORD"], "p@ssword")
        self.assertEqual(environment["PGDATABASE"], "ci_db")
        self.assertNotIn("POSTGRES_DSN", environment)

    def test_standard_pg_environment_uses_default_port_and_rejects_bad_port(self) -> None:
        connection = normalize_postgres_connection(
            {
                "PGHOST": "postgres.service",
                "PGDATABASE": "tests",
                "PGUSER": "runner",
                "PGPASSWORD": "secret",
            }
        )
        self.assertEqual(connection.port, 5432)
        with self.assertRaises(ServicePrimitiveError) as caught:
            normalize_postgres_connection(
                {
                    "PGHOST": "postgres.service",
                    "PGPORT": "70000",
                    "PGDATABASE": "tests",
                }
            )
        self.assertEqual(caught.exception.code, "postgres_port_invalid")
        self.assertNotIn("secret", str(caught.exception))

    def test_dsn_rejects_unbounded_query_options_without_leaking_secret(self) -> None:
        secret = "super-secret"
        with self.assertRaises(ServicePrimitiveError) as caught:
            normalize_postgres_connection(
                {
                    "POSTGRES_DSN": (
                        f"postgresql://runner:{secret}@db.internal/tests"
                        "?application_name=arbitrary"
                    )
                }
            )
        self.assertEqual(caught.exception.code, "postgres_dsn_query_invalid")
        self.assertNotIn(secret, str(caught.exception))


class ReadinessTests(unittest.TestCase):
    def test_tcp_readiness_retries_then_closes_connection(self) -> None:
        opened = mock.Mock()
        with (
            mock.patch(
                "ci_workflows.service_primitives.socket.create_connection",
                side_effect=[OSError("not ready"), opened],
            ) as create_connection,
            mock.patch(
                "ci_workflows.service_primitives.time.monotonic",
                return_value=0.0,
            ),
            mock.patch("ci_workflows.service_primitives.time.sleep"),
        ):
            result = wait_for_tcp_service(
                {"SERVICE_HOST": "db.internal", "SERVICE_PORT": "5432"},
                timeout_seconds=1,
            )
        self.assertTrue(result.ready)
        self.assertEqual(result.kind, "tcp")
        self.assertEqual(result.attempts, 2)
        opened.close.assert_called_once_with()
        self.assertEqual(create_connection.call_count, 2)

    def test_tcp_readiness_returns_bounded_timeout(self) -> None:
        with (
            mock.patch(
                "ci_workflows.service_primitives.socket.create_connection",
                side_effect=OSError("not ready"),
            ),
            mock.patch(
                "ci_workflows.service_primitives.time.monotonic",
                side_effect=[0.0, 0.0, 0.2],
            ),
        ):
            result = wait_for_tcp_service(
                {"SERVICE_HOST": "db.internal", "SERVICE_PORT": "5432"},
                timeout_seconds=0.1,
            )
        self.assertFalse(result.ready)
        self.assertEqual(result.attempts, 1)

    def test_http_readiness_accepts_selected_status_after_retry(self) -> None:
        unavailable = urllib.error.HTTPError(
            "https://service.internal/ready",
            503,
            "unavailable",
            hdrs=None,
            fp=None,
        )
        response = mock.MagicMock()
        response.__enter__.return_value.status = 204
        with (
            mock.patch(
                "ci_workflows.service_primitives.urllib.request.urlopen",
                side_effect=[unavailable, response],
            ) as urlopen,
            mock.patch(
                "ci_workflows.service_primitives.time.monotonic",
                return_value=0.0,
            ),
            mock.patch("ci_workflows.service_primitives.time.sleep"),
        ):
            result = wait_for_http_service(
                {"SERVICE_HTTP_URL": "https://service.internal/ready"},
                timeout_seconds=1,
                expected_statuses=(204,),
            )
        self.assertTrue(result.ready)
        self.assertEqual(result.status, 204)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(urlopen.call_count, 2)

    def test_http_readiness_rejects_credential_bearing_url(self) -> None:
        with self.assertRaises(ServicePrimitiveError) as caught:
            wait_for_http_service(
                {"SERVICE_HTTP_URL": "https://user:secret@service.internal/ready"},
                timeout_seconds=1,
            )
        self.assertEqual(caught.exception.code, "service_http_url_invalid")
        self.assertNotIn("secret", str(caught.exception))

    def test_postgres_readiness_uses_secret_environment_not_arguments(self) -> None:
        connection = PostgreSQLConnection(
            "db.internal",
            5432,
            "tests",
            "runner",
            "secret",
        )
        with (
            mock.patch(
                "ci_workflows.service_primitives.run_process",
                side_effect=[FAILURE, SUCCESS],
            ) as run_process,
            mock.patch(
                "ci_workflows.service_primitives.time.monotonic",
                return_value=0.0,
            ),
            mock.patch("ci_workflows.service_primitives.time.sleep"),
            tempfile.TemporaryDirectory() as directory,
        ):
            result = wait_for_postgres(
                connection,
                cwd=Path(directory).resolve(),
                environment={"PATH": "/usr/bin"},
                timeout_seconds=1,
            )
        self.assertTrue(result.ready)
        self.assertEqual(result.attempts, 2)
        arguments = run_process.call_args_list[0].args[0]
        environment = run_process.call_args_list[0].kwargs["environment"]
        self.assertEqual(arguments, ["pg_isready", "--quiet"])
        self.assertNotIn("secret", " ".join(arguments))
        self.assertEqual(environment["PGPASSWORD"], "secret")


class CommandTests(unittest.TestCase):
    def test_setup_migration_and_test_commands_are_argv_only_structured_runs(self) -> None:
        connection = PostgreSQLConnection("db.internal", 5432, "tests", password="secret")
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory).resolve()
            with mock.patch(
                "ci_workflows.service_primitives.run_process",
                return_value=SUCCESS,
            ) as run_process:
                for function, command in (
                    (run_setup_command, ["tool", "setup"]),
                    (run_migration_command, ["tool", "migrate"]),
                    (run_test_command, ["tool", "test"]),
                ):
                    with self.subTest(function=function.__name__):
                        result = function(
                            command,
                            cwd=cwd,
                            environment={"PATH": "/usr/bin"},
                            connection=connection,
                            timeout_seconds=30,
                        )
                        self.assertTrue(result.ok)
                        args = run_process.call_args.args[0]
                        kwargs = run_process.call_args.kwargs
                        self.assertEqual(args, command)
                        self.assertEqual(kwargs["environment"]["PGPASSWORD"], "secret")
                        self.assertEqual(kwargs["timeout_seconds"], 30)

    def test_execute_psql_sends_inline_sql_over_stdin_not_process_arguments(self) -> None:
        connection = PostgreSQLConnection("db.internal", 5432, "tests", password="secret")
        sql = "SELECT 1;"
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory).resolve()
            with mock.patch(
                "ci_workflows.service_primitives.run_process",
                return_value=SUCCESS,
            ) as run_process:
                result = execute_psql(
                    connection,
                    cwd=cwd,
                    environment={"PATH": "/usr/bin"},
                    sql=sql,
                )
        self.assertTrue(result.ok)
        arguments = run_process.call_args.args[0]
        kwargs = run_process.call_args.kwargs
        self.assertNotIn(sql, arguments)
        self.assertNotIn("secret", " ".join(arguments))
        self.assertEqual(kwargs["stdin"], sql)
        self.assertEqual(kwargs["environment"]["PGPASSWORD"], "secret")

    def test_execute_psql_accepts_only_bounded_regular_sql_file(self) -> None:
        connection = PostgreSQLConnection("db.internal", 5432, "tests")
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory).resolve()
            sql_file = cwd / "migration.sql"
            sql_file.write_text("SELECT 1;", encoding="utf-8")
            with mock.patch(
                "ci_workflows.service_primitives.run_process",
                return_value=SUCCESS,
            ) as run_process:
                execute_psql(
                    connection,
                    cwd=cwd,
                    environment={},
                    sql_file=Path("migration.sql"),
                )
            self.assertIn(str(sql_file), run_process.call_args.args[0])

            outside = cwd.parent / "outside.sql"
            outside.write_text("SELECT 2;", encoding="utf-8")
            try:
                with self.assertRaises(ServicePrimitiveError) as caught:
                    execute_psql(
                        connection,
                        cwd=cwd,
                        environment={},
                        sql_file=outside,
                    )
                self.assertEqual(caught.exception.code, "postgres_sql_file_invalid")
            finally:
                outside.unlink(missing_ok=True)


class RunOwnedTargetTests(unittest.TestCase):
    def test_creation_requires_explicit_request_and_derives_run_owned_name(self) -> None:
        connection = PostgreSQLConnection("db.internal", 5432, "postgres")
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory).resolve()
            with mock.patch(
                "ci_workflows.service_primitives.execute_psql",
                return_value=SUCCESS,
            ) as execute:
                skipped = create_run_owned_postgres_target(
                    connection,
                    requested=False,
                    kind="database",
                    run_id="run-123",
                    cwd=cwd,
                    environment={},
                )
                self.assertFalse(skipped.requested)
                execute.assert_not_called()

                created = create_run_owned_postgres_target(
                    connection,
                    requested=True,
                    kind="database",
                    run_id="run-123",
                    cwd=cwd,
                    environment={},
                )
        self.assertTrue(created.requested)
        self.assertTrue(created.ok)
        self.assertRegex(created.target.name, r"^ci_database_[a-f0-9]{12}$")
        self.assertIn(f'CREATE DATABASE "{created.target.name}";', execute.call_args.kwargs["sql"])
        target_connection = connection_for_postgres_target(connection, created.target)
        self.assertEqual(target_connection.database, created.target.name)
        self.assertEqual(connection.database, "postgres")

    def test_schema_cleanup_is_if_exists_and_safe_to_repeat(self) -> None:
        connection = PostgreSQLConnection("db.internal", 5432, "tests")
        target = run_owned_postgres_target(kind="schema", run_id="run-456")
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory).resolve()
            with mock.patch(
                "ci_workflows.service_primitives.execute_psql",
                return_value=SUCCESS,
            ) as execute:
                first = cleanup_run_owned_postgres_target(
                    connection,
                    target,
                    cwd=cwd,
                    environment={},
                )
                second = cleanup_run_owned_postgres_target(
                    connection,
                    target,
                    cwd=cwd,
                    environment={},
                )
        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(
            execute.call_args.kwargs["sql"],
            f'DROP SCHEMA IF EXISTS "{target.name}" CASCADE;',
        )

    def test_target_rejects_name_without_matching_run_owner_token(self) -> None:
        with self.assertRaises(ServicePrimitiveError) as caught:
            RunOwnedPostgreSQLTarget("database", "production", "a" * 12)
        self.assertEqual(caught.exception.code, "postgres_target_owner_invalid")

    def test_temporary_service_cleanup_delegates_to_bounded_runtime_cleanup(self) -> None:
        paths = [Path("/tmp/service/auth"), Path("/tmp/service/state")]
        with mock.patch(
            "ci_workflows.service_primitives.finalize_temporary_paths",
            return_value=2,
        ) as finalize:
            removed = cleanup_temporary_service_state(
                paths,
                root=Path("/tmp/service"),
            )
        self.assertEqual(removed, 2)
        finalize.assert_called_once_with(paths, root=Path("/tmp/service"))


if __name__ == "__main__":
    unittest.main()
