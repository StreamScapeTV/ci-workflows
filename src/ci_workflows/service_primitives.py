"""Product-neutral PostgreSQL and service-test primitives for shared CI."""
from __future__ import annotations

import hashlib
import math
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Literal, Mapping, Sequence

from .runtime_primitives import ProcessResult, finalize_temporary_paths, run_process

_POSTGRES_DSN_ENV = "POSTGRES_DSN"
_POSTGRES_ENVIRONMENT = ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGSSLMODE")
_SERVICE_HOST_ENV = "SERVICE_HOST"
_SERVICE_PORT_ENV = "SERVICE_PORT"
_SERVICE_HTTP_URL_ENV = "SERVICE_HTTP_URL"
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_RUN_TOKEN = re.compile(r"^[a-f0-9]{12}$")
_GENERATED_TARGET = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_SSLMODE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_MAX_READINESS_SECONDS = 600.0

class ServicePrimitiveError(RuntimeError):
    """Fail closed with one stable non-secret service primitive code."""
    def __init__(self, code: str) -> None:
        if _ERROR_CODE.fullmatch(code) is None:
            raise ValueError("service primitive error code must be a safe identifier")
        self.code = code
        super().__init__(code)

@dataclass(frozen=True, slots=True)
class PostgreSQLConnection:
    """Normalized libpq connection fields with password hidden from repr."""
    host: str
    port: int
    database: str
    username: str = ""
    password: str = field(default="", repr=False)
    sslmode: str = ""
    def __post_init__(self) -> None:
        if not _plain_value(self.host, maximum=253):
            raise ServicePrimitiveError("postgres_host_invalid")
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise ServicePrimitiveError("postgres_port_invalid")
        if not _plain_value(self.database, maximum=128):
            raise ServicePrimitiveError("postgres_database_invalid")
        if self.username and not _plain_value(self.username, maximum=128):
            raise ServicePrimitiveError("postgres_username_invalid")
        if not isinstance(self.password, str) or any(token in self.password for token in ("\x00", "\r", "\n")):
            raise ServicePrimitiveError("postgres_password_invalid")
        if self.sslmode and (not isinstance(self.sslmode, str) or _SSLMODE.fullmatch(self.sslmode) is None):
            raise ServicePrimitiveError("postgres_sslmode_invalid")
    def process_environment(self, environment: Mapping[str, str]) -> dict[str, str]:
        if not isinstance(environment, Mapping) or any(not isinstance(name, str) or not isinstance(value, str) for name, value in environment.items()):
            raise ServicePrimitiveError("service_environment_invalid")
        result = dict(environment)
        result.pop(_POSTGRES_DSN_ENV, None)
        for name in _POSTGRES_ENVIRONMENT:
            result.pop(name, None)
        result["PGHOST"] = self.host
        result["PGPORT"] = str(self.port)
        result["PGDATABASE"] = self.database
        if self.username:
            result["PGUSER"] = self.username
        if self.password:
            result["PGPASSWORD"] = self.password
        if self.sslmode:
            result["PGSSLMODE"] = self.sslmode
        return result
    def with_database(self, database: str) -> "PostgreSQLConnection":
        return replace(self, database=database)

@dataclass(frozen=True, slots=True)
class ServiceReadinessResult:
    kind: Literal["postgres", "tcp", "http"]
    ready: bool
    attempts: int
    status: int | None = None

@dataclass(frozen=True, slots=True)
class RunOwnedPostgreSQLTarget:
    kind: Literal["database", "schema"]
    name: str
    owner_token: str
    def __post_init__(self) -> None:
        if self.kind not in ("database", "schema"):
            raise ServicePrimitiveError("postgres_target_kind_invalid")
        if not isinstance(self.name, str) or _GENERATED_TARGET.fullmatch(self.name) is None:
            raise ServicePrimitiveError("postgres_target_name_invalid")
        if not isinstance(self.owner_token, str) or _RUN_TOKEN.fullmatch(self.owner_token) is None:
            raise ServicePrimitiveError("postgres_target_owner_invalid")
        if self.name != f"ci_{self.kind}_{self.owner_token}":
            raise ServicePrimitiveError("postgres_target_owner_invalid")

@dataclass(frozen=True, slots=True)
class PostgreSQLTargetResult:
    target: RunOwnedPostgreSQLTarget | None
    process: ProcessResult | None
    @property
    def requested(self) -> bool:
        return self.target is not None
    @property
    def ok(self) -> bool:
        return not self.requested or bool(self.process and self.process.ok)

