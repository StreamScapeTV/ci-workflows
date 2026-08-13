"""Hermetic Android/Gradle execution and cleanup."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from .android_contract import bounded_path, git_blob_sha1, require
from .android_types import AndroidValidationError, AndroidValidationPlan, AndroidValidationResult

_MAJOR = re.compile(r'(?:openjdk version |java version |javac )?"?([0-9]+)')
_GRADLE = re.compile(r'(?m)^Gradle\s+([0-9]+\.[0-9]+\.[0-9]+)\s*$')
_SECRET = re.compile(r'(?i)(token|password|authorization|secret|keystore)\s*[:=]\s*\S+')
SYNTHETIC_SMOKE_TASK = ":verifyToolchainSmoke"
GRADLE_DAEMON_CLEANUP_GRACE_SECONDS = 30
GRADLE_DAEMON_CLEANUP_POLL_SECONDS = 0.25


def sanitize(text: str, roots: Sequence[Path] = ()) -> str:
    for root in roots:
        text = text.replace(str(root), "<state>")
    text = re.sub(r'(?i)https?://[^\s/@]+:[^\s/@]+@', 'https://<redacted>@', text)
    return "\n".join(_SECRET.sub(r'\1=<redacted>', text).splitlines()[-120:])


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: int,
    failure_code: str,
    state_root: Path | None = None,
    stage: str = "command",
    check: bool = True,
    timeout_rule_id: str | None = None,
    launch_rule_id: str | None = None,
    nonzero_rule_id: str | None = None,
    diagnostic_subject: str | None = None,
) -> subprocess.CompletedProcess[str]:
    require(bool(argv) and all(isinstance(x, str) and x for x in argv), "invalid_input")
    diagnostic_rules = (timeout_rule_id, launch_rule_id, nonzero_rule_id)
    require(
        (diagnostic_subject is None and not any(diagnostic_rules))
        or (diagnostic_subject is not None and all(diagnostic_rules)),
        "invalid_input",
    )

    def failure(rule_id: str | None) -> AndroidValidationError:
        if rule_id is None or diagnostic_subject is None:
            return AndroidValidationError(failure_code)
        return AndroidValidationError(
            failure_code,
            rule_id=rule_id,
            subject=diagnostic_subject,
        )

    try:
        result = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(environment),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise failure(timeout_rule_id) from error
    except OSError as error:
        raise failure(launch_rule_id) from error
    output = sanitize(
        (result.stdout or "") + (result.stderr or ""),
        (cwd, state_root) if state_root else (cwd,),
    )
    if state_root:
        logs = state_root / "android-validation/logs"
        logs.mkdir(parents=True, exist_ok=True, mode=0o700)
        require(re.fullmatch(r'[a-z][a-z0-9-]{1,63}', stage) is not None, "invalid_input")
        (logs / f"{stage}.log").write_text(output, encoding="utf-8")
    if check and result.returncode:
        raise failure(nonzero_rule_id)
    return result


def isolated_git_environment(
    inherited: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the fixed Git environment used at every source-integrity boundary."""
    source = os.environ if inherited is None else inherited
    path = source.get("PATH", "")
    home = source.get("HOME", "")
    require(bool(path) and bool(home), "invalid_input")
    home_path = Path(home)
    require(home_path.is_absolute(), "invalid_input")
    return {
        "PATH": path,
        "HOME": str(home_path),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "LANG": "C",
        "LC_ALL": "C",
    }


def git_output(
    root: Path,
    args: Sequence[str],
    code: str = "dirty_tree",
    inherited: Mapping[str, str] | None = None,
) -> str:
    return run_command(
        ["git", *args],
        cwd=root,
        environment=isolated_git_environment(inherited),
        timeout_seconds=60,
        failure_code=code,
        stage="git",
    ).stdout.strip()


