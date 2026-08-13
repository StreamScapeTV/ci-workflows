"""Closed producer-owned locks for immutable OCI build inputs."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shlex
import stat
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from .oci_types import OciBuildError

CONTRACT_PATH = Path("contracts/oci-build-input-lock.schema.json")

_SAFE_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PRODUCT_ID = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OCI_REFERENCE = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?/"
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
    r"(?::[A-Za-z0-9_][A-Za-z0-9._-]{0,127})?"
    r"@sha256:[0-9a-f]{64}$"
)
_REGISTRY_HOST = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,62}\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_PLATFORMS = {"linux/amd64", "linux/arm64/v8"}
_FROM = re.compile(
    r"^FROM(?:\s+--platform=([^\s]+))?\s+([^\s]+)"
    r"(?:\s+AS\s+([A-Za-z0-9][A-Za-z0-9_.-]{0,63}))?\s*$",
    re.IGNORECASE,
)
_RESERVED_INPUT_ROOT = ".ciw-build-inputs"
INPUT_FAILURE_CODES = frozenset(
    {
        "dockerfile_parser_ambiguous",
        "input_base_mutable",
        "input_destination_unsafe",
        "input_digest_invalid",
        "input_host_forbidden",
        "input_lock_duplicate",
        "input_lock_incomplete",
        "input_lock_invalid",
        "input_lock_mismatch",
        "input_lock_path_invalid",
        "input_platform_invalid",
        "input_policy_mismatch",
        "input_size_invalid",
        "input_url_invalid",
    }
)


class OciInputContractError(OciBuildError):
    """A stable OCI build failure scoped to the input-lock contract."""


def _fail(code: str) -> None:
    raise OciInputContractError(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        _fail(code)


@dataclass(frozen=True, slots=True)
class OciBasePlatformIdentity:
    platform: str
    manifest_digest: str
    config_digest: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OciBaseLock:
    stage_id: str
    from_ordinal: int
    stage_marker: str
    kind: str
    declared_reference: str
    dockerfile_platform: str | None
    platforms: tuple[str, ...]
    platform_identities: tuple[OciBasePlatformIdentity, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "stage_id": self.stage_id,
            "from_ordinal": self.from_ordinal,
            "stage_marker": self.stage_marker,
            "kind": self.kind,
            "declared_reference": self.declared_reference,
            "dockerfile_platform": self.dockerfile_platform,
            "platforms": list(self.platforms),
            "platform_identities": [item.to_dict() for item in self.platform_identities],
        }


@dataclass(frozen=True, slots=True)
class OciExternalInputLock:
    input_id: str
    url: str
    sha256: str
    maximum_bytes: int
    destination: str

    def to_dict(self) -> dict[str, object]:
        return {
            "input_id": self.input_id,
            "url": self.url,
            "sha256": self.sha256,
            "maximum_bytes": self.maximum_bytes,
            "destination": self.destination,
        }


@dataclass(frozen=True, slots=True)
class OciTargetInputLock:
    product_id: str
    target_id: str
    input_policy_id: str
    platforms: tuple[str, ...]
    bases: tuple[OciBaseLock, ...]
    external_inputs: tuple[OciExternalInputLock, ...]
    lock_digest: str

    def canonical_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "lock_version": "1.0.0",
            "product_id": self.product_id,
            "target_id": self.target_id,
            "input_policy_id": self.input_policy_id,
            "platforms": list(self.platforms),
            "bases": [item.to_dict() for item in self.bases],
            "external_inputs": [item.to_dict() for item in self.external_inputs],
        }


@dataclass(frozen=True, slots=True)
class OciBaseEvidence:
    target_id: str
    stage_id: str
    from_ordinal: int
    declared_reference: str
    platform: str
    root_digest: str
    manifest_digest: str
    config_digest: str
    acquisition_policy_id: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OciExternalInputEvidence:
    target_id: str
    input_id: str
    sha256: str
    size_bytes: int
    acquisition_policy_id: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OciTargetInputEvidence:
    target_id: str
    input_policy_id: str
    lock_digest: str
    bases: tuple[OciBaseEvidence, ...]
    external_inputs: tuple[OciExternalInputEvidence, ...]
    redacted: bool = field(default=True, init=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "target_id": self.target_id,
            "input_policy_id": self.input_policy_id,
            "lock_digest": self.lock_digest,
            "bases": [item.to_dict() for item in self.bases],
            "external_inputs": [item.to_dict() for item in self.external_inputs],
            "redacted": self.redacted,
        }


def _mapping(value: object, code: str = "input_lock_invalid") -> dict[str, Any]:
    _require(isinstance(value, dict), code)
    return dict(value)


def _exact_mapping(value: object, keys: set[str]) -> dict[str, Any]:
    result = _mapping(value)
    _require(set(result) == keys, "input_lock_invalid")
    return result


def _safe_id(value: object) -> str:
    _require(isinstance(value, str) and _SAFE_ID.fullmatch(value) is not None, "input_lock_invalid")
    return value


def _product_id(value: object) -> str:
    _require(isinstance(value, str) and _PRODUCT_ID.fullmatch(value) is not None, "input_lock_invalid")
    return value


def _platforms(value: object) -> tuple[str, ...]:
    _require(isinstance(value, list) and 1 <= len(value) <= len(_PLATFORMS), "input_platform_invalid")
    _require(all(isinstance(item, str) and item in _PLATFORMS for item in value), "input_platform_invalid")
    result = tuple(value)
    _require(len(result) == len(set(result)), "input_lock_duplicate")
    _require(result == tuple(sorted(result)), "input_platform_invalid")
    return result


def _safe_relative(value: object) -> str:
    _require(
        isinstance(value, str)
        and 1 <= len(value) <= 240
        and value == value.strip(),
        "input_destination_unsafe",
    )
    _require("\\" not in value and "\x00" not in value, "input_destination_unsafe")
    path = PurePosixPath(value)
    _require(
        not path.is_absolute()
        and 2 <= len(path.parts) <= 8
        and all(part not in {"", ".", ".."} for part in path.parts),
        "input_destination_unsafe",
    )
    normalized = path.as_posix()
    _require(normalized == value and normalized != _RESERVED_INPUT_ROOT, "input_destination_unsafe")
    _require(
        path.parts[0] == _RESERVED_INPUT_ROOT
        and _RESERVED_INPUT_ROOT not in path.parts[1:]
        and all(_SAFE_NAME.fullmatch(part) is not None for part in path.parts[1:]),
        "input_destination_unsafe",
    )
    return normalized


def _registry_host(reference: str) -> str:
    host = reference.split("/", 1)[0]
    _require(_REGISTRY_HOST.fullmatch(host) is not None, "input_host_forbidden")
    _require(_public_dns_host(host), "input_host_forbidden")
    return host


def _public_dns_host(host: str) -> bool:
    lowered = host.rstrip(".").lower()
    if (
        lowered != host
        or lowered in {"localhost", "localhost.localdomain"}
        or lowered.endswith((".localhost", ".local", ".internal", ".home", ".lan"))
    ):
        return False
    try:
        address = ipaddress.ip_address(lowered.strip("[]"))
    except ValueError:
        return _REGISTRY_HOST.fullmatch(lowered) is not None
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _base(
    value: object,
    target_platforms: tuple[str, ...],
) -> OciBaseLock:
    row = _exact_mapping(
        value,
        {
            "stage_id",
            "from_ordinal",
            "stage_marker",
            "kind",
            "declared_reference",
            "dockerfile_platform",
            "platforms",
            "platform_identities",
        },
    )
    stage_id = _safe_id(row["stage_id"])
    ordinal = row["from_ordinal"]
    _require(type(ordinal) is int and 1 <= ordinal <= 64, "input_lock_invalid")
    marker = row["stage_marker"]
    _require(marker in {"intermediate", "final"}, "input_lock_invalid")
    kind = row["kind"]
    _require(kind in {"scratch", "external"}, "input_lock_invalid")
    reference = row["declared_reference"]
    _require(isinstance(reference, str) and 1 <= len(reference) <= 512, "input_base_mutable")
    if kind == "scratch":
        _require(reference == "scratch", "input_lock_invalid")
    elif kind == "external":
        _require(reference != "scratch", "input_lock_invalid")
        _require(_OCI_REFERENCE.fullmatch(reference) is not None, "input_base_mutable")
        _registry_host(reference)
    dockerfile_platform = row["dockerfile_platform"]
    _require(
        dockerfile_platform is None
        or (isinstance(dockerfile_platform, str) and dockerfile_platform in _PLATFORMS),
        "input_platform_invalid",
    )
    platforms = _platforms(row["platforms"])
    _require(set(platforms) <= set(target_platforms), "input_platform_invalid")
    if dockerfile_platform is not None:
        _require(platforms == (dockerfile_platform,), "input_platform_invalid")
    raw_identities = row["platform_identities"]
    _require(isinstance(raw_identities, list), "input_lock_invalid")
    identities: list[OciBasePlatformIdentity] = []
    for raw_identity in raw_identities:
        identity = _exact_mapping(
            raw_identity, {"platform", "manifest_digest", "config_digest"}
        )
        platform = identity["platform"]
        manifest_digest = identity["manifest_digest"]
        config_digest = identity["config_digest"]
        _require(isinstance(platform, str) and platform in _PLATFORMS, "input_platform_invalid")
        _require(
            isinstance(manifest_digest, str) and _DIGEST.fullmatch(manifest_digest) is not None,
            "input_digest_invalid",
        )
        _require(
            isinstance(config_digest, str) and _DIGEST.fullmatch(config_digest) is not None,
            "input_digest_invalid",
        )
        identities.append(
            OciBasePlatformIdentity(platform, manifest_digest, config_digest)
        )
    platform_identities = tuple(identities)
    if kind == "external":
        _require(
            tuple(item.platform for item in platform_identities) == platforms,
            "input_lock_incomplete",
        )
    else:
        _require(not platform_identities, "input_lock_invalid")
    return OciBaseLock(
        stage_id=stage_id,
        from_ordinal=ordinal,
        stage_marker=str(marker),
        kind=str(kind),
        declared_reference=reference,
        dockerfile_platform=dockerfile_platform,
        platforms=platforms,
        platform_identities=platform_identities,
    )


def _external(value: object) -> OciExternalInputLock:
    row = _exact_mapping(
        value,
        {"input_id", "url", "sha256", "maximum_bytes", "destination"},
    )
    input_id = _safe_id(row["input_id"])
    url = row["url"]
    _require(
        isinstance(url, str)
        and 1 <= len(url) <= 2048
        and url == url.strip()
        and url.isascii()
        and "\\" not in url
        and all(ord(character) >= 0x20 for character in url),
        "input_url_invalid",
    )
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise OciInputContractError("input_url_invalid") from error
    _require(
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
        and parsed.netloc == parsed.netloc.lower()
        and not parsed.fragment,
        "input_url_invalid",
    )
    _require(parsed.hostname == parsed.hostname.lower() and _public_dns_host(parsed.hostname), "input_host_forbidden")
    _require(parsed.path.startswith("/") and parsed.path != "/" and not parsed.query, "input_url_invalid")
    digest = row["sha256"]
    _require(isinstance(digest, str) and _SHA256.fullmatch(digest) is not None, "input_digest_invalid")
    maximum = row["maximum_bytes"]
    _require(type(maximum) is int and 1 <= maximum <= 1_073_741_824, "input_size_invalid")
    return OciExternalInputLock(
        input_id=input_id,
        url=url,
        sha256=digest,
        maximum_bytes=maximum,
        destination=_safe_relative(row["destination"]),
    )


def _canonical_payload(value: object) -> object:
    if isinstance(value, OciTargetInputLock):
        return value.canonical_payload()
    if isinstance(value, (OciBasePlatformIdentity, OciBaseLock, OciExternalInputLock)):
        return value.to_dict()
    if is_dataclass(value):
        return asdict(value)
    return value


def canonical_lock_digest(value: object) -> str:
    """Hash only canonical declared lock data, never derived runtime evidence."""

    payload = json.dumps(
        _canonical_payload(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _bounded_lock_path(source_root: Path, relative_path: str) -> Path:
    _require(source_root.is_dir() and not source_root.is_symlink(), "input_lock_path_invalid")
    root = source_root.resolve()
    _require(isinstance(relative_path, str) and relative_path, "input_lock_path_invalid")
    path = PurePosixPath(relative_path)
    _require(
        not path.is_absolute()
        and "\\" not in relative_path
        and all(part not in {"", ".", ".."} for part in path.parts),
        "input_lock_path_invalid",
    )
    current = root
    for part in path.parts:
        current /= part
        _require(not current.is_symlink(), "input_lock_path_invalid")
    resolved = current.resolve(strict=False)
    _require(root in resolved.parents and resolved.is_file() and not resolved.is_symlink(), "input_lock_path_invalid")
    return resolved


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("input_lock_duplicate")
        result[key] = value
    return result


def _read_lock(path: Path) -> object:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise OciInputContractError("input_lock_path_invalid") from error
    try:
        info = os.fstat(descriptor)
        _require(stat.S_ISREG(info.st_mode) and info.st_size <= 1_048_576, "input_lock_invalid")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            return json.load(handle, object_pairs_hook=_unique_object)
    except OciInputContractError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OciInputContractError("input_lock_invalid") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def load_input_lock_contract(
    source_root: Path,
    relative_path: str,
    *,
    product_id: str,
    target_id: str,
    input_policy_id: str,
    expected_platforms: tuple[str, ...],
) -> OciTargetInputLock:
    """Read the fixed exact-source lock and bind it to its central target policy."""

    path = _bounded_lock_path(source_root, relative_path)
    payload = _read_lock(path)
    row = _exact_mapping(
        payload,
        {
            "schema_version",
            "lock_version",
            "product_id",
            "target_id",
            "input_policy_id",
            "platforms",
            "bases",
            "external_inputs",
        },
    )
    _require(
        type(row["schema_version"]) is int
        and row["schema_version"] == 1
        and row["lock_version"] == "1.0.0",
        "input_lock_invalid",
    )
    declared_product = _product_id(row["product_id"])
    declared_target = _safe_id(row["target_id"])
    declared_policy = _safe_id(row["input_policy_id"])
    _require(
        declared_product == product_id
        and declared_target == target_id
        and declared_policy == input_policy_id,
        "input_policy_mismatch",
    )
    target_platforms = _platforms(row["platforms"])
    _require(target_platforms == _platforms(list(expected_platforms)), "input_platform_invalid")
    raw_bases = row["bases"]
    raw_inputs = row["external_inputs"]
    _require(isinstance(raw_bases, list) and 1 <= len(raw_bases) <= 64, "input_lock_incomplete")
    _require(isinstance(raw_inputs, list) and len(raw_inputs) <= 64, "input_lock_invalid")
    parsed_bases: list[OciBaseLock] = []
    for item in raw_bases:
        parsed_base = _base(item, target_platforms)
        parsed_bases.append(parsed_base)
    bases = tuple(parsed_bases)
    inputs = tuple(_external(item) for item in raw_inputs)
    _require(tuple(item.from_ordinal for item in bases) == tuple(range(1, len(bases) + 1)), "input_lock_incomplete")
    _require(len({item.stage_id for item in bases}) == len(bases), "input_lock_duplicate")
    _require([item.stage_marker for item in bases].count("final") == 1, "input_lock_incomplete")
    _require(bases[-1].stage_marker == "final", "input_lock_incomplete")
    _require(all(item.stage_marker == "intermediate" for item in bases[:-1]), "input_lock_incomplete")
    _require(len({item.input_id for item in inputs}) == len(inputs), "input_lock_duplicate")
    _require(len({item.destination for item in inputs}) == len(inputs), "input_lock_duplicate")
    _require(len({(item.url, item.sha256) for item in inputs}) == len(inputs), "input_lock_duplicate")
    declared = {
        "schema_version": 1,
        "lock_version": "1.0.0",
        "product_id": declared_product,
        "target_id": declared_target,
        "input_policy_id": declared_policy,
        "platforms": list(target_platforms),
        "bases": [item.to_dict() for item in bases],
        "external_inputs": [item.to_dict() for item in inputs],
    }
    return OciTargetInputLock(
        product_id=declared_product,
        target_id=declared_target,
        input_policy_id=declared_policy,
        platforms=target_platforms,
        bases=bases,
        external_inputs=inputs,
        lock_digest=canonical_lock_digest(declared),
    )


def _logical_dockerfile_lines(text: str) -> tuple[str, ...]:
    _require(not text.startswith("\ufeff"), "dockerfile_parser_ambiguous")
    lines: list[str] = []
    logical = ""
    continuing = False
    for raw in text.split("\n"):
        _require(
            all(
                character == " "
                or (character.isprintable() and not character.isspace())
                for character in raw
            ),
            "dockerfile_parser_ambiguous",
        )
        stripped = raw.rstrip()
        if continuing and not stripped:
            _fail("dockerfile_parser_ambiguous")
        if not logical and stripped.lstrip().startswith("#"):
            _require(not continuing, "dockerfile_parser_ambiguous")
            directive = stripped.lstrip()[1:].strip().lower()
            _require(
                not directive.startswith(("syntax=", "escape=", "check=")),
                "dockerfile_parser_ambiguous",
            )
            continue
        if continuing and stripped.lstrip().startswith("#"):
            _fail("dockerfile_parser_ambiguous")
        logical += (stripped[:-1] + " ") if stripped.endswith("\\") else stripped
        continuing = stripped.endswith("\\")
        if not continuing:
            if logical.strip():
                _require("<<" not in logical, "dockerfile_parser_ambiguous")
                lines.append(logical.strip())
            logical = ""
    _require(not continuing and not logical, "dockerfile_parser_ambiguous")
    return tuple(lines)


def _instruction_body(line: str, instruction: str) -> str | None:
    parts = line.split(None, 1)
    if not parts or parts[0].upper() != instruction:
        return None
    _require(len(parts) == 2 and bool(parts[1].strip()), "dockerfile_parser_ambiguous")
    return parts[1].lstrip()


def _prior_stage_source(value: str, prior_aliases: set[str]) -> None:
    _require(bool(value) and "=" not in value, "dockerfile_parser_ambiguous")
    _require(not any(character in value for character in "'\"\\"), "dockerfile_parser_ambiguous")
    _require("$" not in value, "input_base_mutable")
    normalized = value.lower()
    _require(_SAFE_ID.fullmatch(normalized) is not None, "input_lock_incomplete")
    _require(normalized in prior_aliases, "input_lock_incomplete")


def _pop_option(body: str) -> tuple[str, str]:
    match = re.match(r"^(--[^\s]+)(?:\s+|$)(.*)$", body, re.DOTALL)
    _require(match is not None, "dockerfile_parser_ambiguous")
    token, remainder = match.groups()
    _require(
        re.fullmatch(r"--[a-z][a-z0-9-]*(?:=[^\s]+)?", token) is not None,
        "dockerfile_parser_ambiguous",
    )
    return token, remainder.lstrip()


def _copy_operands(body: str) -> tuple[str, ...]:
    if body.startswith("["):
        try:
            operands = json.loads(body, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, OciInputContractError) as error:
            raise OciInputContractError("dockerfile_parser_ambiguous") from error
        _require(
            isinstance(operands, list)
            and len(operands) >= 2
            and all(isinstance(item, str) and item for item in operands),
            "dockerfile_parser_ambiguous",
        )
        return tuple(operands)
    try:
        operands = tuple(shlex.split(body, posix=True))
    except ValueError as error:
        raise OciInputContractError("dockerfile_parser_ambiguous") from error
    _require(len(operands) >= 2, "dockerfile_parser_ambiguous")
    return operands


def _local_copy_source(value: str) -> None:
    _require(
        value
        and "$" not in value
        and "\\" not in value
        and not value.startswith(("/", "git@", "http://", "https://", "git://", "ssh://")),
        "input_lock_incomplete",
    )
    path = PurePosixPath(value)
    _require(".." not in path.parts, "input_lock_incomplete")


def _validate_copy_from(line: str, prior_aliases: set[str]) -> None:
    body = _instruction_body(line, "COPY")
    if body is None:
        return
    sources: list[str] = []
    while body.startswith("--"):
        token, body = _pop_option(body)
        if token.startswith("--from"):
            _require(token.startswith("--from="), "dockerfile_parser_ambiguous")
            sources.append(token.removeprefix("--from="))
    _require(len(sources) <= 1, "dockerfile_parser_ambiguous")
    operands = _copy_operands(body)
    if sources:
        _prior_stage_source(sources[0], prior_aliases)
    else:
        for source in operands[:-1]:
            _local_copy_source(source)


def _validate_run_mount_from(line: str, prior_aliases: set[str]) -> None:
    body = _instruction_body(line, "RUN")
    if body is None:
        return
    while body.startswith("--"):
        token, body = _pop_option(body)
        _require(token.startswith("--mount"), "input_lock_incomplete")
        _require(token.startswith("--mount="), "dockerfile_parser_ambiguous")
        mount = token.removeprefix("--mount=")
        _require(
            bool(mount) and not any(character in mount for character in "'\"\\"),
            "dockerfile_parser_ambiguous",
        )
        options: dict[str, str | None] = {}
        for option in mount.split(","):
            _require(bool(option), "dockerfile_parser_ambiguous")
            key, equals, value = option.partition("=")
            _require(
                key in {"type", "from", "source", "target", "ro"}
                and key not in options,
                "input_lock_incomplete",
            )
            if key == "ro":
                _require(not equals, "dockerfile_parser_ambiguous")
                options[key] = None
            else:
                _require(bool(equals) and bool(value), "dockerfile_parser_ambiguous")
                options[key] = value
        _require(
            set(options) in (
                {"type", "from", "target", "ro"},
                {"type", "from", "source", "target", "ro"},
            )
            and options["type"] == "bind",
            "input_lock_incomplete",
        )
        source = options["from"]
        _require(isinstance(source, str), "dockerfile_parser_ambiguous")
        _prior_stage_source(source, prior_aliases)
        for field in ("source", "target"):
            value = options.get(field)
            if value is None:
                continue
            _require(
                isinstance(value, str)
                and value.startswith("/")
                and "$" not in value
                and ".." not in PurePosixPath(value).parts,
                "input_lock_incomplete",
            )
    _require(bool(body), "dockerfile_parser_ambiguous")


def validate_target_dockerfile_lock(
    dockerfile_path: Path,
    target_lock: OciTargetInputLock,
    platforms: tuple[str, ...],
) -> tuple[OciBaseLock, ...]:
    """Require exact ordered agreement between every logical FROM and its lock."""

    _require(dockerfile_path.is_file() and not dockerfile_path.is_symlink(), "input_lock_path_invalid")
    _require(dockerfile_path.stat().st_size <= 1_048_576, "dockerfile_parser_ambiguous")
    _require(tuple(platforms) == target_lock.platforms, "input_platform_invalid")
    try:
        text = dockerfile_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise OciInputContractError("dockerfile_parser_ambiguous") from error
    parsed: list[tuple[str, int, str, str, str | None, tuple[str, ...]]] = []
    aliases: set[str] = set()
    prior_aliases: set[str] = set()
    for line in _logical_dockerfile_lines(text):
        if not line.upper().startswith("FROM"):
            instruction = line.split(None, 1)[0].upper()
            _require(instruction not in {"ADD", "ONBUILD"}, "input_lock_incomplete")
            _validate_copy_from(line, prior_aliases)
            _validate_run_mount_from(line, prior_aliases)
            continue
        match = _FROM.fullmatch(line)
        _require(match is not None, "dockerfile_parser_ambiguous")
        platform, reference, alias = match.groups()
        _require("$" not in reference, "input_base_mutable")
        normalized_alias = alias.lower() if alias else None
        _require(
            normalized_alias is None or _SAFE_ID.fullmatch(normalized_alias) is not None,
            "dockerfile_parser_ambiguous",
        )
        _require(normalized_alias != "scratch", "dockerfile_parser_ambiguous")
        _require(normalized_alias is None or normalized_alias not in aliases, "dockerfile_parser_ambiguous")
        prior_aliases = set(aliases)
        if normalized_alias:
            aliases.add(normalized_alias)
        ordinal = len(parsed) + 1
        stage_id = normalized_alias or f"stage-{ordinal}"
        declared = reference
        kind = "scratch" if declared == "scratch" else "external"
        if kind == "external":
            _require(_OCI_REFERENCE.fullmatch(declared) is not None, "input_base_mutable")
            _registry_host(declared)
        _require(platform is None or platform in _PLATFORMS, "input_platform_invalid")
        locked_platforms = (platform,) if platform else tuple(platforms)
        parsed.append((stage_id, ordinal, kind, declared, platform, locked_platforms))
    _require(parsed and len(parsed) == len(target_lock.bases), "input_lock_incomplete")
    for index, (actual, locked) in enumerate(zip(parsed, target_lock.bases, strict=True)):
        stage_id, ordinal, kind, reference, platform, locked_platforms = actual
        expected_marker = "final" if index == len(parsed) - 1 else "intermediate"
        _require(
            locked.stage_id == stage_id
            and locked.from_ordinal == ordinal
            and locked.stage_marker == expected_marker
            and locked.kind == kind
            and locked.declared_reference == reference
            and locked.dockerfile_platform == platform
            and locked.platforms == locked_platforms,
            "input_lock_mismatch",
        )
    return target_lock.bases
