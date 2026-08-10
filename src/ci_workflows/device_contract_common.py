"""Shared bounded helpers for the device contract modules."""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .device_types import DeviceValidationError

PROFILE_CONTRACT_PATH = Path("contracts/device-profiles.json")
EVIDENCE_CONTRACT_PATH = Path("contracts/device-evidence.json")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9.-]{2,63}$")
ALIAS = re.compile(r"^[a-z][a-z0-9-]{2,47}$")
CAPABILITY = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
REQUEST_ID = re.compile(r"^issue-([1-9][0-9]*)-([a-z0-9][a-z0-9-]{7,63})$")
RUN_ID = re.compile(r"^[1-9][0-9]{0,19}:[1-9][0-9]{0,3}$")
SCRIPT_SUFFIXES = (".sh", ".py")

def fail(code: str) -> None:
    raise DeviceValidationError(code)

def require(condition: bool, code: str) -> None:
    if not condition:
        fail(code)

def _read_json(path: Path, code: str = "invalid_input") -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DeviceValidationError(code) from error
    require(isinstance(value, Mapping), code)
    return value

def strings(
    value: Any,
    *,
    nonempty: bool = False,
    unique: bool = True,
    code: str = "invalid_input",
) -> list[str]:
    require(isinstance(value, list), code)
    require(not nonempty or bool(value), code)
    require(all(isinstance(item, str) and item for item in value), code)
    require(not unique or len(value) == len(set(value)), code)
    return list(value)

def safe_relative(value: Any, code: str = "invalid_input") -> str:
    require(isinstance(value, str), code)
    candidate = value.strip()
    path = PurePosixPath(candidate)
    require(
        bool(candidate)
        and not path.is_absolute()
        and "\\" not in candidate
        and ".." not in path.parts
        and all(part not in {"", "."} for part in path.parts),
        code,
    )
    require(candidate.endswith(SCRIPT_SUFFIXES), code)
    return path.as_posix()

def version_tuple(value: str) -> tuple[int, ...]:
    require(
        isinstance(value, str)
        and re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){1,2}", value)
        is not None,
        "device_inventory_malformed",
    )
    return tuple(int(part) for part in value.split("."))

def parse_request_id(value: str) -> int:
    match = REQUEST_ID.fullmatch(value)
    require(match is not None, "request_identity_rejected")
    return int(match.group(1))