def pre_execution_status(
    root: Path,
    inherited: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Capture the exact bounded status checked before any Android command runs."""
    output = git_output(
        root,
        ["status", "--porcelain=v1", "--untracked-files=all"],
        "dirty_tree",
        inherited,
    )
    return tuple(line for line in output.splitlines() if line)


def verify_exact_source(
    root: Path,
    sha: str,
    inherited: Mapping[str, str] | None = None,
) -> None:
    require(root.is_dir() and (root / ".git").exists(), "source_mismatch")
    require(
        git_output(root, ["rev-parse", "HEAD"], "source_mismatch", inherited) == sha,
        "source_mismatch",
    )
    require(not pre_execution_status(root, inherited), "dirty_tree")


def copy_source(source: Path, destination: Path) -> None:
    require(not destination.exists() and not destination.is_symlink(), "cleanup_failed")
    for current, directories, files in os.walk(source, followlinks=False):
        base = Path(current)
        require(
            all(not (base / name).is_symlink() for name in [*directories, *files]),
            "invalid_input",
        )
        target = destination / base.relative_to(source)
        target.mkdir(parents=True, exist_ok=True)
        for name in files:
            shutil.copy2(base / name, target / name, follow_symlinks=False)


def remove_no_follow(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    require(path.is_dir(), "cleanup_failed")
    for entry in os.scandir(path):
        child = path / entry.name
        if entry.is_symlink() or entry.is_file(follow_symlinks=False):
            child.unlink()
        else:
            remove_no_follow(child)
    path.rmdir()


def _properties(path: Path, code: str) -> dict[str, str]:
    require(path.is_file() and not path.is_symlink(), code)
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        row = raw.strip()
        if row and not row.startswith("#") and "=" in row:
            key, value = row.split("=", 1)
            result[key.strip()] = value.strip().replace("\\:", ":")
    return result


def _major(output: str) -> int | None:
    match = _MAJOR.search(output)
    return int(match.group(1)) if match else None


def _has_installed_package(listing: str, package: str) -> bool:
    """Match a reviewed installed-package identity without weakening package checks."""
    identities = (
        (package, "platforms;android-37.0")
        if package == "platforms;android-37"
        else (package,)
    )
    return any(
        re.search(rf'(?m)^\s*{re.escape(identity)}\s*\|', listing) is not None
        for identity in identities
    )


def _isolated_runtime_directories(state: Path) -> dict[str, Path]:
    require(state.is_absolute(), "invalid_input")
    base = state / "android-validation"
    directories = {
        "HOME": base / "home",
        "GRADLE_USER_HOME": base / "gradle-home",
        "ANDROID_USER_HOME": base / "android-home",
        "TMPDIR": base / "tmp",
    }
    for directory in directories.values():
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directories


def verify_toolchain(
    source: Path,
    state: Path,
    plan: AndroidValidationPlan,
    contract: Mapping[str, Any],
    inherited: Mapping[str, str],
) -> tuple[int, int]:
    directories = _isolated_runtime_directories(state)
    toolchain = contract.get("toolchain")
    require(isinstance(toolchain, Mapping), "toolchain_mismatch")
    expected_cmdline_revision = toolchain.get("command_line_tools_version")
    require(isinstance(expected_cmdline_revision, str), "toolchain_mismatch")
    sdk_value = inherited.get("ANDROID_SDK_ROOT") or inherited.get("ANDROID_HOME")
    require(bool(sdk_value), "sdk_package_missing")
    sdk = Path(str(sdk_value)).resolve()
    require(sdk.is_absolute() and sdk.is_dir(), "sdk_package_missing")
    env = {
        "PATH": inherited.get("PATH", os.environ.get("PATH", "")),
        "JAVA_HOME": inherited.get("JAVA_HOME", ""),
        "ANDROID_SDK_ROOT": str(sdk),
        "ANDROID_HOME": str(sdk),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        **{key: str(value) for key, value in directories.items()},
    }
    java = run_command(
        ["java", "-version"],
        cwd=source,
        environment=env,
        timeout_seconds=30,
        failure_code="toolchain_mismatch",
        state_root=state,
        stage="java-version",
    )
    javac = run_command(
        ["javac", "-version"],
        cwd=source,
        environment=env,
        timeout_seconds=30,
        failure_code="toolchain_mismatch",
        state_root=state,
        stage="javac-version",
    )
    require(
        _major(java.stdout + java.stderr) == 25
        and _major(javac.stdout + javac.stderr) == 25,
        "toolchain_mismatch",
    )
    sdkmanager = sdk / "cmdline-tools/latest/bin/sdkmanager"
    require(
        sdkmanager.is_file()
        and not sdkmanager.is_symlink()
        and os.access(sdkmanager, os.X_OK)
        and sdk in sdkmanager.resolve().parents,
        "sdk_package_missing",
    )
    version = run_command(
        [str(sdkmanager), "--version"],
        cwd=source,
        environment=env,
        timeout_seconds=30,
        failure_code="toolchain_mismatch",
        state_root=state,
        stage="sdkmanager-version",
    ).stdout.strip()
    require(version == expected_cmdline_revision, "toolchain_mismatch")
    require(
        _properties(
            sdk / "cmdline-tools/latest/source.properties",
            "sdk_package_missing",
        ).get("Pkg.Revision")
        == expected_cmdline_revision,
        "toolchain_mismatch",
    )
    listing = run_command(
        [str(sdkmanager), "--list_installed"],
        cwd=source,
        environment=env,
        timeout_seconds=120,
        failure_code="sdk_package_missing",
        state_root=state,
        stage="sdk-packages",
    ).stdout
    for package in toolchain["packages"]:
        require(_has_installed_package(listing, package), "sdk_package_missing")
    require((sdk / "platforms/android-37/android.jar").is_file(), "sdk_package_missing")
    build = sdk / "build-tools/37.0.0"
    require((build / "aapt2").is_file(), "sdk_package_missing")
    require(
        _properties(build / "source.properties", "sdk_package_missing").get("Pkg.Revision")
        == "37.0.0",
        "toolchain_mismatch",
    )
    return 25, 37


def verify_wrapper(
    copy: Path,
    state: Path,
    plan: AndroidValidationPlan,
    environment: Mapping[str, str],
) -> str:
    working = bounded_path(copy, plan.working_directory)
    require(plan.gradle_wrapper_path == plan.wrapper.launcher_path, "wrapper_invalid")
    wrapper = bounded_path(working, plan.wrapper.launcher_path)
    require(
        wrapper.is_file() and not wrapper.is_symlink() and os.access(wrapper, os.X_OK),
        "wrapper_invalid",
    )
    properties = bounded_path(working, plan.wrapper.properties_path)
    require(properties.is_file() and not properties.is_symlink(), "wrapper_invalid")
    jar = (
        bounded_path(working, plan.wrapper.jar_path)
        if plan.wrapper.jar_path is not None
        else None
    )
    if jar is not None:
        require(jar.is_file() and not jar.is_symlink(), "wrapper_invalid")
    values = _properties(properties, "wrapper_invalid")
    require(
        values.get("distributionUrl") == plan.wrapper.distribution_url,
        "wrapper_distribution_drift",
    )
    if plan.wrapper.distribution_sha256:
        require(
            values.get("distributionSha256Sum") == plan.wrapper.distribution_sha256,
            "wrapper_distribution_drift",
        )
    require(
        git_blob_sha1(wrapper) == plan.wrapper.launcher_blob_sha1,
        "wrapper_invalid",
    )
    require(
        git_blob_sha1(properties) == plan.wrapper.properties_blob_sha1,
        "wrapper_invalid",
    )
    if jar is not None:
        require(
            git_blob_sha1(jar) == plan.wrapper.jar_blob_sha1,
            "wrapper_invalid",
        )
    if plan.wrapper.mode == "standard-wrapper":
        return plan.wrapper.version
    result = run_command(
        [str(wrapper), *plan.fixed_gradle_arguments, "--version"],
        cwd=working,
        environment=environment,
        timeout_seconds=120,
        failure_code="wrapper_invalid",
        state_root=state,
        stage="gradle-version",
        timeout_rule_id="wrapper_probe_timeout",
        launch_rule_id="wrapper_probe_launch_failed",
        nonzero_rule_id="wrapper_probe_nonzero",
        diagnostic_subject=plan.wrapper.launcher_path,
    )
    match = _GRADLE.search(result.stdout + result.stderr)
    require(
        match is not None and match.group(1) == plan.wrapper.version,
        "wrapper_distribution_drift",
    )
    if plan.wrapper.mode == "synthetic-smoke":
        run_command(
            [str(wrapper), *plan.fixed_gradle_arguments, SYNTHETIC_SMOKE_TASK],
            cwd=working,
            environment=environment,
            timeout_seconds=plan.timeout_minutes * 60,
            failure_code="toolchain_mismatch",
            state_root=state,
            stage="gradle-toolchain-smoke",
        )
    return plan.wrapper.version


def execution_environment(
    plan: AndroidValidationPlan,
    state: Path,
    inherited: Mapping[str, str],
    dependency: Path | None,
) -> dict[str, str]:
    dirs = _isolated_runtime_directories(state)
    sdk = inherited.get("ANDROID_SDK_ROOT", inherited.get("ANDROID_HOME", ""))
    env = {
        "PATH": inherited.get("PATH", os.environ.get("PATH", "")),
        "JAVA_HOME": inherited.get("JAVA_HOME", ""),
        "ANDROID_SDK_ROOT": sdk,
        "ANDROID_HOME": sdk,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "CI": "true",
        "GITHUB_ACTIONS": "true",
        "GRADLE_OPTS": "-Dorg.gradle.daemon=false",
        **{key: str(value) for key, value in dirs.items()},
    }
    if dependency:
        values = {
            "root": str(dependency),
            "subdirectory": str(dependency / str(plan.private_dependency_subdirectory)),
            "sha": str(plan.private_dependency_sha),
        }
        for key, source in plan.private_dependency_environment:
            env[key] = values[source]
    return env


def verify_private_dependency(
    state: Path,
    plan: AndroidValidationPlan,
    inherited: Mapping[str, str],
    contract: Mapping[str, Any] | None = None,
) -> Path | None:
    if not plan.requires_private_dependency:
        require(
            not inherited.get("CIW_ANDROID_PRIVATE_DEPENDENCY_PATH", ""),
            "private_dependency_rejected",
        )
        return None
    require(
        plan.private_dependency_id is not None
        and plan.private_dependency_contract_id is not None,
        "private_dependency_rejected",
    )
    relative = f"dependencies/{plan.private_dependency_id}"
    require(
        inherited.get("CIW_ANDROID_PRIVATE_DEPENDENCY_PATH") == relative,
        "private_dependency_rejected",
    )
    root = bounded_path(state.parent, relative)
    require(root.is_dir() and (root / ".git").is_dir(), "private_dependency_missing")
    for key in ("VERIFIED", "REMOTES_ERASED", "CREDENTIALS_ERASED"):
        require(
            inherited.get(f"CIW_ANDROID_PRIVATE_DEPENDENCY_{key}") == "true",
            "private_dependency_dirty",
        )
    require(
        git_output(
            root,
            ["rev-parse", "HEAD"],
            "private_dependency_rejected",
            inherited,
        )
        == plan.private_dependency_sha,
        "private_dependency_rejected",
    )
    symbolic = run_command(
        ["git", "symbolic-ref", "--quiet", "HEAD"],
        cwd=root,
        environment=isolated_git_environment(inherited),
        timeout_seconds=60,
        failure_code="private_dependency_dirty",
        check=False,
    )
    require(
        symbolic.returncode != 0
        and not git_output(root, ["remote"], "private_dependency_dirty", inherited),
        "private_dependency_dirty",
    )
    require(
        not git_output(
            root,
            ["status", "--porcelain=v1", "--untracked-files=all"],
            "private_dependency_dirty",
            inherited,
        ),
        "private_dependency_dirty",
    )
    config = (root / ".git/config").read_text(
        encoding="utf-8",
        errors="replace",
    ).casefold()
    require(
        not any(
            x in config
            for x in ("extraheader", "authorization", "credential.", "github.com/")
        ),
        "private_dependency_dirty",
    )
    required_paths = getattr(plan, "private_dependency_required_paths", ())
    if contract is not None:
        required_paths = contract["private_dependencies"][
            plan.private_dependency_contract_id
        ]["required_paths"]
    for relative_path in required_paths:
        path = bounded_path(root, relative_path)
        require(
            path.is_file() and not path.is_symlink(),
            "private_dependency_missing",
        )
    require(
        bounded_path(root, str(plan.private_dependency_subdirectory)).is_dir(),
        "private_dependency_missing",
    )
    return root


def _tree_hash(root: Path) -> str:
    if not root.exists():
        return hashlib.sha256(b"").hexdigest()
    require(root.is_dir() and not root.is_symlink(), "schema_drift")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "schema_drift")
        if path.is_file():
            digest.update(
                path.relative_to(root).as_posix().encode()
                + b"\0"
                + hashlib.sha256(path.read_bytes()).digest()
            )
    return digest.hexdigest()


def protected_hashes(root: Path, plan: AndroidValidationPlan) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in plan.protected_paths:
        path = bounded_path(root, relative)
        result[relative] = (
            hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file()
            else _tree_hash(path)
        )
    return result


def _failure(stage: str, profile: str) -> str:
    return {
        "compile": "compile_failed",
        "tests": "performance_failed" if profile == "performance" else "tests_failed",
        "lint": "lint_failed",
        "assemble": "assemble_failed",
        "schema-generation": "schema_drift",
        "schema-validation": "schema_drift",
        "consumer-script": "consumer_script_failed",
    }.get(stage, "command_failed")


def _argv(
    copy: Path,
    plan: AndroidValidationPlan,
    original: Sequence[str],
) -> list[str]:
    if original[0].startswith(":") or original[0] == "test":
        working = bounded_path(copy, plan.working_directory)
        result = [
            str(bounded_path(working, plan.gradle_wrapper_path)),
            *plan.fixed_gradle_arguments,
            *original,
        ]
        if plan.targeted_test_selector:
            result += ["--tests", plan.targeted_test_selector]
        return result
    return list(original)


def verify_debug_outputs(copy: Path, plan: AndroidValidationPlan) -> bool:
    if plan.output_mode != "debug-unsigned":
        return False
    for relative in plan.expected_debug_outputs:
        path = bounded_path(copy, relative)
        name = path.name.casefold()
        require(
            path.is_file()
            and not path.is_symlink()
            and name.endswith(".apk")
            and "debug" in name
            and "release" not in name
            and "signed" not in name,
            "debug_output_invalid",
        )
    require(not any(copy.rglob("*.aab")), "debug_output_invalid")
    return True


def execute_android_plan(
    source: Path,
    state: Path,
    plan: AndroidValidationPlan,
    contract: Mapping[str, Any],
    inherited: Mapping[str, str],
) -> AndroidValidationResult:
    verify_exact_source(source, plan.admitted_sha, inherited)
    copy = state / "android-source"
    copy_source(source, copy)
    dependency = verify_private_dependency(state, plan, inherited, contract)
    environment = execution_environment(plan, state, inherited, dependency)
    protected = protected_hashes(copy, plan)
    schemas = {
        path: _tree_hash(bounded_path(copy, path)) for path in plan.schema_paths
    }
    java, api = verify_toolchain(copy, state, plan, contract, inherited)
    gradle = verify_wrapper(copy, state, plan, environment)
    for command in plan.commands:
        run_command(
            _argv(copy, plan, command.argv),
            cwd=bounded_path(copy, plan.working_directory),
            environment=environment,
            timeout_seconds=plan.timeout_minutes * 60,
            failure_code=_failure(command.stage, plan.validation_profile),
            state_root=state,
            stage=command.stage,
        )
    debug = verify_debug_outputs(copy, plan)
    schema = plan.output_mode == "schema"
    if schema:
        require(
            schemas
            == {path: _tree_hash(bounded_path(copy, path)) for path in plan.schema_paths},
            "schema_drift",
        )
    require(protected == protected_hashes(copy, plan), "dirty_tree")
    allowed = set(contract["generated_cleanup_names"])
    for row in git_output(
        copy,
        ["status", "--porcelain=v1", "--untracked-files=all"],
        inherited=inherited,
    ).splitlines():
        require(
            any(name in Path(row[3:].split(" -> ")[-1]).parts for name in allowed),
            "dirty_tree",
        )
    handoff = None
    if plan.is_device_handoff:
        handoff = {
            "contract_version": contract["contract_version"],
            "source_sha": plan.admitted_sha,
            "device_family": str(plan.device_family),
            "request_id": str(plan.device_request_id),
            "status": contract["device_handoff"]["status"],
        }
    evidence = hashlib.sha256(
        f"{plan.admitted_sha}:{plan.task_profile}:{len(plan.commands)}".encode()
    ).hexdigest()[:24]
    return AndroidValidationResult(
        plan.admitted_sha,
        plan.validation_profile,
        plan.task_profile,
        java,
        api,
        gradle,
        len(plan.commands),
        plan.requires_private_dependency,
        debug,
        schema,
        True,
        "pending",
        plan.artifact_exception_id is not None,
        handoff,
        evidence,
    )


def _gradle_daemon_is_active(state: Path) -> bool:
    processes = run_command(
        ["ps", "-axo", "command="],
        cwd=state.parent,
        environment={"PATH": os.environ.get("PATH", "")},
        timeout_seconds=30,
        failure_code="cleanup_failed",
        check=False,
    ).stdout
    return any(
        "GradleDaemon" in row and str(state) in row
        for row in processes.splitlines()
    )


def _wait_for_gradle_daemon_exit(state: Path) -> None:
    deadline = time.monotonic() + GRADLE_DAEMON_CLEANUP_GRACE_SECONDS
    while _gradle_daemon_is_active(state):
        if time.monotonic() >= deadline:
            raise AndroidValidationError("cleanup_failed")
        time.sleep(GRADLE_DAEMON_CLEANUP_POLL_SECONDS)


def cleanup_android_state(state: Path, contract: Mapping[str, Any]) -> None:
    require(state.is_absolute(), "cleanup_failed")
    _wait_for_gradle_daemon_exit(state)
    for path in (state / "android-source", state / "android-validation"):
        remove_no_follow(path)
    require(
        all(
            not path.exists() and not path.is_symlink()
            for path in (state / "android-source", state / "android-validation")
        ),
        "cleanup_failed",
    )