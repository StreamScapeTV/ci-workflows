"""Product-neutral package and SDK build/inspection/publication primitives."""
from __future__ import annotations

import email.parser
import json
import os
import re
import stat
import tarfile
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Protocol, Sequence

from .runtime_primitives import (
    ProcessResult,
    RuntimePrimitiveError,
    create_temporary_workspace,
    finalize_temporary_paths,
    run_process,
)

_ECOSYSTEMS = frozenset({"python", "npm", "jvm"})
_PACKAGE_NAME = re.compile(r"^[A-Za-z0-9@][A-Za-z0-9@._~:/+-]{0,126}$")
_JVM_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,126}[A-Za-z0-9])?$")
_JVM_GROUP = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,254}[A-Za-z0-9])?$")
_PACKAGE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!~-]{0,126}$")
_PYTHON_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$")
_NPM_NAME = re.compile(r"^(?:@[a-z0-9][a-z0-9._~-]{0,63}/)?[a-z0-9][a-z0-9._~-]{0,126}$")
_GRADLE_TASK = re.compile(r"^:?[-A-Za-z0-9_.]+(?::[-A-Za-z0-9_.]+)*$")
_MAVEN_GOAL = re.compile(r"^[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.-]+){0,3}$")
_SECRET_OPTION = re.compile(r"(?i)(password|passwd|token|secret|credential|auth)")
_CREDENTIAL_NAMES = ("CI_PACKAGE_USERNAME", "CI_PACKAGE_PASSWORD", "CI_PACKAGE_TOKEN")
_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
_MAX_METADATA_BYTES = 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 20_000
_MAX_OUTPUT_FILES = 5_000


class PackagePrimitiveError(RuntimeError):
    """Stable non-secret package primitive failure."""

    def __init__(self, code: str, operation: str, *, returncode: int | None = None) -> None:
        self.code = code
        self.operation = operation
        self.returncode = returncode
        text = f"{code}: {operation}"
        if returncode is not None:
            text += f" exited with status {returncode}"
        super().__init__(text)


@dataclass(frozen=True, slots=True)
class PackageIdentity:
    ecosystem: Literal["python", "npm", "jvm"]
    name: str
    version: str


@dataclass(frozen=True, slots=True)
class PackageArtifact:
    ecosystem: Literal["python", "npm", "jvm"]
    kind: str
    path: str
    size: int
    name: str | None = None
    version: str | None = None
    group: str | None = None


@dataclass(frozen=True, slots=True)
class PackageBuildResult:
    operation: str
    process: ProcessResult
    artifacts: tuple[PackageArtifact, ...]


@dataclass(frozen=True, slots=True)
class PublicationResult:
    ecosystem: Literal["python", "npm", "jvm"]
    registry: str | None
    name: str
    version: str
    process: ProcessResult
    auth_paths_removed: int


class ProcessRunner(Protocol):
    def __call__(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        stdin: str = "",
        timeout_seconds: float | None = None,
    ) -> ProcessResult: ...


def _fail(code: str, operation: str, *, returncode: int | None = None) -> None:
    raise PackagePrimitiveError(code, operation, returncode=returncode)


def _single_line(value: object, operation: str, *, maximum: int = 2048) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(character in value for character in ("\x00", "\r", "\n"))
    ):
        _fail("value_invalid", operation)
    return value


def validate_package_identity(
    ecosystem: Literal["python", "npm", "jvm"],
    name: str,
    version: str,
) -> PackageIdentity:
    """Validate a product-neutral package identity."""

    if ecosystem not in _ECOSYSTEMS:
        _fail("ecosystem_invalid", "package.identity")
    name = _single_line(name, "package.identity.name", maximum=128)
    version = _single_line(version, "package.identity.version", maximum=127)
    if ecosystem == "python":
        if _PYTHON_NAME.fullmatch(name) is None:
            _fail("package_name_invalid", "package.identity")
    elif ecosystem == "npm":
        if _NPM_NAME.fullmatch(name) is None:
            _fail("package_name_invalid", "package.identity")
    elif _JVM_NAME.fullmatch(name) is None:
        _fail("package_name_invalid", "package.identity")
    if _PACKAGE_VERSION.fullmatch(version) is None:
        _fail("package_version_invalid", "package.identity")
    return PackageIdentity(ecosystem, name, version)