def _plain_value(value: object, *, maximum: int) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip() and len(value) <= maximum and not any(token in value for token in ("\x00", "\r", "\n"))

def _environment_value(environment: Mapping[str, str], name: str, *, required: bool, maximum: int) -> str:
    if not isinstance(environment, Mapping):
        raise ServicePrimitiveError("service_environment_invalid")
    value = environment.get(name, "")
    if not isinstance(value, str):
        raise ServicePrimitiveError("service_environment_invalid")
    value = value.strip()
    if required and not value:
        raise ServicePrimitiveError(f"{name.lower()}_required")
    if value and not _plain_value(value, maximum=maximum):
        raise ServicePrimitiveError(f"{name.lower()}_invalid")
    return value

def _parse_postgres_dsn(dsn: str) -> PostgreSQLConnection:
    if not _plain_value(dsn, maximum=4096):
        raise ServicePrimitiveError("postgres_dsn_invalid")
    try:
        parsed = urllib.parse.urlsplit(dsn)
        parsed_port = parsed.port
    except ValueError as error:
        raise ServicePrimitiveError("postgres_dsn_invalid") from error
    if parsed.scheme not in ("postgres", "postgresql"):
        raise ServicePrimitiveError("postgres_dsn_scheme_invalid")
    if not parsed.hostname or parsed.fragment:
        raise ServicePrimitiveError("postgres_dsn_invalid")
    database = urllib.parse.unquote(parsed.path.lstrip("/"))
    if not database or "/" in database:
        raise ServicePrimitiveError("postgres_database_invalid")
    try:
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as error:
        raise ServicePrimitiveError("postgres_dsn_query_invalid") from error
    if any(name != "sslmode" or len(values) != 1 for name, values in query.items()):
        raise ServicePrimitiveError("postgres_dsn_query_invalid")
    return PostgreSQLConnection(
        host=parsed.hostname,
        port=parsed_port if parsed_port is not None else 5432,
        database=database,
        username=urllib.parse.unquote(parsed.username or ""),
        password=urllib.parse.unquote(parsed.password or ""),
        sslmode=query.get("sslmode", [""])[0],
    )

def normalize_postgres_connection(environment: Mapping[str, str]) -> PostgreSQLConnection:
    """Normalize fixed PostgreSQL environment variables or one POSTGRES_DSN."""
    dsn = _environment_value(environment, _POSTGRES_DSN_ENV, required=False, maximum=4096)
    if dsn:
        return _parse_postgres_dsn(dsn)
    host = _environment_value(environment, "PGHOST", required=True, maximum=253)
    port_text = _environment_value(environment, "PGPORT", required=False, maximum=5)
    try:
        port = int(port_text or "5432")
    except ValueError as error:
        raise ServicePrimitiveError("postgres_port_invalid") from error
    database = _environment_value(environment, "PGDATABASE", required=True, maximum=128)
    username = _environment_value(environment, "PGUSER", required=False, maximum=128)
    password = environment.get("PGPASSWORD", "")
    if not isinstance(password, str) or any(token in password for token in ("\x00", "\r", "\n")):
        raise ServicePrimitiveError("postgres_password_invalid")
    sslmode = _environment_value(environment, "PGSSLMODE", required=False, maximum=32)
    return PostgreSQLConnection(host, port, database, username, password, sslmode)

