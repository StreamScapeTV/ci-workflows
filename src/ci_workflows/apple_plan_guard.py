"""Security guard for caller-owned Apple protected-full plan channels."""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

from .apple_types import AppleValidationError

_MAX_PLAN_BYTES = 32 * 1024
_MAX_STAGES = 8
_SAFE_CLEANUP_LEAVES = {".build", ".swiftpm", "build", "xcuserdata"}
_SAFE_XCODE_FLAGS = {"-quiet", "-showBuildTimingSummary"}
_SAFE_BOOLEAN_BUILD_SETTINGS = {
    "COMPILER_INDEX_STORE_ENABLE",
    "ENABLE_CODE_COVERAGE",
    "ENABLE_TESTABILITY",
    "GCC_TREAT_WARNINGS_AS_ERRORS",
    "ONLY_ACTIVE_ARCH",
    "SWIFT_TREAT_WARNINGS_AS_ERRORS",
}
_BUILD_SETTING = re.compile(r"^([A-Z][A-Z0-9_]*)=(YES|NO)$")


def _fail(code: str) -> None:
    raise AppleValidationError(code)


def _safe_relative(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or any(ch in value for ch in "\x00\r\n\\"):
        _fail(code)
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or any(part in {"", "."} for part in path.parts):
        _fail(code)
    return path.as_posix()


def _guard_xcode_arguments(value: object) -> None:
    if not isinstance(value, list) or len(value) > 24:
        _fail("validation_plan_invalid")
    for raw in value:
        if not isinstance(raw, str) or not raw or any(ch in raw for ch in "\x00\r\n"):
            _fail("validation_plan_invalid")
        if raw in _SAFE_XCODE_FLAGS:
            continue
        match = _BUILD_SETTING.fullmatch(raw)
        if match is None or match.group(1) not in _SAFE_BOOLEAN_BUILD_SETTINGS:
            _fail("forbidden_operation")


def _guard_cleanup_paths(value: object) -> None:
    if not isinstance(value, list) or len(value) > 16:
        _fail("cleanup_failed")
    for raw in value:
        relative = _safe_relative(raw, "cleanup_failed")
        if PurePosixPath(relative).name not in _SAFE_CLEANUP_LEAVES:
            _fail("cleanup_failed")


def validate_protected_full_plan_json(raw: str) -> None:
    """Reject output redirection and arbitrary cleanup before plan resolution.

    The main planner owns complete structural/path validation.  This guard is a
    separate public-boundary check for the two caller-controlled channels that
    can otherwise affect filesystem placement: Xcode arguments and cleanup
    targets.
    """

    if not isinstance(raw, str) or not raw or len(raw.encode("utf-8")) > _MAX_PLAN_BYTES:
        _fail("validation_plan_invalid")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise AppleValidationError("validation_plan_invalid") from error
    if not isinstance(payload, dict) or set(payload) != {"stages"}:
        _fail("validation_plan_invalid")
    stages = payload["stages"]
    if not isinstance(stages, list) or not 1 <= len(stages) <= _MAX_STAGES:
        _fail("validation_plan_invalid")
    for stage in stages:
        if not isinstance(stage, dict):
            _fail("validation_plan_invalid")
        _guard_cleanup_paths(stage.get("cleanup_paths"))
        operation = stage.get("operation")
        arguments = stage.get("xcodebuild_arguments")
        if operation == "script":
            if arguments != []:
                _fail("validation_plan_invalid")
            continue
        _guard_xcode_arguments(arguments)
