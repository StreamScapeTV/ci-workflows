"""Deterministic Flutter contract parsing and source authority checks."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping

from .flutter_types import (
    FlutterCommand,
    FlutterPin,
    FlutterPlan,
    FlutterProfile,
    FlutterRequest,
    FlutterStage,
    FlutterToolchain,
    RunnerCapability,
)

EXACT_VERSION = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$"
)
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHORT_OR_FULL_SHA = re.compile(r"^[0-9a-f]{7,40}$")
SAFE_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
JDK_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+\+[0-9]+$")
GRADLE_VERSION = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")
FORBIDDEN_VERSION_TOKENS = (
    "stable", "beta", "dev", "master", "main", "^", "~", ">", "<", "*", "x", "X", " "
)
FORBIDDEN_COMMAND_TOKENS = (
    "--release", "--profile", "--device-id", "--flavor", "flutter upgrade",
    "flutter channel", "flutter precache", "flutter config", "publish", "upload",
    "testflight", "app-store", "notar", "security import", "keychain", "provision",
)
ALLOWED_PROFILES = tuple(profile.value for profile in FlutterProfile)
ALLOWED_RUNNERS = tuple(value.value for value in RunnerCapability)
ALLOWED_TRUST = {"untrusted-fork", "trusted-pr", "trusted-exact"}
PIN_SOURCES = {".fvmrc", ".flutter-version", "contract"}
ANDROID_PROFILES = {
    FlutterProfile.QUALITY.value,
    FlutterProfile.CANONICAL_GATE.value,
    FlutterProfile.ANDROID_DEBUG.value,
    FlutterProfile.COMPATIBILITY_SMOKE.value,
}
EXPECTED_TOP_LEVEL_FIELDS = {
    "schema_version", "contract_version", "organization", "workflow_api",
    "stable_check_name", "generation", "setup", "toolchains", "profiles",
    "commands", "consumer_contracts", "source_trust", "forbidden_inputs",
    "cleanup", "failure_codes",
}
EXPECTED_GENERATION_FIELDS = {
    "encoding", "indent", "sort_keys", "trailing_newline", "runtime_fixture_pattern"
}
EXPECTED_SETUP_FIELDS = {
    "action", "immutable", "caller_download_url", "caller_runtime",
    "jdk_action", "jdk_distribution", "caller_jdk",
}
EXPECTED_TOOLCHAIN_FIELDS = {
    "dart_version", "framework_revision", "engine_revision", "gradle_version",
    "jdk_distribution", "jdk_version", "java_version", "java_runtime_version",
    "java_vendor", "javac_version",
}
EXPECTED_PROFILE_FIELDS = {
    "runner", "install_required", "workspace_profile", "timeout_minutes",
    "allowed_source_trust", "stages",
}
EXPECTED_COMMAND_FIELDS = {"id", "stage", "argv", "working_directory", "expected_outputs"}
EXPECTED_CONSUMER_FIELDS = {
    "repository", "flutter_version", "pin_sources", "allow_contract_pin", "profiles"
}
EXPECTED_CONSUMER_PROFILE_FIELDS = {
    "commands", "gate_path", "node_composition", "device_handoff"
}
EXPECTED_TOOLCHAIN_IDS = {"3.41.4", "3.44.6"}
EXPECTED_COMMAND_IDS = {
    "android-apk-debug", "android-apk-directus", "android-appbundle-debug",
    "directus-compatibility", "directus-gate", "finance-compatibility", "finance-gate",
    "flutter-analyze", "flutter-tests", "ios-simulator-debug", "ios-simulator-unsigned",
    "pod-install-deployment", "pub-restore", "source-diff-audit", "synthetic-compatibility",
    "synthetic-gate",
}
EXPECTED_CONSUMER_IDS = {"directus-canonical", "finance-embedded-web", "synthetic-smoke"}
EXPECTED_FAILURE_CODES = {
    "invalid_input", "forbidden_input", "contract_invalid", "invalid_runtime_source",
    "missing_runtime_pin", "runtime_pin_mismatch", "unsupported_runtime",
    "runtime_identity_invalid", "runtime_mismatch", "dart_mismatch", "jdk_mismatch",
    "gradle_mismatch", "missing_lockfile", "source_authority_invalid", "path_rejected",
    "gate_path_rejected", "consumer_contract_rejected", "profile_consumer_mismatch",
    "source_trust_rejected", "source_audit_install_rejected", "platform_runner_mismatch",
    "device_boundary_rejected", "command_boundary_rejected", "stage_order_invalid",
    "command_profile_rejected", "node_composition_failed", "command_failed",
    "lockfile_drift", "output_missing", "dirty_source", "pub_cache_rejected",
    "persistent_pub_cache_changed", "cleanup_failed", "generated_contract_drift",
}
EXPECTED_FORBIDDEN_INPUTS = {
    "arbitrary_command", "arguments", "callback", "container_engine", "database_url",
    "deployment", "device", "engine", "environment_file", "flux_target", "kubeconfig",
    "matrix", "mutable_ref", "namespace", "package_manager", "registry", "runner",
    "runner_labels", "runs_on", "secret", "shell", "signing", "store_operation", "upload",
    "download_url", "runtime", "jdk", "java_home", "pub_cache", "gradle_version",
}


class FlutterValidationError(RuntimeError):
    """Stable bounded validation failure with deterministic terminal projection."""

    def __init__(self, code: str, cleanup_code: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.primary_code = code
        self.cleanup_code = cleanup_code

    def output_values(self) -> dict[str, str]:
        return {
            "result": "failure",
            "failure_code": self.primary_code,
            "primary_failure_code": self.primary_code,
            "cleanup_failure_code": self.cleanup_code,
            "cleanup_result": "failure" if self.cleanup_code else "not-run",
        }


def fail(code: str) -> None:
    raise FlutterValidationError(code)


def _exact_dict(value: object, keys: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        fail(code)
    return dict(value)


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _canonicalize_contract(raw: Mapping[str, Any]) -> dict[str, Any]:
    value = json.loads(json.dumps(raw))
    value["source_trust"] = sorted(value["source_trust"])
    value["failure_codes"] = sorted(value["failure_codes"])
    value["forbidden_inputs"] = sorted(value["forbidden_inputs"])
    for profile in value["profiles"].values():
        profile["allowed_source_trust"] = sorted(profile["allowed_source_trust"])
    return value


def runtime_fixture(toolchain: Mapping[str, Any], version: str) -> dict[str, str]:
    return {
        "dartSdkVersion": str(toolchain["dart_version"]),
        "engineRevision": str(toolchain["engine_revision"]),
        "frameworkRevision": str(toolchain["framework_revision"]),
        "frameworkVersion": version,
        "gradleVersion": str(toolchain["gradle_version"]),
        "javaRuntimeVersion": str(toolchain["java_runtime_version"]),
        "javaVendor": str(toolchain["java_vendor"]),
        "javaVersion": str(toolchain["java_version"]),
        "javacVersion": str(toolchain["javac_version"]),
        "jdkDistribution": str(toolchain["jdk_distribution"]),
        "jdkVersion": str(toolchain["jdk_version"]),
    }


def generated_flutter_files(root: Path) -> dict[Path, bytes]:
    contract_path = root / "contracts" / "flutter-validation.json"
    try:
        raw = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("contract_invalid")
    validate_contract(raw)
    canonical = _canonicalize_contract(raw)
    pattern = canonical["generation"]["runtime_fixture_pattern"]
    result = {contract_path: canonical_json_bytes(canonical)}
    for version in sorted(canonical["toolchains"]):
        relative = pattern.format(flutter_version=version)
        result[root / relative] = canonical_json_bytes(
            runtime_fixture(canonical["toolchains"][version], version)
        )
    return result


def generate_flutter_contract_files(root: Path, *, check: bool) -> tuple[str, ...]:
    files = generated_flutter_files(root)
    changed: list[str] = []
    for path, expected in files.items():
        current = path.read_bytes() if path.is_file() and not path.is_symlink() else b""
        if current == expected:
            continue
        changed.append(path.relative_to(root).as_posix())
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_bytes(expected)
            os.replace(temporary, path)
    if check and changed:
        fail("generated_contract_drift")
    return tuple(sorted(changed))


def load_flutter_contract(root: Path) -> dict[str, Any]:
    path = root / "contracts" / "flutter-validation.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("contract_invalid")
    validate_contract(raw)
    return raw


def exact_version(value: object) -> str:
    if not isinstance(value, str) or EXACT_VERSION.fullmatch(value) is None:
        fail("invalid_runtime_source")
    if any(token in value for token in FORBIDDEN_VERSION_TOKENS):
        fail("invalid_runtime_source")
    return value


def safe_relative(value: object, *, allow_dot: bool = True) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        fail("invalid_input")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".."} for part in path.parts):
        fail("invalid_input")
    if not allow_dot and path == Path("."):
        fail("invalid_input")
    return path


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        fail("path_rejected")


def bounded_path(root: Path, relative: Path, *, must_exist: bool = False) -> Path:
    """Validate a lexical child without following any existing symlink."""
    base = Path(os.path.abspath(root))
    root_meta = _lstat(base)
    if root_meta is not None and (
        stat.S_ISLNK(root_meta.st_mode) or not stat.S_ISDIR(root_meta.st_mode)
    ):
        fail("path_rejected")
    candidate = base.joinpath(*relative.parts)
    current = base
    final_meta: os.stat_result | None = root_meta
    for index, part in enumerate(relative.parts):
        current /= part
        metadata = _lstat(current)
        final_meta = metadata
        if metadata is None:
            break
        if stat.S_ISLNK(metadata.st_mode):
            fail("path_rejected")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            fail("path_rejected")
    if must_exist and final_meta is None:
        fail("path_rejected")
    return candidate


def regular_file(root: Path, relative: str, code: str) -> Path:
    path = bounded_path(root, safe_relative(relative, allow_dot=False))
    metadata = _lstat(path)
    if metadata is None or not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        fail(code)
    return path


def _read_plain_pin(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        fail("missing_runtime_pin")
    if "\n" in text.rstrip("\n") or text.count("\n") > 1:
        fail("invalid_runtime_source")
    return exact_version(text.strip())


def _read_fvm_pin(path: Path) -> str:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("invalid_runtime_source")
    value = _exact_dict(raw, {"flutter"}, "invalid_runtime_source")["flutter"]
    return exact_version(value)


def discover_flutter_pin(source_root: Path, consumer: Mapping[str, Any]) -> FlutterPin:
    declared = consumer["pin_sources"]
    if not isinstance(declared, list) or not declared:
        fail("missing_runtime_pin")
    versions: list[tuple[str, str]] = []
    recognized = {".fvmrc", ".flutter-version"}
    for name in recognized:
        candidate = source_root / name
        if _lstat(candidate) is not None:
            path = regular_file(source_root, name, "invalid_runtime_source")
            versions.append((name, _read_fvm_pin(path) if name == ".fvmrc" else _read_plain_pin(path)))
    for source in declared:
        if source in recognized and not any(name == source for name, _ in versions):
            fail("missing_runtime_pin")
        if source == "contract":
            if consumer.get("allow_contract_pin") is not True:
                fail("invalid_runtime_source")
            versions.append(("contract", exact_version(consumer.get("flutter_version"))))
        elif source not in recognized:
            fail("invalid_runtime_source")
    if not versions:
        fail("missing_runtime_pin")
    values = {version for _, version in versions}
    if len(values) != 1:
        fail("runtime_pin_mismatch")
    return FlutterPin(next(iter(values)), tuple(sorted(name for name, _ in versions)))


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_authority_hashes(source_root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name in (".fvmrc", ".flutter-version", "pubspec.yaml", "pubspec.lock"):
        candidate = source_root / name
        if _lstat(candidate) is not None:
            path = regular_file(source_root, name, "source_authority_invalid")
            hashes[name] = file_sha256(path)
    if "pubspec.yaml" not in hashes or "pubspec.lock" not in hashes:
        fail("missing_lockfile")
    return hashes


def checked_in_script(source_root: Path, value: object) -> Path:
    relative = safe_relative(value, allow_dot=False)
    path = bounded_path(source_root, relative)
    metadata = _lstat(path)
    if metadata is None or not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        fail("gate_path_rejected")
    try:
        first = path.read_text(encoding="utf-8", errors="strict")[:4096]
    except (OSError, UnicodeError):
        fail("gate_path_rejected")
    if "\x00" in first:
        fail("gate_path_rejected")
    return path


def parse_runtime_identity(output: str, toolchain: FlutterToolchain) -> dict[str, str]:
    try:
        raw = json.loads(output)
    except json.JSONDecodeError:
        fail("runtime_identity_invalid")
    if not isinstance(raw, dict):
        fail("runtime_identity_invalid")
    required = {"frameworkVersion", "dartSdkVersion", "frameworkRevision", "engineRevision"}
    if not required.issubset(raw):
        fail("runtime_identity_invalid")
    values = {key: raw[key] for key in required}
    if any(not isinstance(value, str) for value in values.values()):
        fail("runtime_identity_invalid")
    if values["frameworkVersion"] != toolchain.flutter_version:
        fail("runtime_mismatch")
    dart_version = values["dartSdkVersion"].split(" ", 1)[0]
    if dart_version != toolchain.dart_version:
        fail("dart_mismatch")
    framework = values["frameworkRevision"]
    if len(toolchain.framework_revision) == 40:
        if framework != toolchain.framework_revision:
            fail("runtime_mismatch")
    elif not framework.startswith(toolchain.framework_revision):
        fail("runtime_mismatch")
    if toolchain.engine_revision and values["engineRevision"] != toolchain.engine_revision:
        fail("runtime_mismatch")
    return {
        "flutter_version": values["frameworkVersion"],
        "dart_version": dart_version,
        "framework_revision": framework,
        "engine_revision": values["engineRevision"],
    }


def parse_jdk_identity(java_output: str, javac_output: str, toolchain: FlutterToolchain) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line in java_output.splitlines():
        match = re.match(r"^\s*(java\.(?:version|runtime\.version|vendor))\s*=\s*(.+?)\s*$", line)
        if match:
            properties[match.group(1)] = match.group(2)
    javac_match = re.search(r"(?:^|\s)javac\s+([^\s]+)", javac_output.strip())
    if (
        properties.get("java.version") != toolchain.java_version
        or properties.get("java.runtime.version") != toolchain.java_runtime_version
        or properties.get("java.vendor") != toolchain.java_vendor
        or javac_match is None
        or javac_match.group(1) != toolchain.javac_version
    ):
        fail("jdk_mismatch")
    return {
        "java_version": properties["java.version"],
        "java_runtime_version": properties["java.runtime.version"],
        "java_vendor": properties["java.vendor"],
        "javac_version": javac_match.group(1),
    }


def _toolchain(contract: Mapping[str, Any], version: str) -> FlutterToolchain:
    toolchains = contract["toolchains"]
    if version not in toolchains:
        fail("unsupported_runtime")
    raw = toolchains[version]
    return FlutterToolchain(
        flutter_version=version,
        dart_version=exact_version(raw["dart_version"]),
        framework_revision=str(raw["framework_revision"]),
        engine_revision=str(raw["engine_revision"]),
        setup_action=str(contract["setup"]["action"]),
        gradle_version=str(raw["gradle_version"]),
        jdk_distribution=str(raw["jdk_distribution"]),
        jdk_version=str(raw["jdk_version"]),
        java_version=str(raw["java_version"]),
        java_runtime_version=str(raw["java_runtime_version"]),
        java_vendor=str(raw["java_vendor"]),
        javac_version=str(raw["javac_version"]),
        jdk_setup_action=str(contract["setup"]["jdk_action"]),
    )


def _command(raw: Mapping[str, Any]) -> FlutterCommand:
    row = _exact_dict(raw, EXPECTED_COMMAND_FIELDS, "contract_invalid")
    identifier = row["id"]
    if not isinstance(identifier, str) or SAFE_ID.fullmatch(identifier) is None:
        fail("contract_invalid")
    try:
        stage = FlutterStage(row["stage"])
    except (TypeError, ValueError):
        fail("contract_invalid")
    argv = row["argv"]
    if not isinstance(argv, list) or not argv or any(not isinstance(item, str) or not item for item in argv):
        fail("contract_invalid")
    joined = " ".join(argv).lower()
    if any(token in joined for token in FORBIDDEN_COMMAND_TOKENS) or any(item.lower() == "deploy" for item in argv):
        fail("command_boundary_rejected")
    if argv[0] in {"sh", "zsh", "pwsh", "powershell", "cmd", "sudo", "curl", "wget"}:
        fail("command_boundary_rejected")
    working_directory = str(row["working_directory"])
    safe_relative(working_directory)
    expected = row["expected_outputs"]
    if not isinstance(expected, list) or any(not isinstance(item, str) for item in expected):
        fail("contract_invalid")
    for output in expected:
        try:
            safe_relative(output, allow_dot=False)
        except FlutterValidationError:
            fail("path_rejected")
    return FlutterCommand(identifier, stage, tuple(argv), working_directory, tuple(expected))


def build_plan(contract: Mapping[str, Any], request: FlutterRequest, source_root: Path | None) -> FlutterPlan:
    if request.source_trust not in ALLOWED_TRUST or SOURCE_SHA.fullmatch(request.admitted_sha) is None or REPOSITORY.fullmatch(request.repository) is None:
        fail("invalid_input")
    consumers = contract["consumer_contracts"]
    if request.consumer_contract not in consumers:
        fail("consumer_contract_rejected")
    consumer = consumers[request.consumer_contract]
    if consumer["repository"] != request.repository:
        fail("consumer_contract_rejected")
    profile_key = request.validation_profile.value
    if profile_key not in consumer["profiles"]:
        fail("profile_consumer_mismatch")
    profile = contract["profiles"][profile_key]
    if request.source_trust not in profile["allowed_source_trust"]:
        fail("source_trust_rejected")
    runner = RunnerCapability(profile["runner"])
    install_required = bool(profile["install_required"])
    pin = discover_flutter_pin(source_root, consumer) if source_root is not None else None
    version = pin.version if pin is not None else exact_version(consumer["flutter_version"])
    toolchain = _toolchain(contract, version)
    commands = tuple(_command(contract["commands"][identifier]) for identifier in consumer["profiles"][profile_key]["commands"])
    stage_values = tuple(FlutterStage(value) for value in profile["stages"])
    command_stages = tuple(command.stage for command in commands)
    expected_stages = tuple(
        stage for stage in stage_values
        if stage not in {
            FlutterStage.JDK_VERIFY, FlutterStage.RUNTIME_VERIFY, FlutterStage.GRADLE_VERIFY,
            FlutterStage.CLEANUP, FlutterStage.DEVICE_HANDOFF,
        }
    )
    collapsed_stages = tuple(stage for index, stage in enumerate(command_stages) if index == 0 or command_stages[index - 1] != stage)
    if collapsed_stages != expected_stages:
        fail("stage_order_invalid")
    gate_path = consumer["profiles"][profile_key].get("gate_path")
    if source_root is not None and gate_path:
        checked_in_script(source_root, gate_path)
    node_composition = consumer["profiles"][profile_key].get("node_composition")
    device_handoff = consumer["profiles"][profile_key].get("device_handoff")
    if request.validation_profile is FlutterProfile.DEVICE_HANDOFF:
        if runner is not RunnerCapability.PORTABLE or install_required or not isinstance(device_handoff, dict):
            fail("device_boundary_rejected")
    if request.validation_profile is FlutterProfile.IOS_SIMULATOR and runner is not RunnerCapability.APPLE:
        fail("platform_runner_mismatch")
    if request.validation_profile.value in ANDROID_PROFILES and runner is not RunnerCapability.MOBILE:
        fail("platform_runner_mismatch")
    if request.validation_profile is FlutterProfile.SOURCE_AUDIT and (runner is not RunnerCapability.PORTABLE or install_required):
        fail("source_audit_install_rejected")
    return FlutterPlan(
        request=request,
        runner_profile=runner,
        install_required=install_required,
        workspace_profile=str(profile["workspace_profile"]),
        timeout_minutes=int(profile["timeout_minutes"]),
        pin=pin,
        toolchain=toolchain,
        stages=stage_values,
        commands=commands,
        node_composition=node_composition if isinstance(node_composition, dict) else None,
        gate_path=str(gate_path) if gate_path else None,
        device_handoff=device_handoff if isinstance(device_handoff, dict) else None,
    )


def validate_contract(raw: object) -> None:
    contract = _exact_dict(raw, EXPECTED_TOP_LEVEL_FIELDS, "contract_invalid")
    if contract["schema_version"] != 1 or contract["contract_version"] != "1.0.0":
        fail("contract_invalid")
    if contract["workflow_api"] != "validation.flutter" or contract["stable_check_name"] != "CI / Flutter validation":
        fail("contract_invalid")
    generation = _exact_dict(contract["generation"], EXPECTED_GENERATION_FIELDS, "contract_invalid")
    if generation != {
        "encoding": "UTF-8", "indent": 2, "sort_keys": True, "trailing_newline": True,
        "runtime_fixture_pattern": "tests/fixtures/flutter-validation/runtime-{flutter_version}.json",
    }:
        fail("contract_invalid")
    setup = _exact_dict(contract["setup"], EXPECTED_SETUP_FIELDS, "contract_invalid")
    for field in ("action", "jdk_action"):
        if not isinstance(setup[field], str) or "@" not in setup[field] or FULL_SHA.fullmatch(setup[field].rsplit("@", 1)[1]) is None:
            fail("contract_invalid")
    if setup["jdk_distribution"] != "temurin" or setup["immutable"] is not True or setup["caller_download_url"] is not False or setup["caller_runtime"] is not False or setup["caller_jdk"] is not False:
        fail("contract_invalid")
    if not isinstance(contract["toolchains"], dict) or set(contract["toolchains"]) != EXPECTED_TOOLCHAIN_IDS:
        fail("contract_invalid")
    for version, value in contract["toolchains"].items():
        exact_version(version)
        row = _exact_dict(value, EXPECTED_TOOLCHAIN_FIELDS, "contract_invalid")
        exact_version(row["dart_version"])
        if not isinstance(row["gradle_version"], str) or GRADLE_VERSION.fullmatch(row["gradle_version"]) is None:
            fail("contract_invalid")
        exact_version(row["java_version"])
        exact_version(row["javac_version"])
        if SHORT_OR_FULL_SHA.fullmatch(str(row["framework_revision"])) is None or (row["engine_revision"] and FULL_SHA.fullmatch(str(row["engine_revision"])) is None):
            fail("contract_invalid")
        if row["jdk_distribution"] != setup["jdk_distribution"] or JDK_VERSION.fullmatch(str(row["jdk_version"])) is None:
            fail("contract_invalid")
        if not isinstance(row["java_runtime_version"], str) or not row["java_runtime_version"].startswith(row["jdk_version"]):
            fail("contract_invalid")
        if row["java_vendor"] != "Eclipse Adoptium":
            fail("contract_invalid")
    profiles = contract["profiles"]
    if not isinstance(profiles, dict) or set(profiles) != set(ALLOWED_PROFILES):
        fail("contract_invalid")
    for identifier, profile in profiles.items():
        row = _exact_dict(profile, EXPECTED_PROFILE_FIELDS, "contract_invalid")
        if row["runner"] not in ALLOWED_RUNNERS or not isinstance(row["install_required"], bool):
            fail("contract_invalid")
        if not isinstance(row["timeout_minutes"], int) or not 1 <= row["timeout_minutes"] <= 120:
            fail("contract_invalid")
        if not isinstance(row["allowed_source_trust"], list) or row["allowed_source_trust"] != sorted(set(row["allowed_source_trust"])) or not set(row["allowed_source_trust"]).issubset(ALLOWED_TRUST):
            fail("contract_invalid")
        try:
            stages = tuple(FlutterStage(value) for value in row["stages"])
        except (TypeError, ValueError):
            fail("contract_invalid")
        if identifier in ANDROID_PROFILES and FlutterStage.JDK_VERIFY not in stages:
            fail("contract_invalid")
        if identifier == FlutterProfile.ANDROID_DEBUG.value and FlutterStage.GRADLE_VERIFY not in stages:
            fail("contract_invalid")
    if not isinstance(contract["commands"], dict) or set(contract["commands"]) != EXPECTED_COMMAND_IDS:
        fail("contract_invalid")
    for identifier, command in contract["commands"].items():
        if _command(command).command_id != identifier:
            fail("contract_invalid")
    consumers = contract["consumer_contracts"]
    if not isinstance(consumers, dict) or set(consumers) != EXPECTED_CONSUMER_IDS:
        fail("contract_invalid")
    repositories: set[str] = set()
    for identifier, consumer in consumers.items():
        if SAFE_ID.fullmatch(identifier) is None:
            fail("contract_invalid")
        row = _exact_dict(consumer, EXPECTED_CONSUMER_FIELDS, "contract_invalid")
        repository = row["repository"]
        if not isinstance(repository, str) or REPOSITORY.fullmatch(repository) is None or repository in repositories:
            fail("contract_invalid")
        repositories.add(repository)
        exact_version(row["flutter_version"])
        if row["flutter_version"] not in contract["toolchains"]:
            fail("contract_invalid")
        pin_sources = row["pin_sources"]
        if not isinstance(pin_sources, list) or not pin_sources or len(pin_sources) != len(set(pin_sources)) or not set(pin_sources).issubset(PIN_SOURCES) or not isinstance(row["allow_contract_pin"], bool) or ("contract" in pin_sources) != row["allow_contract_pin"]:
            fail("contract_invalid")
        if not isinstance(row["profiles"], dict) or not row["profiles"]:
            fail("contract_invalid")
        for profile_name, profile_value in row["profiles"].items():
            if profile_name not in profiles:
                fail("contract_invalid")
            profile_row = _exact_dict(profile_value, EXPECTED_CONSUMER_PROFILE_FIELDS, "contract_invalid")
            if not isinstance(profile_row["commands"], list) or any(command not in contract["commands"] for command in profile_row["commands"]):
                fail("contract_invalid")
    if contract["source_trust"] != sorted(ALLOWED_TRUST):
        fail("contract_invalid")
    if not isinstance(contract["failure_codes"], list) or contract["failure_codes"] != sorted(EXPECTED_FAILURE_CODES):
        fail("contract_invalid")
    if not isinstance(contract["forbidden_inputs"], list) or contract["forbidden_inputs"] != sorted(EXPECTED_FORBIDDEN_INPUTS):
        fail("contract_invalid")
    cleanup = contract["cleanup"]
    expected_cleanup_fields = {
        "always_required", "remove_build_output", "remove_cocoapods_state", "remove_derived_data",
        "remove_gradle_state", "remove_logs", "remove_pub_cache", "verify_clean_tree",
        "verify_lock_hash", "verify_persistent_pub_cache_unchanged", "zero_artifacts",
    }
    if not isinstance(cleanup, dict) or set(cleanup) != expected_cleanup_fields or any(value is not True for value in cleanup.values()):
        fail("contract_invalid")
