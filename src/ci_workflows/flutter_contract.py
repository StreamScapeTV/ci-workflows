"""Deterministic Flutter contract parsing and source authority checks."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

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

EXACT_VERSION = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHORT_OR_FULL_SHA = re.compile(r"^[0-9a-f]{7,40}$")
SAFE_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
FORBIDDEN_VERSION_TOKENS = ("stable", "beta", "dev", "master", "main", "^", "~", ">", "<", "*", "x", "X", " ")
FORBIDDEN_COMMAND_TOKENS = (
    "--release",
    "--profile",
    "--device-id",
    "--flavor",
    "flutter upgrade",
    "flutter channel",
    "flutter precache",
    "flutter config",
    "publish",
    "upload",
    "testflight",
    "app-store",
    "notar",
    "security import",
    "keychain",
    "provision",
)
ALLOWED_PROFILES = tuple(profile.value for profile in FlutterProfile)
ALLOWED_RUNNERS = tuple(value.value for value in RunnerCapability)
ALLOWED_TRUST = {"untrusted-fork", "trusted-pr", "trusted-exact"}


class FlutterValidationError(RuntimeError):
    """Stable bounded validation failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def fail(code: str) -> None:
    raise FlutterValidationError(code)


def _exact_dict(value: object, keys: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        fail(code)
    return dict(value)


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


def bounded_path(root: Path, relative: Path, *, must_exist: bool = False) -> Path:
    base = root.resolve()
    candidate = root / relative
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            fail("path_rejected")
    resolved = candidate.resolve(strict=False)
    if resolved != base and base not in resolved.parents:
        fail("path_rejected")
    if must_exist and not resolved.exists():
        fail("path_rejected")
    return resolved


def regular_file(root: Path, relative: str, code: str) -> Path:
    path = bounded_path(root, safe_relative(relative, allow_dot=False), must_exist=True)
    if not path.is_file() or path.is_symlink():
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
        if candidate.exists() or candidate.is_symlink():
            path = regular_file(source_root, name, "invalid_runtime_source")
            version = _read_fvm_pin(path) if name == ".fvmrc" else _read_plain_pin(path)
            versions.append((name, version))
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
        if candidate.exists() or candidate.is_symlink():
            path = regular_file(source_root, name, "source_authority_invalid")
            hashes[name] = file_sha256(path)
    if "pubspec.yaml" not in hashes or "pubspec.lock" not in hashes:
        fail("missing_lockfile")
    return hashes


def checked_in_script(source_root: Path, value: object) -> Path:
    relative = safe_relative(value, allow_dot=False)
    path = bounded_path(source_root, relative, must_exist=True)
    if not path.is_file() or path.is_symlink():
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
    return {"flutter_version": values["frameworkVersion"], "dart_version": dart_version, "framework_revision": framework, "engine_revision": values["engineRevision"]}


def _toolchain(contract: Mapping[str, Any], version: str) -> FlutterToolchain:
    toolchains = contract["toolchains"]
    if version not in toolchains:
        fail("unsupported_runtime")
    raw = toolchains[version]
    return FlutterToolchain(flutter_version=version, dart_version=exact_version(raw["dart_version"]), framework_revision=str(raw["framework_revision"]), engine_revision=str(raw.get("engine_revision", "")), setup_action=str(contract["setup"]["action"]))


def _command(raw: Mapping[str, Any]) -> FlutterCommand:
    row = _exact_dict(raw, {"id", "stage", "argv", "working_directory", "expected_outputs"}, "contract_invalid")
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
    if any(token in joined for token in FORBIDDEN_COMMAND_TOKENS):
        fail("command_boundary_rejected")
    if any(item.lower() == "deploy" for item in argv):
        fail("command_boundary_rejected")
    if argv[0] in {"sh", "zsh", "pwsh", "powershell", "cmd", "sudo", "curl", "wget"}:
        fail("command_boundary_rejected")
    expected = row["expected_outputs"]
    if not isinstance(expected, list) or any(not isinstance(item, str) for item in expected):
        fail("contract_invalid")
    return FlutterCommand(identifier, stage, tuple(argv), str(row["working_directory"]), tuple(expected))


def build_plan(contract: Mapping[str, Any], request: FlutterRequest, source_root: Path | None) -> FlutterPlan:
    if request.source_trust not in ALLOWED_TRUST or SOURCE_SHA.fullmatch(request.admitted_sha) is None:
        fail("invalid_input")
    consumers = contract["consumer_contracts"]
    if request.consumer_contract not in consumers:
        fail("consumer_contract_rejected")
    consumer = consumers[request.consumer_contract]
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
    expected_stages = tuple(stage for stage in stage_values if stage not in {FlutterStage.RUNTIME_VERIFY, FlutterStage.CLEANUP, FlutterStage.DEVICE_HANDOFF})
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
    if request.validation_profile in {FlutterProfile.QUALITY, FlutterProfile.CANONICAL_GATE, FlutterProfile.ANDROID_DEBUG, FlutterProfile.COMPATIBILITY_SMOKE} and runner is not RunnerCapability.MOBILE:
        fail("platform_runner_mismatch")
    if request.validation_profile is FlutterProfile.SOURCE_AUDIT and (runner is not RunnerCapability.PORTABLE or install_required):
        fail("source_audit_install_rejected")
    return FlutterPlan(request=request, runner_profile=runner, install_required=install_required, workspace_profile=str(profile["workspace_profile"]), timeout_minutes=int(profile["timeout_minutes"]), pin=pin, toolchain=toolchain, stages=stage_values, commands=commands, node_composition=node_composition if isinstance(node_composition, dict) else None, gate_path=str(gate_path) if gate_path else None, device_handoff=device_handoff if isinstance(device_handoff, dict) else None)


def validate_contract(raw: object) -> None:
    keys = {"schema_version", "contract_version", "organization", "workflow_api", "stable_check_name", "setup", "toolchains", "profiles", "commands", "consumer_contracts", "source_trust", "forbidden_inputs", "cleanup", "failure_codes"}
    contract = _exact_dict(raw, keys, "contract_invalid")
    if contract["schema_version"] != 1 or contract["contract_version"] != "1.0.0":
        fail("contract_invalid")
    if contract["workflow_api"] != "validation.flutter" or contract["stable_check_name"] != "CI / Flutter validation":
        fail("contract_invalid")
    setup = _exact_dict(contract["setup"], {"action", "immutable", "caller_download_url", "caller_runtime"}, "contract_invalid")
    if not isinstance(setup["action"], str) or "@" not in setup["action"] or FULL_SHA.fullmatch(setup["action"].rsplit("@", 1)[1]) is None:
        fail("contract_invalid")
    if setup["immutable"] is not True or setup["caller_download_url"] is not False or setup["caller_runtime"] is not False:
        fail("contract_invalid")
    if not isinstance(contract["toolchains"], dict) or not contract["toolchains"]:
        fail("contract_invalid")
    for version, value in contract["toolchains"].items():
        exact_version(version)
        row = _exact_dict(value, {"dart_version", "framework_revision", "engine_revision"}, "contract_invalid")
        exact_version(row["dart_version"])
        if SHORT_OR_FULL_SHA.fullmatch(str(row["framework_revision"])) is None:
            fail("contract_invalid")
        if row["engine_revision"] and FULL_SHA.fullmatch(str(row["engine_revision"])) is None:
            fail("contract_invalid")
    profiles = contract["profiles"]
    if set(profiles) != set(ALLOWED_PROFILES):
        fail("contract_invalid")
    for profile in profiles.values():
        row = _exact_dict(profile, {"runner", "install_required", "workspace_profile", "timeout_minutes", "allowed_source_trust", "stages"}, "contract_invalid")
        if row["runner"] not in ALLOWED_RUNNERS or not isinstance(row["install_required"], bool):
            fail("contract_invalid")
        if not isinstance(row["timeout_minutes"], int) or not 1 <= row["timeout_minutes"] <= 120:
            fail("contract_invalid")
        if not isinstance(row["allowed_source_trust"], list) or not set(row["allowed_source_trust"]).issubset(ALLOWED_TRUST):
            fail("contract_invalid")
        try:
            tuple(FlutterStage(value) for value in row["stages"])
        except (TypeError, ValueError):
            fail("contract_invalid")
    if not isinstance(contract["commands"], dict) or not contract["commands"]:
        fail("contract_invalid")
    for identifier, command in contract["commands"].items():
        if _command(command).command_id != identifier:
            fail("contract_invalid")
    if not isinstance(contract["consumer_contracts"], dict) or not contract["consumer_contracts"]:
        fail("contract_invalid")
    for identifier, consumer in contract["consumer_contracts"].items():
        if SAFE_ID.fullmatch(identifier) is None:
            fail("contract_invalid")
        row = _exact_dict(consumer, {"repository", "flutter_version", "pin_sources", "allow_contract_pin", "profiles"}, "contract_invalid")
        exact_version(row["flutter_version"])
        if not isinstance(row["profiles"], dict) or not row["profiles"]:
            fail("contract_invalid")
        for profile_name, profile_value in row["profiles"].items():
            if profile_name not in profiles:
                fail("contract_invalid")
            profile_row = _exact_dict(profile_value, {"commands", "gate_path", "node_composition", "device_handoff"}, "contract_invalid")
            if not isinstance(profile_row["commands"], list) or any(command not in contract["commands"] for command in profile_row["commands"]):
                fail("contract_invalid")
    if contract["source_trust"] != sorted(ALLOWED_TRUST):
        fail("contract_invalid")
    if not isinstance(contract["forbidden_inputs"], list) or not contract["forbidden_inputs"]:
        fail("contract_invalid")
