"""Execution engine for the bounded Flutter validation contract."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Protocol, Sequence

from .flutter_contract import (
    FlutterValidationError,
    bounded_path,
    canonical_json_bytes,
    checked_in_script,
    fail,
    parse_jdk_identity,
    parse_runtime_identity,
    safe_relative,
    source_authority_hashes,
)
from .flutter_types import (
    FlutterPlan,
    FlutterProfile,
    FlutterResult,
    FlutterStage,
    RunnerCapability,
)


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> CommandOutcome: ...


class SubprocessCommandRunner:
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> CommandOutcome:
        try:
            completed = subprocess.run(
                list(argv),
                cwd=cwd,
                env=dict(env),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError:
            fail("command_failed")
        return CommandOutcome(completed.returncode, completed.stdout, completed.stderr)


def _lstat(path: Path, code: str = "cleanup_failed") -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        fail(code)


def _state_directory(state_root: Path, *, create: bool = True) -> Path:
    path = Path(os.path.abspath(state_root)) / "flutter-validation"
    metadata = _lstat(path)
    if metadata is not None and (
        stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode)
    ):
        fail("cleanup_failed")
    if metadata is None and create:
        path.mkdir(parents=True, exist_ok=False)
    return path


def expected_pub_cache_path(state_root: Path) -> Path:
    return _state_directory(state_root) / "pub-cache"


def _path_is_within(path: Path, root: Path) -> bool:
    absolute = Path(os.path.abspath(path))
    base = Path(os.path.abspath(root))
    return absolute == base or base in absolute.parents


def _assert_exact_pub_cache(
    state_root: Path,
    environment: Mapping[str, str],
    *,
    require_exists: bool,
) -> Path:
    expected = expected_pub_cache_path(state_root)
    actual = environment.get("PUB_CACHE", "")
    if not actual or not Path(actual).is_absolute() or Path(os.path.abspath(actual)) != expected:
        fail("pub_cache_rejected")
    metadata = _lstat(expected, "pub_cache_rejected")
    if require_exists and metadata is None:
        fail("pub_cache_rejected")
    if metadata is not None and (
        stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode)
    ):
        fail("pub_cache_rejected")
    if not _path_is_within(expected, state_root):
        fail("pub_cache_rejected")
    return expected


def isolated_environment(
    state_root: Path,
    base: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, Path]]:
    root = _state_directory(state_root)
    directories = {
        name: root / name
        for name in (
            "home",
            "pub-cache",
            "flutter-state",
            "gradle-home",
            "cocoapods-home",
            "derived-data",
            "tmp",
            "logs",
            "reports",
            "build-output",
        )
    }
    for path in directories.values():
        metadata = _lstat(path)
        if metadata is not None and (
            stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode)
        ):
            fail("cleanup_failed")
        if metadata is None:
            path.mkdir(parents=True, exist_ok=False)
    blocked = {
        "FLUTTER_STORAGE_BASE_URL",
        "PUB_HOSTED_URL",
        "CI_FLUTTER_VERSION",
        "FLUTTER_VERSION",
        "DEVICE_ID",
        "ANDROID_SERIAL",
        "FASTLANE_SESSION",
        "MATCH_PASSWORD",
        "APPLE_CERTIFICATE",
        "APPLE_CERTIFICATE_PASSWORD",
        "APP_STORE_CONNECT_API_KEY",
        "SUPABASE_ACCESS_TOKEN",
        "DATABASE_URL",
    }
    env = {key: value for key, value in (base or os.environ).items() if key not in blocked}
    env.update(
        HOME=str(directories["home"]),
        PUB_CACHE=str(directories["pub-cache"]),
        FLUTTER_SUPPRESS_ANALYTICS="true",
        CI="true",
        GRADLE_USER_HOME=str(directories["gradle-home"]),
        COCOAPODS_HOME=str(directories["cocoapods-home"]),
        CI_DERIVED_DATA=str(directories["derived-data"]),
        TMPDIR=str(directories["tmp"]),
        XDG_CACHE_HOME=str(directories["home"] / ".cache"),
        XDG_CONFIG_HOME=str(directories["home"] / ".config"),
        XDG_STATE_HOME=str(directories["home"] / ".local" / "state"),
        NO_COLOR="1",
    )
    return env, directories


def _snapshot_entry(path: Path, root: Path, digest: "hashlib._Hash", count: list[int]) -> None:
    metadata = _lstat(path, "persistent_pub_cache_changed")
    relative = "." if path == root else path.relative_to(root).as_posix()
    if metadata is None:
        digest.update(f"{relative}\0missing\n".encode())
        return
    count[0] += 1
    if count[0] > 100_000:
        fail("persistent_pub_cache_changed")
    kind = (
        "symlink" if stat.S_ISLNK(metadata.st_mode)
        else "directory" if stat.S_ISDIR(metadata.st_mode)
        else "file" if stat.S_ISREG(metadata.st_mode)
        else "other"
    )
    digest.update(
        f"{relative}\0{kind}\0{stat.S_IMODE(metadata.st_mode)}\0{metadata.st_size}\0{metadata.st_mtime_ns}\n".encode()
    )
    if kind == "symlink":
        try:
            digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
        except OSError:
            fail("persistent_pub_cache_changed")
        return
    if kind == "file":
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
        except OSError:
            fail("persistent_pub_cache_changed")
        return
    if kind == "directory":
        try:
            children = sorted((path / entry.name for entry in os.scandir(path)), key=lambda item: item.name)
        except OSError:
            fail("persistent_pub_cache_changed")
        for child in children:
            _snapshot_entry(child, root, digest, count)


def fingerprint_no_follow(path: Path) -> str:
    digest = hashlib.sha256()
    _snapshot_entry(path, path, digest, [0])
    return digest.hexdigest()


def _snapshot_file(state_root: Path) -> Path:
    return _state_directory(state_root) / "persistent-pub-cache-snapshot.json"


def snapshot_persistent_pub_cache(
    state_root: Path,
    environment: Mapping[str, str],
) -> dict[str, str]:
    root = _state_directory(state_root)
    expected = expected_pub_cache_path(state_root)
    configured = environment.get("PUB_CACHE", "")
    if configured:
        configured_path = Path(configured)
        if not configured_path.is_absolute():
            fail("pub_cache_rejected")
        configured_path = Path(os.path.abspath(configured_path))
    else:
        configured_path = Path("")
    persistent = configured_path if configured and not _path_is_within(configured_path, root) else None
    payload = {
        "path": str(persistent) if persistent is not None else "",
        "fingerprint": fingerprint_no_follow(persistent) if persistent is not None else "not-applicable",
        "isolated_pub_cache": str(expected),
        "schema_version": 1,
    }
    _snapshot_file(state_root).write_bytes(canonical_json_bytes(payload))
    return {
        "persistent_pub_cache_path": payload["path"],
        "persistent_pub_cache_fingerprint": payload["fingerprint"],
        "pub_cache_path": str(expected),
        "failure_code": "",
        "primary_failure_code": "",
        "cleanup_failure_code": "",
    }


def bind_pub_cache(
    state_root: Path,
    environment: Mapping[str, str],
) -> dict[str, str]:
    snapshot = _snapshot_file(state_root)
    metadata = _lstat(snapshot, "pub_cache_rejected")
    if metadata is None or not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        fail("pub_cache_rejected")
    expected = expected_pub_cache_path(state_root)
    expected.mkdir(parents=True, exist_ok=True)
    metadata = _lstat(expected, "pub_cache_rejected")
    if metadata is None or not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        fail("pub_cache_rejected")
    github_env = environment.get("GITHUB_ENV", "")
    if github_env:
        target = Path(github_env)
        if not target.is_absolute() or target.is_symlink():
            fail("pub_cache_rejected")
        try:
            with target.open("a", encoding="utf-8") as handle:
                handle.write(f"PUB_CACHE={expected}\n")
        except OSError:
            fail("pub_cache_rejected")
    return {
        "pub_cache_path": str(expected),
        "persistent_pub_cache_unchanged": "pending",
        "failure_code": "",
        "primary_failure_code": "",
        "cleanup_failure_code": "",
    }


def verify_persistent_pub_cache(state_root: Path) -> dict[str, str]:
    snapshot = _snapshot_file(state_root)
    metadata = _lstat(snapshot, "persistent_pub_cache_changed")
    if metadata is None or not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        fail("persistent_pub_cache_changed")
    try:
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("persistent_pub_cache_changed")
    if not isinstance(payload, dict) or set(payload) != {
        "path", "fingerprint", "isolated_pub_cache", "schema_version"
    } or payload["schema_version"] != 1:
        fail("persistent_pub_cache_changed")
    path = payload["path"]
    current = fingerprint_no_follow(Path(path)) if path else "not-applicable"
    if current != payload["fingerprint"]:
        fail("persistent_pub_cache_changed")
    return {
        "persistent_pub_cache_path": str(path),
        "persistent_pub_cache_unchanged": "true",
        "failure_code": "",
        "primary_failure_code": "",
        "cleanup_failure_code": "",
    }


def _render_argv(argv: Sequence[str], values: Mapping[str, str]) -> tuple[str, ...]:
    rendered: list[str] = []
    for item in argv:
        output = item
        for key, value in values.items():
            output = output.replace("${" + key + "}", value)
        if "${" in output or "}" in output:
            fail("command_profile_rejected")
        rendered.append(output)
    return tuple(rendered)


def _run_checked(
    runner: CommandRunner,
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    failure_code: str,
    state_root: Path | None = None,
) -> CommandOutcome:
    if argv and argv[0] in {"flutter", "dart"}:
        if state_root is None:
            fail("pub_cache_rejected")
        _assert_exact_pub_cache(state_root, env, require_exists=True)
    outcome = runner.run(argv, cwd=cwd, env=env)
    if outcome.returncode != 0:
        fail(failure_code)
    return outcome


def _verify_authority(source_root: Path, before: Mapping[str, str]) -> str:
    after = source_authority_hashes(source_root)
    if dict(before) != after:
        fail("lockfile_drift")
    return after["pubspec.lock"]


def _git_marker_exists(source_root: Path) -> bool:
    marker = source_root / ".git"
    return marker.exists() or marker.is_file()


def _git_command(source_root: Path, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *arguments], cwd=source_root, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
    except OSError:
        fail("dirty_source")


def _assert_clean_source(source_root: Path) -> None:
    if not _git_marker_exists(source_root):
        return
    completed = _git_command(source_root, ["status", "--porcelain=v1", "--untracked-files=all"])
    if completed.returncode != 0 or completed.stdout.strip():
        fail("dirty_source")


def _assert_tracked_regular(source_root: Path, relative: str) -> None:
    checked_in_script(source_root, relative)
    if not _git_marker_exists(source_root):
        return
    completed = _git_command(source_root, ["ls-files", "--error-unmatch", "--", relative])
    if completed.returncode != 0 or completed.stdout.strip() != relative:
        fail("gate_path_rejected")


def _verify_expected_outputs(source_root: Path, expected: Sequence[str]) -> bool:
    for value in expected:
        path = bounded_path(source_root, safe_relative(value, allow_dot=False))
        metadata = _lstat(path, "path_rejected")
        if metadata is None:
            fail("output_missing")
        if stat.S_ISLNK(metadata.st_mode) or not (
            stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)
        ):
            fail("path_rejected")
    return bool(expected)


def _verify_gradle_wrapper(source_root: Path, expected_version: str) -> str:
    relative = "android/gradle/wrapper/gradle-wrapper.properties"
    path = bounded_path(source_root, safe_relative(relative, allow_dot=False))
    metadata = _lstat(path, "path_rejected")
    if metadata is None:
        fail("gradle_mismatch")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        fail("path_rejected")
    try:
        rows = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        fail("gradle_mismatch")
    values = [row.split("=", 1)[1].strip() for row in rows if row.startswith("distributionUrl=")]
    expected_suffix = f"gradle-{expected_version}-all.zip"
    if len(values) != 1 or not values[0].endswith(expected_suffix):
        fail("gradle_mismatch")
    return expected_version


def _compose_node(
    plan: FlutterPlan,
    contract_root: Path,
    source_root: Path,
    state_root: Path,
    runner: CommandRunner,
    env: Mapping[str, str],
) -> None:
    composition = plan.node_composition
    if composition is None:
        return
    node_script = contract_root / "scripts" / "ci" / "node.py"
    if not node_script.is_file() or node_script.is_symlink():
        fail("node_composition_failed")
    node_env = dict(env)
    node_env.update(
        INPUT_ADMITTED_SHA=plan.request.admitted_sha,
        INPUT_VALIDATION_PROFILE=composition["validation_profile"],
        INPUT_VERSION_FILE=composition.get("version_file", ""),
        INPUT_NODE_VERSION=composition.get("node_version", ""),
        INPUT_WORKING_DIRECTORY=composition.get("working_directory", "."),
        INPUT_INSTALL_PROFILE=composition["install_profile"],
        INPUT_COMMAND_PROFILE=composition["command_profile"],
        INPUT_SCRIPT_PATH=composition.get("script_path", ""),
        INPUT_STATIC_OUTPUT_DIRECTORY="",
        INPUT_OUTPUT_VERIFIER_PATH="",
        INPUT_PUBLIC_ENVIRONMENT="",
        INPUT_ARTIFACT_EXCEPTION_ID="",
        GITHUB_REPOSITORY=plan.request.repository,
        GITHUB_WORKSPACE=str(source_root.parent),
        RUNNER_TEMP=str(state_root.parent),
    )
    _run_checked(
        runner,
        ("python3", str(node_script), "--phase", "execute", "--source-root", source_root.name),
        cwd=contract_root,
        env=node_env,
        failure_code="node_composition_failed",
    )


def verify_toolchain_identity(
    *,
    plan: FlutterPlan,
    source_root: Path,
    state_root: Path,
    runner: CommandRunner | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    command_runner = runner or SubprocessCommandRunner()
    base = environment or os.environ
    if plan.install_required:
        _assert_exact_pub_cache(state_root, base, require_exists=True)
    env, _ = isolated_environment(state_root, base)
    jdk = {
        "java_version": "",
        "java_runtime_version": "",
        "java_vendor": "",
        "javac_version": "",
    }
    if plan.runner_profile is RunnerCapability.MOBILE:
        java_home = env.get("JAVA_HOME", "")
        if not java_home or not Path(java_home).is_absolute():
            fail("jdk_mismatch")
        java = _run_checked(
            command_runner,
            ("java", "-XshowSettings:properties", "-version"),
            cwd=source_root,
            env=env,
            failure_code="jdk_mismatch",
        )
        javac = _run_checked(
            command_runner,
            ("javac", "-version"),
            cwd=source_root,
            env=env,
            failure_code="jdk_mismatch",
        )
        jdk = parse_jdk_identity(
            "\n".join(part for part in (java.stdout, java.stderr) if part),
            "\n".join(part for part in (javac.stdout, javac.stderr) if part),
            plan.toolchain,
        )
    runtime = _run_checked(
        command_runner,
        ("flutter", "--version", "--machine"),
        cwd=source_root,
        env=env,
        failure_code="runtime_identity_invalid",
        state_root=state_root,
    )
    return {**parse_runtime_identity(runtime.stdout, plan.toolchain), **jdk}


def execute_flutter_plan(
    *,
    plan: FlutterPlan,
    contract_root: Path,
    source_root: Path,
    state_root: Path,
    runner: CommandRunner | None = None,
    environment: Mapping[str, str] | None = None,
) -> FlutterResult:
    command_runner = runner or SubprocessCommandRunner()
    before = source_authority_hashes(source_root)
    base = environment or os.environ
    if plan.install_required:
        _assert_exact_pub_cache(state_root, base, require_exists=True)
    env, directories = isolated_environment(state_root, base)
    completed: list[FlutterStage] = []
    identity = {
        "flutter_version": plan.toolchain.flutter_version,
        "dart_version": plan.toolchain.dart_version,
        "framework_revision": plan.toolchain.framework_revision,
        "engine_revision": plan.toolchain.engine_revision,
        "java_version": "",
        "java_runtime_version": "",
        "java_vendor": "",
        "javac_version": "",
    }
    output_verified = False
    gradle_verified = ""
    if plan.request.validation_profile is FlutterProfile.SOURCE_AUDIT:
        if plan.install_required:
            fail("source_audit_install_rejected")
    elif plan.request.validation_profile is FlutterProfile.DEVICE_HANDOFF:
        if plan.install_required:
            fail("device_boundary_rejected")
    else:
        identity = verify_toolchain_identity(
            plan=plan,
            source_root=source_root,
            state_root=state_root,
            runner=command_runner,
            environment=env,
        )
        if plan.runner_profile is RunnerCapability.MOBILE:
            completed.append(FlutterStage.JDK_VERIFY)
        completed.append(FlutterStage.RUNTIME_VERIFY)

    if plan.node_composition is not None:
        _compose_node(plan, contract_root, source_root, state_root, command_runner, env)
        completed.append(FlutterStage.NODE_COMPOSITION)

    values = {
        "source_sha": plan.request.admitted_sha,
        "run_number": env.get("GITHUB_RUN_NUMBER", "0"),
        "derived_data": str(directories["derived-data"]),
    }
    for command in plan.commands:
        if command.stage is FlutterStage.ANDROID_DEBUG and not gradle_verified:
            gradle_verified = _verify_gradle_wrapper(source_root, plan.toolchain.gradle_version)
            completed.append(FlutterStage.GRADLE_VERIFY)
        cwd = bounded_path(source_root, safe_relative(command.working_directory))
        metadata = _lstat(cwd, "command_profile_rejected")
        if metadata is None or stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            fail("command_profile_rejected")
        argv = _render_argv(command.argv, values)
        if argv[0] == "checked-in-script":
            _assert_tracked_regular(source_root, argv[1])
            script = checked_in_script(source_root, argv[1])
            argv = ("bash", str(script), *argv[2:])
        _run_checked(
            command_runner,
            argv,
            cwd=cwd,
            env=env,
            failure_code="command_failed",
            state_root=state_root,
        )
        completed.append(command.stage)
        if command.expected_outputs:
            output_verified = _verify_expected_outputs(source_root, command.expected_outputs) or output_verified

    lock_hash = _verify_authority(source_root, before)
    evidence_id = hashlib.sha256(
        json.dumps(
            {
                "repository": plan.request.repository,
                "consumer": plan.request.consumer_contract,
                "profile": plan.request.validation_profile.value,
                "sha": plan.request.admitted_sha,
                "stages": [stage.value for stage in completed],
                "flutter": identity["flutter_version"],
                "dart": identity["dart_version"],
                "gradle": gradle_verified,
                "java": identity["java_version"],
                "javac": identity["javac_version"],
                "lock": lock_hash,
                "pub_cache": str(directories["pub-cache"]),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return FlutterResult(
        plan=plan,
        status="success",
        completed_stages=tuple(completed),
        flutter_version=identity["flutter_version"],
        dart_version=identity["dart_version"],
        framework_revision=identity["framework_revision"],
        engine_revision=identity["engine_revision"],
        lockfile_sha256=lock_hash,
        clean_tree=True,
        cleanup_result="deferred-to-always",
        output_verified=output_verified,
        evidence_id=evidence_id,
        device_handoff=plan.device_handoff,
        gradle_version=gradle_verified,
        java_version=identity["java_version"],
        java_runtime_version=identity["java_runtime_version"],
        java_vendor=identity["java_vendor"],
        javac_version=identity["javac_version"],
        pub_cache_path=str(directories["pub-cache"]),
        persistent_pub_cache_unchanged=False,
    )


def _lexical_target(root: Path, relative: str) -> Path:
    root = Path(os.path.abspath(root))
    metadata = _lstat(root)
    if metadata is None or not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        fail("cleanup_failed")
    parts = PurePosixPath(relative).parts
    if not parts or ".." in parts:
        fail("cleanup_failed")
    target = root.joinpath(*parts)
    current = root
    for part in parts[:-1]:
        current /= part
        current_metadata = _lstat(current)
        if current_metadata is None:
            break
        if not stat.S_ISDIR(current_metadata.st_mode) or stat.S_ISLNK(current_metadata.st_mode):
            fail("cleanup_failed")
    return target


def _remove_no_follow(path: Path) -> None:
    metadata = _lstat(path)
    if metadata is None:
        return
    try:
        if stat.S_ISLNK(metadata.st_mode) or stat.S_ISREG(metadata.st_mode):
            os.unlink(path)
        elif stat.S_ISDIR(metadata.st_mode):
            os.chmod(
                path,
                metadata.st_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR,
                follow_symlinks=False,
            )
            with os.scandir(path) as entries:
                children = [path / entry.name for entry in entries]
            for child in children:
                _remove_no_follow(child)
            os.rmdir(path)
        else:
            fail("cleanup_failed")
    except FlutterValidationError:
        raise
    except OSError:
        fail("cleanup_failed")
    if _lstat(path) is not None:
        fail("cleanup_failed")


def _cleanup_targets(source_root: Path, state_root: Path) -> tuple[Path, ...]:
    state_targets = (_lexical_target(state_root, "flutter-validation"),)
    source_metadata = _lstat(source_root)
    if source_metadata is None:
        return state_targets
    if not stat.S_ISDIR(source_metadata.st_mode) or stat.S_ISLNK(source_metadata.st_mode):
        fail("cleanup_failed")
    return state_targets + (
        _lexical_target(source_root, "build"),
        _lexical_target(source_root, ".dart_tool"),
        _lexical_target(source_root, "coverage"),
        _lexical_target(source_root, "ios/Pods"),
        _lexical_target(source_root, "ios/.symlinks"),
        _lexical_target(source_root, "ios/Flutter/ephemeral"),
        _lexical_target(source_root, "ios/Flutter/Generated.xcconfig"),
        _lexical_target(source_root, "ios/Flutter/flutter_export_environment.sh"),
        _lexical_target(source_root, "android/.gradle"),
    )


def cleanup_flutter_state(source_root: Path, state_root: Path) -> None:
    for path in _cleanup_targets(source_root, state_root):
        _remove_no_follow(path)


def terminal_cleanup_flutter_state(
    source_root: Path,
    state_root: Path,
    *,
    primary_failure_code: str = "",
) -> dict[str, str]:
    try:
        cleanup_flutter_state(source_root, state_root)
    except FlutterValidationError as cleanup:
        if primary_failure_code:
            raise FlutterValidationError(primary_failure_code, cleanup.code) from cleanup
        raise
    return {
        "result": "failure" if primary_failure_code else "success",
        "failure_code": primary_failure_code,
        "primary_failure_code": primary_failure_code,
        "cleanup_failure_code": "",
        "cleanup_result": "success",
    }


def assert_zero_flutter_residue(source_root: Path, state_root: Path) -> None:
    remaining = [str(path) for path in _cleanup_targets(source_root, state_root) if _lstat(path) is not None]
    if remaining:
        fail("cleanup_failed")
    if _lstat(source_root) is not None:
        _assert_clean_source(source_root)