def _directory(path: Path, operation: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.is_symlink():
        _fail("directory_invalid", operation)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise PackagePrimitiveError("directory_invalid", operation) from error
    if not resolved.is_dir():
        _fail("directory_invalid", operation)
    return resolved


def _bounded_target(root: Path, requested: Path, operation: str) -> Path:
    root = _directory(root, operation)
    candidate = Path(requested)
    if ".." in candidate.parts or "\\" in os.fspath(candidate):
        _fail("path_invalid", operation)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = Path(os.path.normpath(os.fspath(candidate)))
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        _fail("path_invalid", operation)
    if not relative.parts:
        _fail("path_invalid", operation)
    cursor = root
    for part in relative.parts[:-1]:
        cursor = cursor / part
        if cursor.is_symlink():
            _fail("path_invalid", operation)
    return candidate


def _bounded_file(root: Path, requested: Path, operation: str) -> Path:
    candidate = _bounded_target(root, requested, operation)
    if candidate.is_symlink():
        _fail("file_invalid", operation)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise PackagePrimitiveError("file_invalid", operation) from error
    if not resolved.is_file():
        _fail("file_invalid", operation)
    try:
        resolved.relative_to(_directory(root, operation))
    except ValueError:
        _fail("file_invalid", operation)
    return resolved


def _prepare_output_directory(project: Path, requested: Path, operation: str) -> Path:
    root = _directory(project, operation)
    target = _bounded_target(root, requested, operation)
    if target.is_symlink():
        _fail("package_output_invalid", operation)
    if target.exists():
        if not target.is_dir():
            _fail("package_output_invalid", operation)
        if any(target.iterdir()):
            _fail("package_output_not_empty", operation)
    else:
        try:
            target.mkdir(mode=0o700, parents=True)
        except OSError as error:
            raise PackagePrimitiveError("package_output_create_failed", operation) from error
    return target.resolve(strict=True)


def _existing_output_directory(project: Path, requested: Path, operation: str) -> Path:
    root = _directory(project, operation)
    target = _bounded_target(root, requested, operation)
    if target.is_symlink():
        _fail("package_output_invalid", operation)
    try:
        target = target.resolve(strict=True)
    except OSError as error:
        raise PackagePrimitiveError("package_output_missing", operation) from error
    if not target.is_dir():
        _fail("package_output_invalid", operation)
    return target


def _tool(path: Path, operation: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.is_symlink():
        _fail("tool_unavailable", operation)
    try:
        candidate = candidate.resolve(strict=True)
    except OSError as error:
        raise PackagePrimitiveError("tool_unavailable", operation) from error
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        _fail("tool_unavailable", operation)
    return candidate


def _environment(environment: Mapping[str, str] | None) -> dict[str, str]:
    source = os.environ if environment is None else environment
    result: dict[str, str] = {}
    for name, value in source.items():
        if (
            not isinstance(name, str)
            or not name
            or "=" in name
            or any(character in name for character in ("\x00", "\r", "\n"))
            or not isinstance(value, str)
            or "\x00" in value
        ):
            _fail("environment_invalid", "package.environment")
        result[name] = value
    return result


def _credentials(environment: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in _CREDENTIAL_NAMES:
        value = environment.get(name, "")
        if not isinstance(value, str) or any(character in value for character in ("\x00", "\r", "\n")):
            _fail("credential_invalid", "package.credentials")
        if value:
            result[name] = value
    return result


def _redact(text: str, secret_values: Sequence[str]) -> str:
    result = text
    for secret in sorted({value for value in secret_values if value}, key=len, reverse=True):
        result = result.replace(secret, "***")
    return result


def _execute(
    operation: str,
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    runner: ProcessRunner | None,
    stdin: str = "",
    timeout_seconds: float | None = None,
    secret_values: Sequence[str] = (),
) -> ProcessResult:
    if not arguments or any(
        not isinstance(value, str)
        or not value
        or any(character in value for character in ("\x00", "\r", "\n"))
        for value in arguments
    ):
        _fail("arguments_invalid", operation)
    try:
        result = (runner or run_process)(
            tuple(arguments),
            cwd=_directory(cwd, operation),
            environment=_environment(environment),
            stdin=stdin,
            timeout_seconds=timeout_seconds,
        )
    except RuntimePrimitiveError as error:
        raise PackagePrimitiveError("runtime_failed", operation) from error
    except OSError as error:
        raise PackagePrimitiveError("command_unavailable", operation) from error
    if not isinstance(result, ProcessResult):
        _fail("runner_result_invalid", operation)
    redacted = ProcessResult(
        result.returncode,
        _redact(result.stdout, secret_values),
        _redact(result.stderr, secret_values),
        result.timed_out,
    )
    if result.timed_out:
        _fail("command_timeout", operation)
    if result.returncode != 0:
        _fail("command_failed", operation, returncode=result.returncode)
    return redacted


def _registry_url(value: str, operation: str) -> str:
    value = _single_line(value, operation, maximum=2048).rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise PackagePrimitiveError("registry_url_invalid", operation) from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
        or ".." in urllib.parse.unquote(parsed.path).split("/")
    ):
        _fail("registry_url_invalid", operation)
    return value


def _archive_size(path: Path, operation: str) -> int:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise PackagePrimitiveError("package_artifact_unavailable", operation) from error
    if size <= 0 or size > _MAX_ARCHIVE_BYTES:
        _fail("package_artifact_size_invalid", operation)
    return size


def _safe_archive_member(name: str, operation: str) -> None:
    if (
        not isinstance(name, str)
        or not name
        or name.startswith("/")
        or "\\" in name
        or any(part == ".." for part in name.split("/"))
    ):
        _fail("package_archive_member_invalid", operation)


def _metadata_identity(text: str, ecosystem: Literal["python", "npm"], operation: str) -> PackageIdentity:
    try:
        message = email.parser.Parser().parsestr(text)
    except (TypeError, ValueError) as error:
        raise PackagePrimitiveError("package_metadata_invalid", operation) from error
    names = message.get_all("Name", [])
    versions = message.get_all("Version", [])
    if len(names) != 1 or len(versions) != 1:
        _fail("package_metadata_invalid", operation)
    return validate_package_identity(ecosystem, names[0].strip(), versions[0].strip())


def _read_wheel_identity(path: Path) -> PackageIdentity:
    operation = "python.inspect.wheel"
    _archive_size(path, operation)
    try:
        with zipfile.ZipFile(path, "r") as archive:
            members = archive.infolist()
            if len(members) > _MAX_ARCHIVE_MEMBERS:
                _fail("package_archive_too_large", operation)
            matches = []
            for member in members:
                _safe_archive_member(member.filename, operation)
                if member.filename.endswith(".dist-info/METADATA") and not member.is_dir():
                    matches.append(member)
            if len(matches) != 1 or matches[0].file_size > _MAX_METADATA_BYTES:
                _fail("package_metadata_invalid", operation)
            raw = archive.read(matches[0])
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise PackagePrimitiveError("package_archive_invalid", operation) from error
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PackagePrimitiveError("package_metadata_invalid", operation) from error
    return _metadata_identity(text, "python", operation)


def _read_sdist_identity(path: Path) -> PackageIdentity:
    operation = "python.inspect.sdist"
    _archive_size(path, operation)
    try:
        with tarfile.open(path, "r:*") as archive:
            members = archive.getmembers()
            if len(members) > _MAX_ARCHIVE_MEMBERS:
                _fail("package_archive_too_large", operation)
            matches = []
            for member in members:
                _safe_archive_member(member.name, operation)
                if (member.name.endswith("/PKG-INFO") or member.name == "PKG-INFO") and member.isfile():
                    matches.append(member)
            if len(matches) != 1 or matches[0].size > _MAX_METADATA_BYTES:
                _fail("package_metadata_invalid", operation)
            handle = archive.extractfile(matches[0])
            if handle is None:
                _fail("package_metadata_invalid", operation)
            raw = handle.read(_MAX_METADATA_BYTES + 1)
    except (OSError, tarfile.TarError) as error:
        raise PackagePrimitiveError("package_archive_invalid", operation) from error
    if len(raw) > _MAX_METADATA_BYTES:
        _fail("package_metadata_invalid", operation)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PackagePrimitiveError("package_metadata_invalid", operation) from error
    return _metadata_identity(text, "python", operation)


def inspect_python_packages(
    project_directory: Path,
    output_directory: Path,
    *,
    expected_name: str | None = None,
    expected_version: str | None = None,
) -> tuple[PackageArtifact, ...]:
    """Inspect wheel and sdist metadata without extracting either archive."""

    operation = "python.inspect"
    output = _existing_output_directory(project_directory, output_directory, operation)
    files = sorted(path for path in output.iterdir() if path.is_file() and not path.is_symlink())
    wheels = [path for path in files if path.suffix == ".whl"]
    sdists = [path for path in files if path.name.endswith(".tar.gz")]
    if not wheels or not sdists:
        _fail("python_package_outputs_missing", operation)
    expected = (
        validate_package_identity("python", expected_name, expected_version)
        if expected_name is not None and expected_version is not None
        else None
    )
    if (expected_name is None) != (expected_version is None):
        _fail("package_identity_incomplete", operation)
    artifacts: list[PackageArtifact] = []
    identities: set[tuple[str, str]] = set()
    for kind, paths, reader in (
        ("wheel", wheels, _read_wheel_identity),
        ("sdist", sdists, _read_sdist_identity),
    ):
        for path in paths:
            identity = reader(path)
            identities.add((identity.name, identity.version))
            if expected is not None and (identity.name, identity.version) != (expected.name, expected.version):
                _fail("package_identity_mismatch", operation)
            artifacts.append(
                PackageArtifact(
                    "python",
                    kind,
                    path.name,
                    _archive_size(path, operation),
                    identity.name,
                    identity.version,
                )
            )
    if len(identities) != 1:
        _fail("package_identity_mismatch", operation)
    return tuple(artifacts)


def build_python_packages(
    interpreter: Path,
    *,
    project_directory: Path,
    output_directory: Path,
    expected_name: str | None = None,
    expected_version: str | None = None,
    environment: Mapping[str, str] | None = None,
    runner: ProcessRunner | None = None,
    timeout_seconds: float | None = None,
) -> PackageBuildResult:
    """Build a wheel and sdist, then inspect their embedded package metadata."""

    operation = "python.build"
    python = _tool(interpreter, operation)
    root = _directory(project_directory, operation)
    output = _prepare_output_directory(root, output_directory, operation)
    result = _execute(
        operation,
        (str(python), "-m", "build", "--wheel", "--sdist", "--outdir", str(output)),
        cwd=root,
        environment=_environment(environment),
        runner=runner,
        timeout_seconds=timeout_seconds,
    )
    artifacts = inspect_python_packages(
        root,
        output,
        expected_name=expected_name,
        expected_version=expected_version,
    )
    return PackageBuildResult(operation, result, artifacts)


def publish_python_packages(
    interpreter: Path,
    artifacts: Sequence[Path],
    *,
    project_directory: Path,
    registry_url: str,
    package_name: str,
    package_version: str,
    environment: Mapping[str, str],
    runner: ProcessRunner | None = None,
    timeout_seconds: float | None = None,
) -> PublicationResult:
    """Publish caller-selected Python artifacts using fixed named credentials."""

    operation = "python.publish"
    identity = validate_package_identity("python", package_name, package_version)
    python = _tool(interpreter, operation)
    root = _directory(project_directory, operation)
    registry = _registry_url(registry_url, operation)
    if not artifacts:
        _fail("package_artifacts_required", operation)
    files = tuple(_bounded_file(root, path, operation) for path in artifacts)
    for path in files:
        if path.suffix == ".whl":
            actual = _read_wheel_identity(path)
        elif path.name.endswith(".tar.gz"):
            actual = _read_sdist_identity(path)
        else:
            _fail("python_package_artifact_invalid", operation)
        if (actual.name, actual.version) != (identity.name, identity.version):
            _fail("package_identity_mismatch", operation)
    source = _environment(environment)
    credentials = _credentials(source)
    token = credentials.get("CI_PACKAGE_TOKEN", "")
    username = credentials.get("CI_PACKAGE_USERNAME", "")
    password = credentials.get("CI_PACKAGE_PASSWORD", "")
    if token:
        username, password = "__token__", token
    elif not username or not password:
        _fail("package_credentials_required", operation)
    child = dict(source)
    for name in _CREDENTIAL_NAMES:
        child.pop(name, None)
    child.pop("TWINE_USERNAME", None)
    child.pop("TWINE_PASSWORD", None)
    child["TWINE_USERNAME"] = username
    child["TWINE_PASSWORD"] = password
    child["TWINE_NON_INTERACTIVE"] = "1"
    secrets = tuple(value for value in credentials.values() if value)
    result = _execute(
        operation,
        (
            str(python),
            "-m",
            "twine",
            "upload",
            "--non-interactive",
            "--repository-url",
            registry,
            *(str(path) for path in files),
        ),
        cwd=root,
        environment=child,
        runner=runner,
        timeout_seconds=timeout_seconds,
        secret_values=secrets,
    )
    return PublicationResult("python", registry, identity.name, identity.version, result, 0)


def _read_npm_identity(path: Path) -> PackageIdentity:
    operation = "npm.inspect"
    _archive_size(path, operation)
    try:
        with tarfile.open(path, "r:*") as archive:
            members = archive.getmembers()
            if len(members) > _MAX_ARCHIVE_MEMBERS:
                _fail("package_archive_too_large", operation)
            matches = []
            for member in members:
                _safe_archive_member(member.name, operation)
                if member.name == "package/package.json" and member.isfile():
                    matches.append(member)
            if len(matches) != 1 or matches[0].size > _MAX_METADATA_BYTES:
                _fail("package_metadata_invalid", operation)
            handle = archive.extractfile(matches[0])
            if handle is None:
                _fail("package_metadata_invalid", operation)
            raw = handle.read(_MAX_METADATA_BYTES + 1)
    except (OSError, tarfile.TarError) as error:
        raise PackagePrimitiveError("package_archive_invalid", operation) from error
    if len(raw) > _MAX_METADATA_BYTES:
        _fail("package_metadata_invalid", operation)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PackagePrimitiveError("package_metadata_invalid", operation) from error
    if not isinstance(payload, Mapping):
        _fail("package_metadata_invalid", operation)
    return validate_package_identity("npm", payload.get("name"), payload.get("version"))


def inspect_npm_package(
    project_directory: Path,
    artifact: Path,
    *,
    expected_name: str | None = None,
    expected_version: str | None = None,
) -> PackageArtifact:
    """Inspect an npm package tarball without extracting it."""

    operation = "npm.inspect"
    root = _directory(project_directory, operation)
    path = _bounded_file(root, artifact, operation)
    identity = _read_npm_identity(path)
    if (expected_name is None) != (expected_version is None):
        _fail("package_identity_incomplete", operation)
    if expected_name is not None and expected_version is not None:
        expected = validate_package_identity("npm", expected_name, expected_version)
        if (identity.name, identity.version) != (expected.name, expected.version):
            _fail("package_identity_mismatch", operation)
    return PackageArtifact(
        "npm",
        "tarball",
        str(path.relative_to(root)),
        _archive_size(path, operation),
        identity.name,
        identity.version,
    )


def npm_pack(
    npm_executable: Path,
    *,
    project_directory: Path,
    output_directory: Path,
    expected_name: str | None = None,
    expected_version: str | None = None,
    environment: Mapping[str, str] | None = None,
    runner: ProcessRunner | None = None,
    timeout_seconds: float | None = None,
) -> PackageBuildResult:
    """Create one npm tarball with ``npm pack --json`` and inspect package.json."""

    operation = "npm.pack"
    npm = _tool(npm_executable, operation)
    root = _directory(project_directory, operation)
    output = _prepare_output_directory(root, output_directory, operation)
    result = _execute(
        operation,
        (str(npm), "pack", "--json", "--pack-destination", str(output)),
        cwd=root,
        environment=_environment(environment),
        runner=runner,
        timeout_seconds=timeout_seconds,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise PackagePrimitiveError("npm_pack_output_invalid", operation) from error
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], Mapping):
        _fail("npm_pack_output_invalid", operation)
    filename = payload[0].get("filename")
    if not isinstance(filename, str) or Path(filename).name != filename or "/" in filename or "\\" in filename:
        _fail("npm_pack_output_invalid", operation)
    artifact = inspect_npm_package(
        root,
        output / filename,
        expected_name=expected_name,
        expected_version=expected_version,
    )
    json_name = payload[0].get("name")
    json_version = payload[0].get("version")
    if json_name is not None and json_version is not None:
        packed = validate_package_identity("npm", json_name, json_version)
        if (packed.name, packed.version) != (artifact.name, artifact.version):
            _fail("package_identity_mismatch", operation)
    return PackageBuildResult(operation, result, (artifact,))


def _npm_auth_line(registry: str, token: str) -> str:
    parsed = urllib.parse.urlsplit(registry)
    path = parsed.path.rstrip("/")
    prefix = f"//{parsed.netloc}{path}/" if path else f"//{parsed.netloc}/"
    return f"{prefix}:_authToken={token}\n"


def npm_publish(
    npm_executable: Path,
    artifact: Path,
    *,
    project_directory: Path,
    temporary_root: Path,
    registry_url: str,
    package_name: str,
    package_version: str,
    environment: Mapping[str, str],
    runner: ProcessRunner | None = None,
    timeout_seconds: float | None = None,
) -> PublicationResult:
    """Publish an npm tarball using a temporary mode-0600 userconfig."""

    operation = "npm.publish"
    identity = validate_package_identity("npm", package_name, package_version)
    npm = _tool(npm_executable, operation)
    root = _directory(project_directory, operation)
    artifact_path = _bounded_file(root, artifact, operation)
    actual = _read_npm_identity(artifact_path)
    if (actual.name, actual.version) != (identity.name, identity.version):
        _fail("package_identity_mismatch", operation)
    registry = _registry_url(registry_url, operation)
    temp = _directory(temporary_root, operation)
    source = _environment(environment)
    credentials = _credentials(source)
    token = credentials.get("CI_PACKAGE_TOKEN", "")
    if not token:
        _fail("package_token_required", operation)
    child = dict(source)
    for name in _CREDENTIAL_NAMES:
        child.pop(name, None)
    child.pop("NPM_TOKEN", None)
    auth_root: Path | None = None
    removed = 0
    try:
        try:
            auth_root = create_temporary_workspace(temp, prefix="npm-auth")
        except RuntimePrimitiveError as error:
            raise PackagePrimitiveError("package_auth_state_failed", operation) from error
        userconfig = auth_root / "npmrc"
        userconfig.write_text(_npm_auth_line(registry, token), encoding="utf-8")
        userconfig.chmod(0o600)
        result = _execute(
            operation,
            (
                str(npm),
                "publish",
                str(artifact_path),
                "--registry",
                registry,
                "--userconfig",
                str(userconfig),
            ),
            cwd=root,
            environment=child,
            runner=runner,
            timeout_seconds=timeout_seconds,
            secret_values=(token,),
        )
    except OSError as error:
        raise PackagePrimitiveError("package_auth_state_failed", operation) from error
    finally:
        if auth_root is not None:
            try:
                removed = finalize_temporary_paths((auth_root,), root=temp)
            except RuntimePrimitiveError as error:
                raise PackagePrimitiveError("package_auth_cleanup_failed", operation) from error
    return PublicationResult("npm", registry, identity.name, identity.version, result, removed)


def _non_secret_arguments(values: Sequence[str], operation: str) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        value = _single_line(value, operation, maximum=1024)
        if _SECRET_OPTION.search(value):
            _fail("secret_option_forbidden", operation)
        result.append(value)
    return tuple(result)


def _jvm_action(tool: Literal["gradle", "maven"], value: str, operation: str) -> str:
    value = _single_line(value, operation, maximum=256)
    pattern = _GRADLE_TASK if tool == "gradle" else _MAVEN_GOAL
    if value.startswith("-") or pattern.fullmatch(value) is None:
        _fail("publication_action_invalid", operation)
    return value


def _pom_child(root: ET.Element, name: str) -> str | None:
    for child in root:
        if child.tag.rsplit("}", 1)[-1] == name:
            return (child.text or "").strip() or None
    return None


def _pom_identity(path: Path) -> tuple[str | None, str, str]:
    operation = "jvm.inspect.pom"
    size = _archive_size(path, operation)
    if size > _MAX_METADATA_BYTES:
        _fail("package_metadata_invalid", operation)
    try:
        raw = path.read_bytes()
        root = ET.fromstring(raw)
    except (OSError, ET.ParseError) as error:
        raise PackagePrimitiveError("package_metadata_invalid", operation) from error
    artifact_id = _pom_child(root, "artifactId")
    group_id = _pom_child(root, "groupId")
    version = _pom_child(root, "version")
    parent = next((child for child in root if child.tag.rsplit("}", 1)[-1] == "parent"), None)
    if parent is not None:
        group_id = group_id or _pom_child(parent, "groupId")
        version = version or _pom_child(parent, "version")
    if artifact_id is None or version is None:
        _fail("package_metadata_invalid", operation)
    validate_package_identity("jvm", artifact_id, version)
    if group_id is not None:
        _single_line(group_id, operation, maximum=256)
        if _JVM_GROUP.fullmatch(group_id) is None:
            _fail("package_metadata_invalid", operation)
    return group_id, artifact_id, version


def inspect_jvm_outputs(
    project_directory: Path,
    output_directory: Path,
    *,
    expected_name: str,
    expected_version: str,
    expected_group: str | None = None,
) -> tuple[PackageArtifact, ...]:
    """Inventory bounded AAR/JAR/POM outputs and verify one matching POM."""

    operation = "jvm.inspect"
    expected = validate_package_identity("jvm", expected_name, expected_version)
    group = None
    if expected_group is not None:
        group = _single_line(expected_group, operation, maximum=256)
        if _JVM_GROUP.fullmatch(group) is None:
            _fail("package_group_invalid", operation)
    root = _directory(project_directory, operation)
    output = _existing_output_directory(root, output_directory, operation)
    files = sorted(path for path in output.rglob("*") if path.is_file() or path.is_symlink())
    if len(files) > _MAX_OUTPUT_FILES:
        _fail("package_outputs_too_many", operation)
    artifacts: list[PackageArtifact] = []
    matched_pom = False
    for candidate in files:
        path = _bounded_file(root, candidate, operation)
        suffix = path.suffix.lower()
        if suffix not in {".pom", ".aar", ".jar"}:
            continue
        relative = str(path.relative_to(root))
        if suffix == ".pom":
            pom_group, pom_name, pom_version = _pom_identity(path)
            if (pom_name, pom_version) == (expected.name, expected.version) and (
                group is None or pom_group == group
            ):
                matched_pom = True
            artifacts.append(
                PackageArtifact("jvm", "pom", relative, _archive_size(path, operation), pom_name, pom_version, pom_group)
            )
        else:
            artifacts.append(
                PackageArtifact("jvm", suffix.removeprefix("."), relative, _archive_size(path, operation))
            )
    if not artifacts:
        _fail("jvm_package_outputs_missing", operation)
    if not matched_pom:
        _fail("package_identity_mismatch", operation)
    return tuple(artifacts)


def run_jvm_publication_tasks(
    tool: Literal["gradle", "maven"],
    executable: Path,
    actions: Sequence[str],
    *,
    project_directory: Path,
    output_directory: Path,
    package_name: str,
    package_version: str,
    package_group: str | None = None,
    options: Sequence[str] = (),
    environment: Mapping[str, str] | None = None,
    runner: ProcessRunner | None = None,
    timeout_seconds: float | None = None,
) -> PackageBuildResult:
    """Run caller-selected Gradle/Maven publication tasks and inspect outputs."""

    operation = f"jvm.{tool}.publish"
    if tool not in {"gradle", "maven"}:
        _fail("publication_tool_invalid", "jvm.publish")
    identity = validate_package_identity("jvm", package_name, package_version)
    command = _tool(executable, operation)
    root = _directory(project_directory, operation)
    target = _prepare_output_directory(root, output_directory, operation)
    if not actions:
        _fail("publication_actions_required", operation)
    safe_actions = tuple(_jvm_action(tool, action, operation) for action in actions)
    safe_options = _non_secret_arguments(options, operation)
    source = _environment(environment)
    credentials = _credentials(source)
    secrets = tuple(credentials.values())
    arguments = (
        (str(command), *safe_actions, *safe_options)
        if tool == "gradle"
        else (str(command), *safe_options, *safe_actions)
    )
    result = _execute(
        operation,
        arguments,
        cwd=root,
        environment=source,
        runner=runner,
        timeout_seconds=timeout_seconds,
        secret_values=secrets,
    )
    artifacts = inspect_jvm_outputs(
        root,
        target,
        expected_name=identity.name,
        expected_version=identity.version,
        expected_group=package_group,
    )
    return PackageBuildResult(operation, result, artifacts)
