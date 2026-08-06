from __future__ import annotations

import contextlib
import hashlib
import os
import pathlib
import shutil
import socket
import subprocess
import tarfile
import tempfile
import unittest
import urllib.error
import urllib.request
from dataclasses import dataclass

POSTGRES_VERSION = "17.6"
POSTGRES_SOURCE_URL = "https://ftp.postgresql.org/pub/source/v17.6/postgresql-17.6.tar.gz"
POSTGRES_SOURCE_SHA256 = (
    "2910b85283674da2dae6ac13fe5ebbaaf3c482446396cba32e6728d3cc736d86"
)
REQUIRED_BINARIES = (
    "initdb",
    "pg_ctl",
    "postgres",
    "psql",
    "createdb",
    "dropdb",
    "pg_dump",
)
RECONSTRUCTION_OPT_IN = "AGENT_STATE_RUN_POSTGRES_RECONSTRUCTION"
CONFIGURED_BIN_DIR = "AGENT_STATE_POSTGRES_BIN"


class CommandError(RuntimeError):
    pass


def _run(
    command: list[str],
    *,
    cwd: pathlib.Path | None = None,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    check: bool = True,
    timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        detail = "\n".join((result.stdout + "\n" + result.stderr).splitlines()[-80:])
        raise CommandError(f"command failed ({result.returncode}): {' '.join(command)}\n{detail}")
    return result


def _require_explicit_reconstruction() -> None:
    if os.environ.get(RECONSTRUCTION_OPT_IN) != "1":
        raise unittest.SkipTest(
            "explicit PostgreSQL 17 reconstruction is not part of normal test discovery; "
            f"set {RECONSTRUCTION_OPT_IN}=1 and run the documented reconstruction command"
        )


def _safe_extract(archive: pathlib.Path, destination: pathlib.Path) -> None:
    destination_real = destination.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            if target != destination_real and destination_real not in target.parents:
                raise CommandError(f"unsafe archive member: {member.name}")
        tar.extractall(destination)


def _validate_bin_dir(bin_dir: pathlib.Path) -> pathlib.Path | None:
    if not all((bin_dir / name).is_file() for name in REQUIRED_BINARIES):
        return None
    version = _run([str(bin_dir / "initdb"), "--version"], check=False).stdout
    if f" {POSTGRES_VERSION}" not in version and " 17." not in version:
        return None
    return bin_dir


def _find_configured_postgres() -> pathlib.Path | None:
    configured = os.environ.get(CONFIGURED_BIN_DIR)
    if not configured:
        return None
    resolved = _validate_bin_dir(pathlib.Path(configured).expanduser().resolve())
    if resolved is None:
        raise CommandError(
            f"{CONFIGURED_BIN_DIR} must contain a complete PostgreSQL 17 binary set"
        )
    return resolved


def _find_system_postgres() -> pathlib.Path | None:
    found: list[pathlib.Path] = []
    for name in REQUIRED_BINARIES:
        path = shutil.which(name)
        if path is None:
            return None
        found.append(pathlib.Path(path))
    return _validate_bin_dir(found[0].parent)


def _download_source(destination: pathlib.Path) -> pathlib.Path:
    archive = destination / f"postgresql-{POSTGRES_VERSION}.tar.gz"
    digest = hashlib.sha256()
    request = urllib.request.Request(
        POSTGRES_SOURCE_URL,
        headers={"User-Agent": "StreamScapeTV-ci-workflows-issue-52"},
    )
    try:
        response_context = urllib.request.urlopen(request, timeout=60)
    except urllib.error.URLError as exc:
        raise CommandError(
            "PostgreSQL 17 is not installed and the pinned official source archive "
            f"could not be downloaded; provide {CONFIGURED_BIN_DIR} or network access"
        ) from exc
    with response_context as response, archive.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            output.write(chunk)
    actual = digest.hexdigest()
    if actual != POSTGRES_SOURCE_SHA256:
        raise CommandError(
            f"PostgreSQL source digest mismatch: expected {POSTGRES_SOURCE_SHA256}, got {actual}"
        )
    return archive


def _build_postgres(root: pathlib.Path) -> pathlib.Path:
    root.mkdir(parents=True, exist_ok=True)
    archive = _download_source(root)
    source_root = root / "source"
    source_root.mkdir()
    _safe_extract(archive, source_root)
    sources = [path for path in source_root.iterdir() if path.is_dir()]
    if len(sources) != 1:
        raise CommandError(f"unexpected PostgreSQL archive layout: {sources}")
    source = sources[0]
    install = root / "install"
    env = os.environ.copy()
    env.setdefault("CFLAGS", "-O2")
    _run(
        [
            str(source / "configure"),
            f"--prefix={install}",
            "--with-ssl=openssl",
            "--without-readline",
            "--without-zlib",
            "--without-icu",
            "--without-ldap",
            "--without-libxml",
            "--without-libxslt",
        ],
        cwd=source,
        env=env,
        timeout=180,
    )
    jobs = max(1, min(os.cpu_count() or 2, 4))
    _run(["make", "-s", f"-j{jobs}"], cwd=source, env=env, timeout=480)
    _run(["make", "-s", "install"], cwd=source, env=env, timeout=180)
    extension_env = env.copy()
    extension_env["PATH"] = f"{install / 'bin'}:{extension_env.get('PATH', '')}"
    pgcrypto = source / "contrib" / "pgcrypto"
    _run(["make", "-s", f"-j{jobs}"], cwd=pgcrypto, env=extension_env, timeout=180)
    _run(["make", "-s", "install"], cwd=pgcrypto, env=extension_env, timeout=120)
    return install / "bin"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class SqlResult:
    returncode: int
    stdout: str
    stderr: str


class PostgresRuntime:
    """Explicit, isolated PostgreSQL 17 runtime for canonical reconstruction only."""

    def __init__(self, repository_root: pathlib.Path):
        _require_explicit_reconstruction()
        self.repository_root = repository_root
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="agent-state-pg17-"))
        self.bin_dir: pathlib.Path | None = None
        self.data_dir = self.root / "data"
        self.socket_dir = self.root / "socket"
        self.log_file = self.root / "postgres.log"
        self.port = _free_port()
        self._process_prefix: list[str] = []
        self._started = False

    def __enter__(self) -> "PostgresRuntime":
        try:
            self.bin_dir = (
                _find_configured_postgres()
                or _find_system_postgres()
                or _build_postgres(self.root / "build")
            )
            self._prepare_process_user()
            self._initdb()
            self._start()
            self._bootstrap_roles()
            return self
        except Exception:
            self.close()
            raise

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def bin(self) -> pathlib.Path:
        if self.bin_dir is None:
            raise RuntimeError("PostgreSQL runtime is not initialized")
        return self.bin_dir

    def _prepare_process_user(self) -> None:
        self.data_dir.mkdir()
        self.socket_dir.mkdir()
        self.log_file.touch()
        if os.geteuid() != 0:
            return
        runuser = shutil.which("runuser")
        if runuser is None:
            raise CommandError("root execution requires runuser for PostgreSQL")
        self._process_prefix = [runuser, "-u", "nobody", "--"]
        for path in (self.root, self.data_dir, self.socket_dir, self.log_file):
            os.chown(path, 65534, 65534)
        os.chmod(self.root, 0o755)
        os.chmod(self.data_dir, 0o700)
        os.chmod(self.socket_dir, 0o700)

    def _server_command(self, command: list[str]) -> list[str]:
        return [*self._process_prefix, *command]

    def _initdb(self) -> None:
        _run(
            self._server_command(
                [
                    str(self.bin / "initdb"),
                    "--pgdata",
                    str(self.data_dir),
                    "--username=postgres",
                    "--auth=trust",
                    "--no-locale",
                    "--encoding=UTF8",
                ]
            ),
            timeout=120,
        )

    def _start(self) -> None:
        options = (
            f"-F -p {self.port} -k {self.socket_dir} "
            "-c listen_addresses='' -c max_connections=40 "
            "-c log_min_messages=warning"
        )
        _run(
            self._server_command(
                [
                    str(self.bin / "pg_ctl"),
                    "--pgdata",
                    str(self.data_dir),
                    "--log",
                    str(self.log_file),
                    "--options",
                    options,
                    "--wait",
                    "start",
                ]
            ),
            timeout=120,
        )
        self._started = True

    def _bootstrap_roles(self) -> None:
        bootstrap = (
            self.repository_root
            / "tests"
            / "fixtures"
            / "supabase-agent-state"
            / "bootstrap.sql"
        )
        self.psql_file("postgres", bootstrap)

    def connection_args(self, database: str) -> list[str]:
        return [
            "--host",
            str(self.socket_dir),
            "--port",
            str(self.port),
            "--username",
            "postgres",
            "--dbname",
            database,
        ]

    def psql(
        self,
        database: str,
        sql: str,
        *,
        role: str | None = None,
        check: bool = True,
        timeout: int = 120,
    ) -> SqlResult:
        if role is not None:
            sql = f"set role {role};\n{sql}"
        result = _run(
            [
                str(self.bin / "psql"),
                *self.connection_args(database),
                "--no-psqlrc",
                "--quiet",
                "--tuples-only",
                "--no-align",
                "--set=ON_ERROR_STOP=1",
            ],
            input_text=sql,
            check=check,
            timeout=timeout,
        )
        return SqlResult(result.returncode, result.stdout.strip(), result.stderr.strip())

    def psql_file(self, database: str, path: pathlib.Path) -> None:
        _run(
            [
                str(self.bin / "psql"),
                *self.connection_args(database),
                "--no-psqlrc",
                "--set=ON_ERROR_STOP=1",
                "--file",
                str(path),
            ],
            timeout=180,
        )

    def create_database(self, database: str) -> None:
        _run(
            [str(self.bin / "createdb"), *self.connection_args("postgres")[:-2], database],
            timeout=60,
        )

    def drop_database(self, database: str) -> None:
        _run(
            [
                str(self.bin / "dropdb"),
                *self.connection_args("postgres")[:-2],
                "--if-exists",
                "--force",
                database,
            ],
            timeout=60,
        )

    def apply_migrations(self, database: str) -> list[pathlib.Path]:
        migrations = sorted((self.repository_root / "supabase" / "migrations").glob("*.sql"))
        if not migrations:
            raise CommandError("no migrations found")
        for migration in migrations:
            self.psql_file(database, migration)
        return migrations

    def schema_dump(self, database: str) -> str:
        result = _run(
            [
                str(self.bin / "pg_dump"),
                *self.connection_args(database),
                "--schema-only",
                "--no-owner",
            ],
            timeout=120,
        )
        normalized: list[str] = []
        for line in result.stdout.splitlines():
            if (
                line.startswith("--")
                or line.startswith("\\restrict")
                or line.startswith("\\unrestrict")
            ):
                continue
            normalized.append(line.rstrip())
        return "\n".join(normalized).strip() + "\n"

    def popen_psql(
        self,
        database: str,
        sql: str,
        *,
        role: str | None = None,
    ) -> subprocess.Popen[str]:
        if role is not None:
            sql = f"set role {role};\n{sql}"
        return subprocess.Popen(
            [
                str(self.bin / "psql"),
                *self.connection_args(database),
                "--no-psqlrc",
                "--quiet",
                "--tuples-only",
                "--no-align",
                "--set=ON_ERROR_STOP=1",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def close(self) -> None:
        if self._started and self.bin_dir is not None:
            with contextlib.suppress(Exception):
                _run(
                    self._server_command(
                        [
                            str(self.bin / "pg_ctl"),
                            "--pgdata",
                            str(self.data_dir),
                            "--wait",
                            "--mode=immediate",
                            "stop",
                        ]
                    ),
                    check=False,
                    timeout=60,
                )
            self._started = False
        shutil.rmtree(self.root, ignore_errors=True)