def _readiness_duration(value: float, *, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value <= 0 or value > _MAX_READINESS_SECONDS:
        raise ServicePrimitiveError(code)
    return float(value)

def _wait_for_probe(kind: Literal["postgres", "tcp", "http"], probe: Callable[[float], tuple[bool, int | None]], *, timeout_seconds: float, interval_seconds: float) -> ServiceReadinessResult:
    timeout = _readiness_duration(timeout_seconds, code="service_timeout_invalid")
    interval = _readiness_duration(interval_seconds, code="service_interval_invalid")
    deadline = time.monotonic() + timeout
    attempts = 0
    last_status: int | None = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 and attempts:
            return ServiceReadinessResult(kind, False, attempts, last_status)
        attempts += 1
        ready, last_status = probe(max(remaining, 0.001))
        if ready:
            return ServiceReadinessResult(kind, True, attempts, last_status)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return ServiceReadinessResult(kind, False, attempts, last_status)
        time.sleep(min(interval, remaining))

def wait_for_tcp_service(environment: Mapping[str, str], *, timeout_seconds: float, interval_seconds: float = 0.25) -> ServiceReadinessResult:
    host = _environment_value(environment, _SERVICE_HOST_ENV, required=True, maximum=253)
    port_text = _environment_value(environment, _SERVICE_PORT_ENV, required=True, maximum=5)
    try:
        port = int(port_text)
    except ValueError as error:
        raise ServicePrimitiveError("service_port_invalid") from error
    if not 1 <= port <= 65535:
        raise ServicePrimitiveError("service_port_invalid")
    def probe(remaining: float) -> tuple[bool, int | None]:
        try:
            connection = socket.create_connection((host, port), timeout=remaining)
        except OSError:
            return False, None
        try:
            return True, None
        finally:
            connection.close()
    return _wait_for_probe("tcp", probe, timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)

def _http_url(environment: Mapping[str, str]) -> str:
    url = _environment_value(environment, _SERVICE_HTTP_URL_ENV, required=True, maximum=2048)
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as error:
        raise ServicePrimitiveError("service_http_url_invalid") from error
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ServicePrimitiveError("service_http_url_invalid")
    return url

def wait_for_http_service(environment: Mapping[str, str], *, timeout_seconds: float, interval_seconds: float = 0.25, expected_statuses: Sequence[int] = (200, 204)) -> ServiceReadinessResult:
    url = _http_url(environment)
    if isinstance(expected_statuses, (str, bytes)) or not expected_statuses or any(isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599 for status in expected_statuses):
        raise ServicePrimitiveError("service_http_statuses_invalid")
    accepted = frozenset(expected_statuses)
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "ci-workflows-service-readiness/1"})
    def probe(remaining: float) -> tuple[bool, int | None]:
        try:
            with urllib.request.urlopen(request, timeout=remaining) as response:
                status = int(response.status)
        except urllib.error.HTTPError as error:
            status = int(error.code)
            error.close()
        except (urllib.error.URLError, TimeoutError, OSError):
            return False, None
        return status in accepted, status
    return _wait_for_probe("http", probe, timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)

def wait_for_postgres(connection: PostgreSQLConnection, *, cwd: Path, environment: Mapping[str, str], timeout_seconds: float, interval_seconds: float = 0.25, executable: str = "pg_isready") -> ServiceReadinessResult:
    if not _plain_value(executable, maximum=512):
        raise ServicePrimitiveError("postgres_readiness_executable_invalid")
    process_environment = connection.process_environment(environment)
    def probe(remaining: float) -> tuple[bool, int | None]:
        result = run_process([executable, "--quiet"], cwd=cwd, environment=process_environment, timeout_seconds=remaining)
        return result.ok, result.returncode
    return _wait_for_probe("postgres", probe, timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)

def run_service_command(arguments: Sequence[str], *, cwd: Path, environment: Mapping[str, str], connection: PostgreSQLConnection | None = None, stdin: str = "", timeout_seconds: float | None = None) -> ProcessResult:
    process_environment = connection.process_environment(environment) if connection is not None else dict(environment)
    return run_process(arguments, cwd=cwd, environment=process_environment, stdin=stdin, timeout_seconds=timeout_seconds)

def run_setup_command(arguments: Sequence[str], *, cwd: Path, environment: Mapping[str, str], connection: PostgreSQLConnection | None = None, timeout_seconds: float | None = None) -> ProcessResult:
    return run_service_command(arguments, cwd=cwd, environment=environment, connection=connection, timeout_seconds=timeout_seconds)

def run_migration_command(arguments: Sequence[str], *, cwd: Path, environment: Mapping[str, str], connection: PostgreSQLConnection | None = None, timeout_seconds: float | None = None) -> ProcessResult:
    return run_service_command(arguments, cwd=cwd, environment=environment, connection=connection, timeout_seconds=timeout_seconds)

def run_test_command(arguments: Sequence[str], *, cwd: Path, environment: Mapping[str, str], connection: PostgreSQLConnection | None = None, timeout_seconds: float | None = None) -> ProcessResult:
    return run_service_command(arguments, cwd=cwd, environment=environment, connection=connection, timeout_seconds=timeout_seconds)

