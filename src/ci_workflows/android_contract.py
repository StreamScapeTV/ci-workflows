"""Checked-in Android validation contract, trust, and plan resolution."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .android_types import (
    AndroidCommand,
    AndroidValidationError,
    AndroidValidationPlan,
    AndroidValidationRequest,
    AndroidWrapperContract,
)

CONTRACT_PATH = Path("contracts/android-validation.json")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9.-]{2,63}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
PROFILES = {
    "toolchain-smoke", "compile", "unit-targeted", "unit-full", "performance",
    "lint", "assemble-debug", "room-schema", "consumer-script", "device-handoff",
}
STAGES = {"compile", "tests", "lint", "assemble", "schema-generation", "schema-validation", "consumer-script"}
PROHIBITED = {"docker", "dockerd", "podman", "buildah", "skopeo", "jib", "kubectl", "helm", "gradle", "adb", "fastlane"}
REQUIRED_FORBIDDEN = {
    "arbitrary_task", "arbitrary_command", "arguments", "shell", "callback",
    "jdk_url", "sdk_url", "runner", "runs_on", "runner_labels", "container_engine",
    "registry", "signing_identity", "keystore", "play_store", "secret_name",
    "database_url", "backend_url", "physical_device", "device_selector", "deployment",
    "mutable_ref", "artifact_upload", "release", "helm", "flux_target",
}


def require(condition: bool, code: str) -> None:
    if not condition:
        raise AndroidValidationError(code)


def safe_relative(value: Any, code: str = "invalid_input", *, allow_dot: bool = False) -> str:
    require(isinstance(value, str), code)
    candidate = value.strip()
    if allow_dot and candidate == ".":
        return "."
    path = PurePosixPath(candidate)
    require(bool(candidate) and not path.is_absolute() and ".." not in path.parts and "\\" not in candidate, code)
    require(all(part not in {"", "."} for part in path.parts), code)
    return path.as_posix()


def bounded_path(root: Path, relative: str) -> Path:
    boundary = root.resolve()
    if relative == ".":
        return boundary
    target = (boundary / Path(*PurePosixPath(relative).parts)).resolve(strict=False)
    require(boundary in target.parents, "invalid_input")
    current = boundary
    for part in target.relative_to(boundary).parts:
        current /= part
        require(not (current.exists() and current.is_symlink()), "invalid_input")
    return target


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_blob_sha1(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


def strings(value: Any, *, nonempty: bool = False, unique: bool = True) -> list[str]:
    require(isinstance(value, list) and (value or not nonempty), "invalid_input")
    require(all(isinstance(item, str) and item for item in value), "invalid_input")
    require(not unique or len(value) == len(set(value)), "invalid_input")
    return list(value)


def merged_task(consumer: Mapping[str, Any], task: Mapping[str, Any]) -> dict[str, Any]:
    defaults = consumer.get("defaults")
    require(isinstance(defaults, Mapping), "invalid_input")
    return {**defaults, **task}


def validate_wrapper(wrapper: Mapping[str, Any]) -> None:
    require(wrapper.get("mode") in {"standard-wrapper", "checked-in-launcher", "synthetic-smoke"}, "wrapper_invalid")
    version = wrapper.get("version")
    require(isinstance(version, str) and VERSION.fullmatch(version) is not None, "wrapper_invalid")
    safe_relative(wrapper.get("properties_path"), "wrapper_invalid")
    require(wrapper.get("distribution_url") == f"https://services.gradle.org/distributions/gradle-{version}-bin.zip", "wrapper_distribution_drift")
    digest = wrapper.get("distribution_sha256")
    require(digest is None or (isinstance(digest, str) and SHA256.fullmatch(digest) is not None), "wrapper_distribution_drift")
    for key in ("tracked_blob_sha1", "properties_blob_sha1"):
        value = wrapper.get(key)
        require(value is None or (isinstance(value, str) and FULL_SHA.fullmatch(value) is not None), "wrapper_invalid")
    if wrapper.get("mode") == "checked-in-launcher":
        require(digest is not None and wrapper.get("tracked_blob_sha1") is not None, "wrapper_invalid")


def validate_command(command: Mapping[str, Any], forbidden: Sequence[str]) -> None:
    require(set(command) == {"stage", "argv"} and command.get("stage") in STAGES, "task_profile_rejected")
    argv = strings(command.get("argv"), nonempty=True, unique=False)
    require(len(argv) <= 20 and all(len(item) <= 256 and "\n" not in item and "\r" not in item for item in argv), "task_profile_rejected")
    serialized = "\0".join(argv)
    require(argv[0].casefold() not in PROHIBITED and not any(fragment in serialized for fragment in forbidden), "task_profile_rejected")
    for item in argv:
        if item.startswith(":"):
            require(re.fullmatch(r":[A-Za-z0-9_-]+(?::[A-Za-z0-9_-]+)*", item) is not None, "task_profile_rejected")
    if argv[0] in {"bash", "python", "python3"}:
        require(len(argv) >= 2 and argv[1].startswith("scripts/"), "task_profile_rejected")


def validate_task(contract: Mapping[str, Any], repository: str, consumer: Mapping[str, Any], name: str, raw: Mapping[str, Any]) -> None:
    require(IDENTIFIER.fullmatch(name) is not None, "task_profile_rejected")
    task = merged_task(consumer, raw)
    profile = task.get("validation_profile")
    require(profile in PROFILES, "unsupported_profile")
    safe_relative(task.get("working_directory"), allow_dot=True)
    safe_relative(task.get("gradle_wrapper_path"), "wrapper_invalid")
    require(task.get("wrapper_id") in contract["wrappers"], "wrapper_invalid")
    commands = task.get("commands")
    require(isinstance(commands, list), "task_profile_rejected")
    forbidden = strings(contract.get("forbidden_argument_fragments"), nonempty=True)
    for command in commands:
        require(isinstance(command, Mapping), "task_profile_rejected")
        validate_command(command, forbidden)
    if profile in {"toolchain-smoke", "device-handoff"}:
        require(not commands, "task_profile_rejected")
    if profile == "unit-targeted":
        require(len(commands) == 1 and commands[0]["stage"] == "tests" and "--tests" not in commands[0]["argv"], "task_profile_rejected")
    script = task.get("consumer_script_path")
    if profile == "consumer-script":
        script = safe_relative(script, "task_profile_rejected")
        require(any(row["argv"][:2] == ["bash", script] for row in commands), "task_profile_rejected")
    else:
        require(script in {None, ""}, "task_profile_rejected")
    for key in ("protected_paths", "schema_paths", "expected_debug_outputs"):
        for path in strings(task.get(key)):
            safe_relative(path)
    if profile == "assemble-debug":
        outputs = strings(task.get("expected_debug_outputs"), nonempty=True)
        require(all(path.endswith(".apk") and "debug" in path.casefold() for path in outputs), "debug_output_invalid")
    else:
        require(not task.get("expected_debug_outputs") or profile == "consumer-script", "debug_output_invalid")
    dependency_id = task.get("private_dependency_contract_id")
    if dependency_id is not None:
        require(dependency_id in contract["private_dependencies"], "private_dependency_rejected")
        dependency = contract["private_dependencies"][dependency_id]
        require(repository in dependency["allowed_consumers"], "private_dependency_rejected")
        require(contract["profiles"][profile]["allows_private_dependency"] is True, "private_dependency_rejected")


def load_android_contract(root: Path) -> Mapping[str, Any]:
    """Load and validate the complete checked-in Android contract."""

    try:
        contract = json.loads((root / CONTRACT_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AndroidValidationError("invalid_input") from error
    require(isinstance(contract, Mapping), "invalid_input")
    require((contract.get("schema_version"), contract.get("contract_version"), contract.get("organization")) == (1, "1.0.0", "StreamScapeTV"), "invalid_input")
    require((contract.get("workflow_api"), contract.get("stable_check_name")) == ("validation.android", "CI / Android validation"), "invalid_input")
    require((contract.get("planner_runner_profile"), contract.get("execution_runner_profile")) == ("portable", "mobile"), "invalid_input")
    require(contract.get("hard_timeout_minutes") == 120 and contract.get("cache_mode") == "disabled" and contract.get("artifact_policy") == "zero-default", "invalid_input")
    toolchain = contract.get("toolchain")
    require(isinstance(toolchain, Mapping), "toolchain_mismatch")
    require((toolchain.get("java_major"), toolchain.get("javac_major"), toolchain.get("android_api"), toolchain.get("command_line_tools_version"), toolchain.get("build_tools_version"), toolchain.get("locale"), toolchain.get("gradle_daemon")) == (25, 25, 37, "14742923", "37.0.0", "C.UTF-8", False), "toolchain_mismatch")
    require(strings(toolchain.get("packages"), nonempty=True) == ["platform-tools", "platforms;android-37.0", "build-tools;37.0.0"], "sdk_package_missing")
    profiles = contract.get("profiles")
    require(isinstance(profiles, Mapping) and set(profiles) == PROFILES, "unsupported_profile")
    for name, profile in profiles.items():
        require(isinstance(profile, Mapping) and isinstance(profile.get("timeout_minutes"), int) and 1 <= profile["timeout_minutes"] <= 120, "invalid_input")
        require(profile.get("output_mode") in {"none", "test", "lint", "debug-unsigned", "schema", "consumer-owned", "handoff-only"}, "invalid_input")
        require(set(strings(profile.get("allowed_source_trust"), nonempty=True)) <= {"trusted-pr", "trusted-exact"}, "source_trust_rejected")
        if name == "device-handoff":
            require(profile.get("requires_gradle") is False and profile.get("output_mode") == "handoff-only", "invalid_input")
    require(contract.get("fixed_gradle_arguments") == ["--no-daemon", "--console=plain", "--warning-mode=all", "--stacktrace"], "task_profile_rejected")
    require(isinstance(contract.get("test_selector_regex"), str), "test_filter_rejected")
    re.compile(contract["test_selector_regex"])
    wrappers = contract.get("wrappers")
    require(isinstance(wrappers, Mapping) and wrappers, "wrapper_invalid")
    for key, wrapper in wrappers.items():
        require(IDENTIFIER.fullmatch(str(key)) is not None and isinstance(wrapper, Mapping), "wrapper_invalid")
        validate_wrapper(wrapper)
    dependencies = contract.get("private_dependencies")
    require(isinstance(dependencies, Mapping), "private_dependency_rejected")
    for key, dependency in dependencies.items():
        require(IDENTIFIER.fullmatch(str(key)) is not None and isinstance(dependency, Mapping), "private_dependency_rejected")
        require(REPOSITORY.fullmatch(str(dependency.get("repository", ""))) is not None, "private_dependency_rejected")
        require(all(FULL_SHA.fullmatch(item) for item in strings(dependency.get("allowed_shas"), nonempty=True)), "private_dependency_rejected")
        safe_relative(dependency.get("expected_subdirectory"), "private_dependency_rejected")
        for path in strings(dependency.get("required_paths"), nonempty=True):
            safe_relative(path, "private_dependency_rejected")
        require(all(REPOSITORY.fullmatch(item) for item in strings(dependency.get("allowed_consumers"), nonempty=True)), "private_dependency_rejected")
    exceptions = contract.get("artifact_exceptions")
    require(isinstance(exceptions, Mapping), "artifact_policy_failed")
    for key, exception in exceptions.items():
        require(IDENTIFIER.fullmatch(str(key)) is not None and isinstance(exception, Mapping), "artifact_policy_failed")
        require(set(strings(exception.get("forbidden_extensions"), nonempty=True)) >= {".apk", ".aab"}, "artifact_policy_failed")
        require(1 <= exception.get("maximum_files", 0) <= 1000 and 1 <= exception.get("maximum_bytes", 0) <= 64 * 1024 * 1024, "artifact_policy_failed")
    consumers = contract.get("consumers")
    require(isinstance(consumers, Mapping) and consumers, "invalid_input")
    for repository, consumer in consumers.items():
        require(REPOSITORY.fullmatch(repository) is not None and isinstance(consumer, Mapping), "invalid_input")
        defaults = consumer.get("defaults")
        require(isinstance(defaults, Mapping) and defaults.get("wrapper_id") in wrappers, "wrapper_invalid")
        safe_relative(defaults.get("working_directory"), allow_dot=True)
        safe_relative(defaults.get("gradle_wrapper_path"), "wrapper_invalid")
        for key in ("protected_paths", "schema_paths"):
            for path in strings(defaults.get(key)):
                safe_relative(path)
        tasks = consumer.get("tasks")
        require(isinstance(tasks, Mapping) and tasks, "invalid_input")
        for name, task in tasks.items():
            require(isinstance(task, Mapping), "invalid_input")
            validate_task(contract, repository, consumer, str(name), task)
    require(REQUIRED_FORBIDDEN <= set(strings(contract.get("forbidden_inputs"), nonempty=True)), "invalid_input")
    cleanup = contract.get("cleanup")
    require(isinstance(cleanup, Mapping) and cleanup and all(value is True for value in cleanup.values()), "cleanup_failed")
    return contract


def source_trust_from_environment(environment: Mapping[str, str]) -> str:
    if environment.get("GITHUB_EVENT_NAME") != "pull_request":
        return "trusted-exact"
    try:
        event = json.loads(Path(environment.get("GITHUB_EVENT_PATH", "")).read_text(encoding="utf-8"))
        head_repository = event["pull_request"]["head"]["repo"]["full_name"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise AndroidValidationError("invalid_input") from error
    return "trusted-pr" if head_repository == environment.get("GITHUB_REPOSITORY") else "untrusted-fork"


def optional_input(environment: Mapping[str, str], name: str) -> str | None:
    value = environment.get(name, "").strip()
    return value or None


def request_from_environment(environment: Mapping[str, str], contract: Mapping[str, Any] | None = None) -> AndroidValidationRequest:
    repository = environment.get("GITHUB_REPOSITORY", "").strip()
    sha = environment.get("INPUT_ADMITTED_SHA", "").strip()
    profile = environment.get("INPUT_VALIDATION_PROFILE", "").strip()
    task = environment.get("INPUT_TASK_PROFILE", "").strip()
    working = environment.get("INPUT_WORKING_DIRECTORY", ".").strip() or "."
    wrapper = environment.get("INPUT_GRADLE_WRAPPER_PATH", "gradlew").strip() or "gradlew"
    require(REPOSITORY.fullmatch(repository) is not None and FULL_SHA.fullmatch(sha) is not None, "invalid_input")
    require(profile in PROFILES and IDENTIFIER.fullmatch(task) is not None, "unsupported_profile")
    safe_relative(working, allow_dot=True)
    safe_relative(wrapper, "wrapper_invalid")
    if contract is not None:
        for forbidden in contract["forbidden_inputs"]:
            require(not environment.get("INPUT_" + str(forbidden).upper().replace("-", "_"), "").strip(), "invalid_input")
    return AndroidValidationRequest(
        repository, sha, profile, task, working, wrapper,
        optional_input(environment, "INPUT_TARGETED_TEST_SELECTOR"),
        optional_input(environment, "INPUT_CONSUMER_SCRIPT_PROFILE"),
        optional_input(environment, "INPUT_PRIVATE_DEPENDENCY_CONTRACT_ID"),
        optional_input(environment, "INPUT_PRIVATE_DEPENDENCY_SHA"),
        optional_input(environment, "INPUT_ARTIFACT_EXCEPTION_ID"),
        optional_input(environment, "INPUT_DEVICE_FAMILY"),
        optional_input(environment, "INPUT_DEVICE_REQUEST_ID"),
        source_trust_from_environment(environment),
    )


def commands(value: Sequence[Mapping[str, Any]]) -> tuple[AndroidCommand, ...]:
    return tuple(AndroidCommand(str(row["stage"]), tuple(str(item) for item in row["argv"])) for row in value)


def resolve_validation_plan(contract: Mapping[str, Any], request: AndroidValidationRequest) -> AndroidValidationPlan:
    require(request.repository in contract["consumers"], "task_profile_rejected")
    consumer = contract["consumers"][request.repository]
    require(request.task_profile in consumer["tasks"], "task_profile_rejected")
    task = merged_task(consumer, consumer["tasks"][request.task_profile])
    require(task["validation_profile"] == request.validation_profile, "unsupported_profile")
    profile = contract["profiles"][request.validation_profile]
    require(request.source_trust in profile["allowed_source_trust"], "source_trust_rejected")
    require((request.working_directory, request.gradle_wrapper_path) == (task["working_directory"], task["gradle_wrapper_path"]), "task_profile_rejected")
    selector = request.targeted_test_selector
    if request.validation_profile == "unit-targeted":
        require(selector is not None and re.fullmatch(contract["test_selector_regex"], selector) is not None, "test_filter_rejected")
    else:
        require(selector is None, "test_filter_rejected")
    script = task.get("consumer_script_path")
    if request.validation_profile == "consumer-script":
        require(request.consumer_script_profile == request.task_profile and isinstance(script, str), "task_profile_rejected")
    else:
        require(request.consumer_script_profile is None, "task_profile_rejected")
    dependency_contract_id = task.get("private_dependency_contract_id")
    dependency_repository = dependency_sha = dependency_subdirectory = dependency_id = None
    if dependency_contract_id is None:
        require(request.private_dependency_contract_id is None and request.private_dependency_sha is None, "private_dependency_rejected")
    else:
        require(request.private_dependency_contract_id == dependency_contract_id, "private_dependency_rejected")
        dependency = contract["private_dependencies"][dependency_contract_id]
        require(request.private_dependency_sha in dependency["allowed_shas"], "private_dependency_rejected")
        require(request.source_trust in {"trusted-pr", "trusted-exact"}, "private_dependency_rejected")
        dependency_repository = dependency["repository"]
        dependency_sha = request.private_dependency_sha
        dependency_subdirectory = dependency["expected_subdirectory"]
        dependency_id = dependency["dependency_id"]
    exception = request.artifact_exception_id
    if exception is not None:
        require(exception in contract["artifact_exceptions"] and request.validation_profile in contract["artifact_exceptions"][exception]["allowed_profiles"], "artifact_policy_failed")
    if request.validation_profile == "device-handoff":
        handoff = contract["device_handoff"]
        require(request.device_family in handoff["families"], "invalid_input")
        require(request.device_request_id is not None and re.fullmatch(handoff["request_id_regex"], request.device_request_id) is not None, "invalid_input")
    else:
        require(request.device_family is None and request.device_request_id is None, "invalid_input")
    wrapper = contract["wrappers"][task["wrapper_id"]]
    return AndroidValidationPlan(
        repository=request.repository, admitted_sha=request.admitted_sha,
        validation_profile=request.validation_profile, task_profile=request.task_profile,
        runner_profile=contract["execution_runner_profile"], planner_runner_profile=contract["planner_runner_profile"],
        timeout_minutes=profile["timeout_minutes"], source_trust=request.source_trust,
        working_directory=task["working_directory"], gradle_wrapper_path=task["gradle_wrapper_path"],
        wrapper=AndroidWrapperContract(wrapper["mode"], wrapper["version"], wrapper["properties_path"], wrapper["distribution_url"], wrapper.get("distribution_sha256"), wrapper.get("tracked_blob_sha1"), wrapper.get("properties_blob_sha1")),
        commands=commands(task["commands"]), fixed_gradle_arguments=tuple(contract["fixed_gradle_arguments"]),
        targeted_test_selector=selector, consumer_script_path=script,
        private_dependency_contract_id=dependency_contract_id, private_dependency_repository=dependency_repository,
        private_dependency_sha=dependency_sha, private_dependency_subdirectory=dependency_subdirectory,
        private_dependency_id=dependency_id, artifact_exception_id=exception,
        protected_paths=tuple(task["protected_paths"]), schema_paths=tuple(task["schema_paths"]),
        expected_debug_outputs=tuple(task["expected_debug_outputs"]), output_mode=profile["output_mode"],
        device_family=request.device_family, device_request_id=request.device_request_id,
    )
