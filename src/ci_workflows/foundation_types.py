"""Shared typed validation helpers for non-language CI foundation primitives."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SAFE_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
INSTRUCTION = re.compile(r"^[a-z][a-z0-9_]{2,95}$")


class FoundationError(RuntimeError):
    """Fail-closed error carrying only a stable non-secret instruction code."""

    def __init__(self, instruction: str) -> None:
        if INSTRUCTION.fullmatch(instruction) is None:
            raise ValueError("foundation instruction must be a safe code")
        self.instruction = instruction
        super().__init__(instruction)


@dataclass(frozen=True)
class ContractLocation:
    root: Path
    relative_path: str

    @property
    def path(self) -> Path:
        return self.root / self.relative_path


def require(condition: bool, instruction: str) -> None:
    if not condition:
        raise FoundationError(instruction)


def full_sha(value: Any, instruction: str = "exact_sha_required") -> str:
    require(isinstance(value, str) and FULL_SHA.fullmatch(value) is not None, instruction)
    return value


def sha256_hex(value: Any, instruction: str = "sha256_required") -> str:
    require(isinstance(value, str) and SHA256.fullmatch(value) is not None, instruction)
    return value


def safe_id(value: Any, instruction: str = "invalid_safe_id") -> str:
    require(isinstance(value, str) and SAFE_ID.fullmatch(value) is not None, instruction)
    return value


def safe_name(value: Any, instruction: str = "invalid_safe_name") -> str:
    require(isinstance(value, str) and SAFE_NAME.fullmatch(value) is not None, instruction)
    return value


def repository_name(value: Any, instruction: str = "invalid_repository") -> str:
    require(isinstance(value, str) and REPOSITORY.fullmatch(value) is not None, instruction)
    return value


def bounded_int(
    value: Any,
    *,
    minimum: int,
    maximum: int,
    instruction: str,
) -> int:
    if isinstance(value, bool):
        raise FoundationError(instruction)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise FoundationError(instruction) from error
    require(minimum <= parsed <= maximum, instruction)
    return parsed


def safe_relative_path(value: Any, instruction: str = "invalid_relative_path") -> str:
    require(isinstance(value, str), instruction)
    candidate = value.strip()
    pure = PurePosixPath(candidate)
    require(
        bool(candidate)
        and not pure.is_absolute()
        and ".." not in pure.parts
        and "\\" not in candidate
        and all(part not in {"", "."} for part in pure.parts),
        instruction,
    )
    return pure.as_posix()


def bounded_path(root: Path, relative: str, instruction: str = "path_outside_root") -> Path:
    relative = safe_relative_path(relative)
    resolved_root = root.resolve()
    target = (resolved_root / Path(*PurePosixPath(relative).parts)).resolve(strict=False)
    require(target != resolved_root and resolved_root in target.parents, instruction)
    return target


def ensure_no_symlink_escape(root: Path, target: Path) -> None:
    resolved_root = root.resolve()
    require(target == resolved_root or resolved_root in target.parents, "path_outside_root")
    current = resolved_root
    if current.is_symlink():
        raise FoundationError("symlink_escape_detected")
    try:
        relative_parts = target.relative_to(resolved_root).parts
    except ValueError as error:
        raise FoundationError("path_outside_root") from error
    for part in relative_parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise FoundationError("symlink_escape_detected")


def load_contract(root: Path, relative_path: str) -> Mapping[str, Any]:
    location = ContractLocation(root=root, relative_path=safe_relative_path(relative_path))
    try:
        value = json.loads(location.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FoundationError("foundation_contract_unavailable") from error
    require(isinstance(value, dict), "foundation_contract_invalid")
    require(value.get("schema_version") == 1, "foundation_contract_schema_unsupported")
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_identifier(prefix: str, value: Any, *, length: int = 20) -> str:
    prefix = safe_id(prefix)
    require(12 <= length <= 48, "invalid_identifier_length")
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:length]}"


def atomic_write_text(path: Path, content: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, value: Any, *, mode: int = 0o600) -> None:
    atomic_write_text(path, canonical_json(value) + "\n", mode=mode)
