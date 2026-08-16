"""Product-neutral mobile signing and distribution primitives."""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Protocol, Sequence

from .runtime_primitives import ProcessResult, RuntimePrimitiveError, run_process

APPLE_CERTIFICATE_B64_ENV = "CIW_APPLE_CERTIFICATE_P12_B64"
APPLE_CERTIFICATE_PASSWORD_ENV = "CIW_APPLE_CERTIFICATE_PASSWORD"
APPLE_KEYCHAIN_PASSWORD_ENV = "CIW_APPLE_KEYCHAIN_PASSWORD"
APPLE_PROFILE_B64_ENV = "CIW_APPLE_PROVISIONING_PROFILE_B64"
APP_STORE_KEY_ID_ENV = "CIW_APP_STORE_CONNECT_KEY_ID"
APP_STORE_ISSUER_ID_ENV = "CIW_APP_STORE_CONNECT_ISSUER_ID"
APP_STORE_PRIVATE_KEY_B64_ENV = "CIW_APP_STORE_CONNECT_PRIVATE_KEY_B64"
ANDROID_KEYSTORE_B64_ENV = "CIW_ANDROID_KEYSTORE_B64"
ANDROID_STORE_PASSWORD_ENV = "CIW_ANDROID_KEYSTORE_PASSWORD"
ANDROID_KEY_PASSWORD_ENV = "CIW_ANDROID_KEY_PASSWORD"
GOOGLE_PLAY_SERVICE_ACCOUNT_JSON_ENV = "CIW_GOOGLE_PLAY_SERVICE_ACCOUNT_JSON"

_ERROR = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. +()=,:/@-]{0,511}$")
_PACKAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$")
_TRACK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MARKER = ".ciw-distribution-state"


class DistributionPrimitiveError(RuntimeError):
    def __init__(self, code: str, operation: str = "") -> None:
        if _ERROR.fullmatch(code) is None:
            raise ValueError("invalid distribution error code")
        self.code, self.operation = code, operation
        super().__init__(f"{code}: {operation}" if operation else code)


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


class CommandRunner(Protocol):
    def run(self, argv: Sequence[str], *, cwd: Path, env: Mapping[str, str]) -> ProcessResult | CommandOutcome: ...


class RuntimeCommandRunner:
    def run(self, argv: Sequence[str], *, cwd: Path, env: Mapping[str, str]) -> ProcessResult:
        return run_process(argv, cwd=cwd, environment=env)


@dataclass(frozen=True, slots=True)
class DistributionState:
    root: Path


@dataclass(frozen=True, slots=True)
class CredentialFile:
    kind: str
    path: Path


@dataclass(frozen=True, slots=True)
class AppleCredentials:
    certificate: CredentialFile
    profile: CredentialFile | None


@dataclass(frozen=True, slots=True)
class AppleKeychain:
    path: Path


@dataclass(frozen=True, slots=True)
class PackageResult:
    platform: str
    operation: str
    package: Path
    signed: bool
    verified: bool


@dataclass(frozen=True, slots=True)
class StoreAuth:
    provider: Literal["app-store-connect", "google-play"]
    credential: CredentialFile
    key_id: str = ""
    issuer_id: str = ""


@dataclass(frozen=True, slots=True)
class UploadRequest:
    provider: Literal["app-store-connect", "google-play"]
    package: Path
    argv: tuple[str, ...]
    cwd: Path
    environment_overrides: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class UploadResult:
    provider: str
    package: Path
    uploaded: bool


def _fail(code: str, operation: str = "") -> None:
    raise DistributionPrimitiveError(code, operation)