def _bounded_sql_file(cwd: Path, sql_file: Path) -> Path:
    root = Path(cwd)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ServicePrimitiveError("service_cwd_invalid")
    root = root.resolve()
    candidate = Path(sql_file)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise ServicePrimitiveError("postgres_sql_file_invalid") from error
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ServicePrimitiveError("postgres_sql_file_invalid")
    try:
        candidate = candidate.resolve(strict=True)
        candidate.relative_to(root)
    except (OSError, ValueError) as error:
        raise ServicePrimitiveError("postgres_sql_file_invalid") from error
    if not candidate.is_file():
        raise ServicePrimitiveError("postgres_sql_file_invalid")
    return candidate

def execute_psql(connection: PostgreSQLConnection, *, cwd: Path, environment: Mapping[str, str], sql: str | None = None, sql_file: Path | None = None, executable: str = "psql", timeout_seconds: float | None = None) -> ProcessResult:
    if (sql is None) == (sql_file is None):
        raise ServicePrimitiveError("postgres_sql_source_invalid")
    if not _plain_value(executable, maximum=512):
        raise ServicePrimitiveError("postgres_psql_executable_invalid")
    arguments = [executable, "--no-psqlrc", "--set", "ON_ERROR_STOP=1", "--quiet"]
    stdin = ""
    if sql is not None:
        if not isinstance(sql, str) or not sql or "\x00" in sql:
            raise ServicePrimitiveError("postgres_sql_invalid")
        stdin = sql
    else:
        bounded = _bounded_sql_file(cwd, Path(sql_file))
        arguments.extend(["--file", str(bounded)])
    return run_process(arguments, cwd=cwd, environment=connection.process_environment(environment), stdin=stdin, timeout_seconds=timeout_seconds)

def _owner_token(run_id: str) -> str:
    if not isinstance(run_id, str) or not run_id or len(run_id) > 256 or any(token in run_id for token in ("\x00", "\r", "\n")):
        raise ServicePrimitiveError("postgres_run_id_invalid")
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12]

def run_owned_postgres_target(*, kind: Literal["database", "schema"], run_id: str) -> RunOwnedPostgreSQLTarget:
    if kind not in ("database", "schema"):
        raise ServicePrimitiveError("postgres_target_kind_invalid")
    token = _owner_token(run_id)
    return RunOwnedPostgreSQLTarget(kind, f"ci_{kind}_{token}", token)

def create_run_owned_postgres_target(connection: PostgreSQLConnection, *, requested: bool, kind: Literal["database", "schema"], run_id: str, cwd: Path, environment: Mapping[str, str], executable: str = "psql", timeout_seconds: float | None = None) -> PostgreSQLTargetResult:
    if not isinstance(requested, bool):
        raise ServicePrimitiveError("postgres_target_request_invalid")
    if not requested:
        return PostgreSQLTargetResult(None, None)
    target = run_owned_postgres_target(kind=kind, run_id=run_id)
    quoted = f'"{target.name}"'
    sql = f"CREATE DATABASE {quoted};" if target.kind == "database" else f"CREATE SCHEMA {quoted};"
    return PostgreSQLTargetResult(target, execute_psql(connection, cwd=cwd, environment=environment, sql=sql, executable=executable, timeout_seconds=timeout_seconds))

def connection_for_postgres_target(connection: PostgreSQLConnection, target: RunOwnedPostgreSQLTarget) -> PostgreSQLConnection:
    return connection.with_database(target.name) if target.kind == "database" else connection

def cleanup_run_owned_postgres_target(connection: PostgreSQLConnection, target: RunOwnedPostgreSQLTarget | None, *, cwd: Path, environment: Mapping[str, str], executable: str = "psql", timeout_seconds: float | None = None) -> ProcessResult | None:
    if target is None:
        return None
    RunOwnedPostgreSQLTarget(target.kind, target.name, target.owner_token)
    quoted = f'"{target.name}"'
    sql = f"DROP DATABASE IF EXISTS {quoted};" if target.kind == "database" else f"DROP SCHEMA IF EXISTS {quoted} CASCADE;"
    return execute_psql(connection, cwd=cwd, environment=environment, sql=sql, executable=executable, timeout_seconds=timeout_seconds)

def cleanup_temporary_service_state(paths: Sequence[Path], *, root: Path) -> int:
    return finalize_temporary_paths(paths, root=root)
