"""Collision-safe extension loading for reviewed Apple consumer contracts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from .apple_contract import (
    ENVIRONMENT_KEY,
    FORBIDDEN_ENVIRONMENT_KEYS,
    IDENTIFIER,
    REPOSITORY,
    STATE_DIRECTORIES,
    _strings,
    _validate_command,
    _validate_task,
    load_apple_contract as load_base_apple_contract,
    require,
    safe_relative,
)
from .apple_types import AppleProfile, AppleValidationError

_FRAGMENT_PATTERN = "apple-validation-*.json"


def _mapping(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise AppleValidationError("contract_invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AppleValidationError("contract_invalid") from error
    require(isinstance(value, dict), "contract_invalid")
    return value


def _validate_fragment_task(
    task_id: str,
    raw: Mapping[str, object],
    contract: Mapping[str, object],
) -> dict[str, object]:
    """Validate an ordinary task or a stricter script-only simulator extension."""

    try:
        return _validate_task(
            task_id,
            raw,
            contract["profiles"],
            contract["_simulators"],
            contract["artifact_exceptions"],
        )
    except AppleValidationError as primary:
        if primary.code != "command_profile_rejected":
            raise

    required = {
        "validation_profile",
        "working_directory",
        "container",
        "simulator_id",
        "commands",
        "protected_paths",
        "cleanup_paths",
        "environment",
        "artifact_exception_ids",
    }
    require(set(raw) == required, "task_profile_rejected")
    profile = raw.get("validation_profile")
    require(
        profile in {
            AppleProfile.IOS_SIMULATOR.value,
            AppleProfile.TVOS_SIMULATOR.value,
        },
        "command_profile_rejected",
    )
    require(raw.get("container") is None, "command_profile_rejected")
    simulator_id = raw.get("simulator_id")
    simulators = contract["_simulators"]
    require(
        isinstance(simulator_id, str)
        and isinstance(simulators, Mapping)
        and simulator_id in simulators,
        "simulator_contract_invalid",
    )
    simulator = simulators[simulator_id]
    expected_platform = (
        "iOS Simulator"
        if profile == AppleProfile.IOS_SIMULATOR.value
        else "tvOS Simulator"
    )
    require(
        getattr(simulator, "platform", None) == expected_platform,
        "unsafe_destination",
    )
    commands_raw = raw.get("commands")
    require(
        isinstance(commands_raw, list) and bool(commands_raw),
        "command_profile_rejected",
    )
    commands = tuple(
        _validate_command(row)
        for row in commands_raw
        if isinstance(row, Mapping)
    )
    require(
        len(commands) == len(commands_raw)
        and all(command.kind.endswith("-script") for command in commands),
        "command_profile_rejected",
    )
    working_directory = safe_relative(
        raw.get("working_directory"),
        allow_dot=True,
    )
    protected_paths = tuple(
        safe_relative(item, "path_rejected")
        for item in _strings(raw.get("protected_paths"))
    )
    cleanup_paths = tuple(
        safe_relative(item, "cleanup_failed")
        for item in _strings(raw.get("cleanup_paths"))
    )
    environment = raw.get("environment")
    require(isinstance(environment, Mapping), "environment_rejected")
    environment_bindings: list[tuple[str, str]] = []
    for key, value in sorted(environment.items()):
        require(
            isinstance(key, str)
            and ENVIRONMENT_KEY.fullmatch(key) is not None
            and key not in FORBIDDEN_ENVIRONMENT_KEYS
            and isinstance(value, str)
            and value in STATE_DIRECTORIES,
            "environment_rejected",
        )
        environment_bindings.append((key, value))
    exceptions = tuple(_strings(raw.get("artifact_exception_ids")))
    artifact_exceptions = contract["artifact_exceptions"]
    require(
        isinstance(artifact_exceptions, Mapping)
        and all(item in artifact_exceptions for item in exceptions),
        "artifact_exception_rejected",
    )
    return {
        "validation_profile": str(profile),
        "working_directory": working_directory,
        "container": None,
        "simulator": simulator,
        "commands": commands,
        "protected_paths": protected_paths,
        "cleanup_paths": cleanup_paths,
        "environment_bindings": tuple(environment_bindings),
        "artifact_exception_ids": exceptions,
    }


def load_apple_contract(root: Path) -> Mapping[str, object]:
    """Load the base contract and validate additive task/consumer fragments."""

    contract = dict(load_base_apple_contract(root))
    tasks = dict(contract["tasks"])
    typed_tasks = dict(contract["_tasks"])
    consumers = dict(contract["consumer_contracts"])
    for path in sorted((root / "contracts").glob(_FRAGMENT_PATTERN)):
        fragment = _mapping(path)
        require(set(fragment) == {"tasks", "consumer_contracts"}, "contract_invalid")
        additions = fragment["tasks"]
        require(isinstance(additions, dict) and additions, "contract_invalid")
        for task_id, raw in additions.items():
            require(
                isinstance(task_id, str)
                and task_id not in tasks
                and isinstance(raw, Mapping),
                "contract_invalid",
            )
            typed_tasks[task_id] = _validate_fragment_task(task_id, raw, contract)
            tasks[task_id] = dict(raw)
        incoming_consumers = fragment["consumer_contracts"]
        require(
            isinstance(incoming_consumers, dict) and incoming_consumers,
            "contract_invalid",
        )
        for identifier, consumer in incoming_consumers.items():
            require(
                isinstance(identifier, str)
                and IDENTIFIER.fullmatch(identifier) is not None
                and identifier not in consumers
                and isinstance(consumer, Mapping),
                "consumer_contract_rejected",
            )
            require(
                set(consumer) == {"repository", "profiles"}
                and isinstance(consumer.get("repository"), str)
                and REPOSITORY.fullmatch(str(consumer["repository"])) is not None,
                "consumer_contract_rejected",
            )
            mapping = consumer.get("profiles")
            require(isinstance(mapping, Mapping) and mapping, "consumer_contract_rejected")
            for profile, task_id in mapping.items():
                require(
                    profile in contract["profiles"]
                    and task_id in typed_tasks
                    and typed_tasks[str(task_id)]["validation_profile"] == profile,
                    "consumer_contract_rejected",
                )
            consumers[identifier] = {
                "repository": consumer["repository"],
                "profiles": dict(mapping),
            }
    return {
        **contract,
        "tasks": tasks,
        "consumer_contracts": consumers,
        "_tasks": typed_tasks,
    }