def _line(value: str, code: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or value != value.strip() or any(c in value for c in "\x00\r\n"):
        _fail(code)
    return value


def _safe(value: str, code: str) -> str:
    value = _line(value, code, maximum=512)
    if _SAFE.fullmatch(value) is None:
        _fail(code)
    return value


def _env(environment: Mapping[str, str] | None) -> dict[str, str]:
    source = os.environ if environment is None else environment
    result: dict[str, str] = {}
    for name, value in source.items():
        if not isinstance(name, str) or not isinstance(value, str) or not name or any(c in name for c in "\x00\r\n=") or "\x00" in value:
            _fail("environment_invalid")
        result[name] = value
    return result


def _secret(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "")
    if not isinstance(value, str) or not value or "\x00" in value:
        _fail("secret_missing")
    return value


def _path(path: Path, *, directory: bool | None = None, code: str = "path_invalid") -> Path:
    candidate = Path(path)
    if candidate.is_symlink():
        _fail(code)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise DistributionPrimitiveError(code) from error
    if directory is True and not resolved.is_dir(): _fail(code)
    if directory is False and not resolved.is_file(): _fail(code)
    if directory is None and not (resolved.is_file() or resolved.is_dir()): _fail(code)
    return resolved


def _tool(path: Path, operation: str) -> Path:
    resolved = _path(path, directory=False, code="tool_unavailable")
    if not os.access(resolved, os.X_OK): _fail("tool_unavailable", operation)
    return resolved


def create_distribution_state(parent: Path) -> DistributionState:
    root = _path(parent, directory=True, code="state_parent_invalid")
    try:
        created = Path(tempfile.mkdtemp(prefix="ciw-distribution-", dir=root))
        created.chmod(0o700)
        marker = created / _MARKER
        marker.write_text("1\n", encoding="utf-8")
        marker.chmod(0o600)
    except OSError as error:
        raise DistributionPrimitiveError("state_create_failed") from error
    return DistributionState(created)


def _state(state: DistributionState) -> Path:
    root = Path(state.root)
    if not root.exists(): return root
    root = _path(root, directory=True, code="state_invalid")
    marker = root / _MARKER
    if marker.is_symlink() or not marker.is_file(): _fail("state_invalid")
    return root


def _write_secret(state: DistributionState, environment: Mapping[str, str], env_name: str, filename: str, kind: str, *, json_secret: bool = False) -> CredentialFile:
    root = _state(state)
    if not root.exists(): _fail("state_invalid")
    target = root / filename
    if target.exists() or target.is_symlink(): _fail("credential_exists")
    raw = _secret(environment, env_name)
    try:
        if json_secret:
            value = json.loads(raw)
            if not isinstance(value, dict) or not value: _fail("credential_encoding_invalid")
            data = json.dumps(value, separators=(",", ":")).encode()
        else:
            data = base64.b64decode(raw, validate=True)
    except (json.JSONDecodeError, ValueError, binascii.Error) as error:
        raise DistributionPrimitiveError("credential_encoding_invalid") from error
    if not data or len(data) > 16 * 1024 * 1024: _fail("credential_size_invalid")
    try:
        target.write_bytes(data); target.chmod(0o600)
    except OSError as error:
        raise DistributionPrimitiveError("credential_write_failed") from error
    return CredentialFile(kind, target)


def materialize_apple_credentials(state: DistributionState, *, environment: Mapping[str, str], include_profile: bool = True) -> AppleCredentials:
    certificate = _write_secret(state, environment, APPLE_CERTIFICATE_B64_ENV, "apple-signing.p12", "apple-certificate")
    profile = _write_secret(state, environment, APPLE_PROFILE_B64_ENV, "apple-provisioning.mobileprovision", "apple-profile") if include_profile else None
    return AppleCredentials(certificate, profile)


def materialize_android_keystore(state: DistributionState, *, environment: Mapping[str, str]) -> CredentialFile:
    return _write_secret(state, environment, ANDROID_KEYSTORE_B64_ENV, "android-signing.keystore", "android-keystore")


def materialize_store_auth(state: DistributionState, provider: Literal["app-store-connect", "google-play"], *, environment: Mapping[str, str]) -> StoreAuth:
    if provider == "app-store-connect":
        key_id, issuer = _secret(environment, APP_STORE_KEY_ID_ENV), _secret(environment, APP_STORE_ISSUER_ID_ENV)
        if _SAFE.fullmatch(key_id) is None or _SAFE.fullmatch(issuer) is None: _fail("store_identity_invalid")
        key = _write_secret(state, environment, APP_STORE_PRIVATE_KEY_B64_ENV, f"AuthKey_{key_id}.p8", "app-store-private-key")
        return StoreAuth(provider, key, key_id, issuer)
    if provider == "google-play":
        key = _write_secret(state, environment, GOOGLE_PLAY_SERVICE_ACCOUNT_JSON_ENV, "google-play-service-account.json", "google-play-service-account", json_secret=True)
        return StoreAuth(provider, key)
    _fail("provider_invalid")


def _run(operation: str, argv: Sequence[str], cwd: Path, environment: Mapping[str, str] | None, runner: CommandRunner | None) -> None:
    try:
        result = (runner or RuntimeCommandRunner()).run(tuple(_line(v, "argument_invalid") for v in argv), cwd=_path(cwd, directory=True), env=_env(environment))
    except RuntimePrimitiveError as error:
        raise DistributionPrimitiveError("command_unavailable" if error.code == "process_start_failed" else "runtime_failed", operation) from error
    except OSError as error:
        raise DistributionPrimitiveError("command_unavailable", operation) from error
    if not isinstance(result, (ProcessResult, CommandOutcome)): _fail("runner_result_invalid", operation)
    if result.timed_out: _fail("command_timeout", operation)
    if result.returncode != 0: _fail("command_failed", operation)


def prepare_apple_keychain(security: Path, credentials: AppleCredentials, state: DistributionState, *, environment: Mapping[str, str], runner: CommandRunner | None = None) -> AppleKeychain:
    operation, tool, root = "apple.keychain.prepare", _tool(security, "apple.keychain.prepare"), _state(state)
    password, certificate_password = _secret(environment, APPLE_KEYCHAIN_PASSWORD_ENV), _secret(environment, APPLE_CERTIFICATE_PASSWORD_ENV)
    keychain = root / "ciw-signing.keychain-db"
    for argv in ((str(tool), "create-keychain", "-p", password, str(keychain)), (str(tool), "unlock-keychain", "-p", password, str(keychain)), (str(tool), "import", str(_path(credentials.certificate.path, directory=False)), "-k", str(keychain), "-P", certificate_password, "-T", "/usr/bin/codesign")):
        _run(operation, argv, root, environment, runner)
    return AppleKeychain(keychain)


def apple_archive(xcodebuild: Path, *, project_directory: Path, container: Path, container_kind: Literal["project", "workspace"], scheme: str, configuration: str, archive_path: Path, destination: str | None = None, environment: Mapping[str, str] | None = None, runner: CommandRunner | None = None) -> PackageResult:
    operation, tool, root = "apple.archive", _tool(xcodebuild, "apple.archive"), _path(project_directory, directory=True)
    container_path = _path(container)
    if container_kind not in {"project", "workspace"} or not str(container_path).endswith(".xcodeproj" if container_kind == "project" else ".xcworkspace"): _fail("apple_container_invalid")
    archive = (root / archive_path if not archive_path.is_absolute() else archive_path).resolve(strict=False)
    if root not in archive.parents: _fail("archive_path_invalid")
    argv = [str(tool), f"-{container_kind}", str(container_path), "-scheme", _safe(scheme, "scheme_invalid"), "-configuration", _safe(configuration, "configuration_invalid"), "-archivePath", str(archive)]
    if destination is not None: argv += ["-destination", _safe(destination, "destination_invalid")]
    argv.append("archive"); _run(operation, argv, root, environment, runner)
    return PackageResult("apple", operation, archive, True, False)


def apple_export(xcodebuild: Path, *, project_directory: Path, archive: Path, export_options: Path, export_path: Path, environment: Mapping[str, str] | None = None, runner: CommandRunner | None = None) -> PackageResult:
    operation, tool, root = "apple.export", _tool(xcodebuild, "apple.export"), _path(project_directory, directory=True)
    archive_path, options = _path(archive, directory=True), _path(export_options, directory=False)
    output = (root / export_path if not export_path.is_absolute() else export_path).resolve(strict=False)
    if output != root and root not in output.parents: _fail("export_path_invalid")
    _run(operation, (str(tool), "-exportArchive", "-archivePath", str(archive_path), "-exportOptionsPlist", str(options), "-exportPath", str(output)), root, environment, runner)
    return PackageResult("apple", operation, output, True, False)


def apple_sign(codesign: Path, package: Path, *, project_directory: Path, identity: str, keychain: AppleKeychain | None = None, environment: Mapping[str, str] | None = None, runner: CommandRunner | None = None) -> PackageResult:
    operation, tool, root, target = "apple.sign", _tool(codesign, "apple.sign"), _path(project_directory, directory=True), _path(package)
    argv = [str(tool), "--force", "--sign", _safe(identity, "signing_identity_invalid")]
    if keychain is not None: argv += ["--keychain", str(keychain.path)]
    argv.append(str(target)); _run(operation, argv, root, environment, runner)
    return PackageResult("apple", operation, target, True, False)


def apple_verify(codesign: Path, package: Path, *, project_directory: Path, environment: Mapping[str, str] | None = None, runner: CommandRunner | None = None) -> PackageResult:
    operation, tool, root, target = "apple.verify", _tool(codesign, "apple.verify"), _path(project_directory, directory=True), _path(package)
    _run(operation, (str(tool), "--verify", "--deep", "--strict", "--verbose=2", str(target)), root, environment, runner)
    return PackageResult("apple", operation, target, True, True)


def android_sign(kind: Literal["apk", "aab"], tool: Path, package: Path, keystore: CredentialFile, *, project_directory: Path, key_alias: str, environment: Mapping[str, str], output_path: Path | None = None, runner: CommandRunner | None = None) -> PackageResult:
    root, target, store = _path(project_directory, directory=True), _path(package, directory=False), _path(keystore.path, directory=False)
    _secret(environment, ANDROID_STORE_PASSWORD_ENV); _secret(environment, ANDROID_KEY_PASSWORD_ENV)
    alias = _safe(key_alias, "key_alias_invalid")
    if kind == "apk":
        signer, operation = _tool(tool, "android.apk.sign"), "android.apk.sign"
        if output_path is None: _fail("output_path_required")
        output = (root / output_path if not output_path.is_absolute() else output_path).resolve(strict=False)
        if root not in output.parents: _fail("output_path_invalid")
        argv = (str(signer), "sign", "--ks", str(store), "--ks-pass", f"env:{ANDROID_STORE_PASSWORD_ENV}", "--key-pass", f"env:{ANDROID_KEY_PASSWORD_ENV}", "--ks-key-alias", alias, "--out", str(output), str(target)); result_path = output
    elif kind == "aab":
        signer, operation, result_path = _tool(tool, "android.aab.sign"), "android.aab.sign", target
        argv = (str(signer), "-keystore", str(store), "-storepass:env", ANDROID_STORE_PASSWORD_ENV, "-keypass:env", ANDROID_KEY_PASSWORD_ENV, str(target), alias)
    else: _fail("package_kind_invalid")
    _run(operation, argv, root, environment, runner)
    return PackageResult("android", operation, result_path, True, False)


def android_verify(kind: Literal["apk", "aab"], tool: Path, package: Path, *, project_directory: Path, environment: Mapping[str, str] | None = None, runner: CommandRunner | None = None) -> PackageResult:
    root, target = _path(project_directory, directory=True), _path(package, directory=False)
    if kind == "apk": verifier, operation, argv = _tool(tool, "android.apk.verify"), "android.apk.verify", None
    elif kind == "aab": verifier, operation, argv = _tool(tool, "android.aab.verify"), "android.aab.verify", None
    else: _fail("package_kind_invalid")
    argv = (str(verifier), "verify", "--verbose", "--print-certs", str(target)) if kind == "apk" else (str(verifier), "-verify", "-strict", "-verbose", str(target))
    _run(operation, argv, root, environment, runner)
    return PackageResult("android", operation, target, True, True)


def app_store_upload_request(xcrun: Path, package: Path, auth: StoreAuth, *, project_directory: Path, platform: Literal["ios", "tvos"]) -> UploadRequest:
    if auth.provider != "app-store-connect" or platform not in {"ios", "tvos"}: _fail("upload_request_invalid")
    tool, root, target = _tool(xcrun, "app-store.request"), _path(project_directory, directory=True), _path(package, directory=False)
    argv = (str(tool), "altool", "--upload-app", "--file", str(target), "--type", platform, "--apiKey", auth.key_id, "--apiIssuer", auth.issuer_id)
    return UploadRequest(auth.provider, target, argv, root, {"API_PRIVATE_KEYS_DIR": str(auth.credential.path.parent)})


def google_play_upload_request(fastlane: Path, package: Path, auth: StoreAuth, *, project_directory: Path, package_name: str, track: str, kind: Literal["apk", "aab"], release_status: str | None = None) -> UploadRequest:
    if auth.provider != "google-play" or _PACKAGE.fullmatch(package_name) is None or _TRACK.fullmatch(track) is None or kind not in {"apk", "aab"}: _fail("upload_request_invalid")
    tool, root, target = _tool(fastlane, "google-play.request"), _path(project_directory, directory=True), _path(package, directory=False)
    argv = [str(tool), "supply", "--json_key", str(auth.credential.path), "--package_name", package_name, "--track", track, f"--{kind}", str(target), "--skip_upload_metadata", "true", "--skip_upload_images", "true", "--skip_upload_screenshots", "true"]
    if release_status is not None: argv += ["--release_status", _safe(release_status, "release_status_invalid")]
    return UploadRequest(auth.provider, target, tuple(argv), root, {})


def execute_upload(request: UploadRequest, *, environment: Mapping[str, str] | None = None, runner: CommandRunner | None = None) -> UploadResult:
    environment_copy = _env(environment); environment_copy.update(request.environment_overrides)
    _run(f"{request.provider}.upload", request.argv, request.cwd, environment_copy, runner)
    return UploadResult(request.provider, request.package, True)


def cleanup_distribution_state(state: DistributionState, *, security: Path | None = None, keychain: AppleKeychain | None = None, environment: Mapping[str, str] | None = None, runner: CommandRunner | None = None) -> None:
    root = Path(state.root)
    if not root.exists(): return
    root = _state(state); failure: DistributionPrimitiveError | None = None
    if keychain is not None and (keychain.path.exists() or keychain.path.is_symlink()):
        try:
            if security is None: _fail("security_tool_required")
            tool = _tool(security, "apple.keychain.cleanup"); _run("apple.keychain.cleanup", (str(tool), "delete-keychain", str(keychain.path)), root, environment, runner)
        except DistributionPrimitiveError as error: failure = error
    try: shutil.rmtree(root)
    except OSError as error: raise DistributionPrimitiveError("cleanup_failed") from error
    if root.exists(): _fail("cleanup_failed")
    if failure is not None: raise failure
