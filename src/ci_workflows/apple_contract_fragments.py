"""Collision-safe extension loading for reviewed Apple consumer contracts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from .apple_contract import (
    IDENTIFIER,
    REPOSITORY,
    _validate_task,
    load_apple_contract as load_base_apple_contract,
    require,
)
from .apple_types import AppleValidationError

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
            typed_tasks[task_id] = _validate_task(
                task_id,
                raw,
                contract["profiles"],
                contract["_simulators"],
                contract["artifact_exceptions"],
            )
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
