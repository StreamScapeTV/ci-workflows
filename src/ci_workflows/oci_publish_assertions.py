"""Typed OCI image assertions for local publication and remote read-back."""
from __future__ import annotations

import hashlib
import json
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from . import oci_contract
from . import oci_execution
from . import oci_execution_safe
from .oci_publish import OciPublishError, PublishPlan, PublishTarget
from .oci_types import OciBuildError, OciTarget


@dataclass(frozen=True)
class HealthcheckAssertion:
    """Exact Docker-compatible healthcheck expected in every platform config."""

    test: tuple[str, ...]
    interval_nanoseconds: int
    timeout_nanoseconds: int
    start_period_nanoseconds: int
    start_interval_nanoseconds: int
    retries: int

    def image_config(self) -> Mapping[str, object]:
        return {
            "Test": list(self.test),
            "Interval": self.interval_nanoseconds,
            "Timeout": self.timeout_nanoseconds,
            "StartPeriod": self.start_period_nanoseconds,
            "StartInterval": self.start_interval_nanoseconds,
            "Retries": self.retries,
        }


@dataclass(frozen=True)
class PublicationAssertions:
    """Publication-only assertions bound to a checked-in product target."""

    required_executables: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    healthcheck: HealthcheckAssertion | None


@dataclass(frozen=True)
class _FilesystemEntry:
    """Final overlay entry metadata needed by publication assertions."""

    regular_file: bool
    directory: bool
    executable: bool
    symlink_target: str | None


_HEALTHCHECK_FIELDS = {
    "test",
    "interval_nanoseconds",
    "timeout_nanoseconds",
    "start_period_nanoseconds",
    "start_interval_nanoseconds",
    "retries",
}
_IMAGE_HEALTHCHECK_FIELDS = {
    "Test",
    "Interval",
    "Timeout",
    "StartPeriod",
    "StartInterval",
    "Retries",
}


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise OciPublishError(code)


def _mapping(value: Any) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), "invalid_contract")
    return value


def _load_contract(repository_root: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(
            (repository_root / "contracts/oci-products.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as error:
        raise OciPublishError("publication_dependency_missing") from error
    return _mapping(payload)


def _strings(value: Any) -> tuple[str, ...]:
    _require(isinstance(value, list), "invalid_contract")
    _require(
        all(
            isinstance(item, str)
            and item
            and item == item.strip()
            and len(item) <= 4_096
            and all(character not in item for character in ("\x00", "\r", "\n"))
            for item in value
        ),
        "invalid_contract",
    )
    _require(len(value) == len(set(value)), "invalid_contract")
    return tuple(value)


def _absolute_paths(value: Any) -> tuple[str, ...]:
    paths = _strings(value)
    for item in paths:
        path = PurePosixPath(item)
        _require(
            path.is_absolute()
            and item.startswith("/")
            and not item.startswith("//")
            and item == path.as_posix()
            and ".." not in path.parts
            and item != "/",
            "invalid_contract",
        )
    return paths


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _sequence_summary(values: Sequence[str]) -> Mapping[str, object]:
    """Identify an exact vector without exposing its potentially sensitive text."""

    return {
        "count": len(values),
        "digest": _canonical_digest(list(values)),
    }


def _healthcheck_summary(
    healthcheck: HealthcheckAssertion | None,
) -> Mapping[str, object]:
    if healthcheck is None:
        return {"mode": "absent"}
    return {
        "mode": "exact",
        "test_mode": healthcheck.test[0],
        "test": _sequence_summary(healthcheck.test),
        "interval_nanoseconds": healthcheck.interval_nanoseconds,
        "timeout_nanoseconds": healthcheck.timeout_nanoseconds,
        "start_period_nanoseconds": healthcheck.start_period_nanoseconds,
        "start_interval_nanoseconds": healthcheck.start_interval_nanoseconds,
        "retries": healthcheck.retries,
    }


def _assertion_evidence(
    target: OciTarget,
    assertions: PublicationAssertions,
) -> Mapping[str, object]:
    """Return a deterministic redacted identity of the assertions just checked."""

    required_files = _absolute_paths(list(target.required_files))
    raw_contract = {
        "runtime": {
            "user": target.required_user,
            "entrypoint": list(target.required_entrypoint),
            "command": list(target.required_command),
            "ports": list(target.required_ports),
        },
        "filesystem": {
            "required_files": list(required_files),
            "required_tools": list(target.required_tools),
            "required_executables": list(assertions.required_executables),
            "forbidden_tools": list(target.forbidden_tools),
            "forbidden_paths": list(assertions.forbidden_paths),
        },
        "healthcheck": (
            None
            if assertions.healthcheck is None
            else {
                "test": list(assertions.healthcheck.test),
                "interval_nanoseconds": assertions.healthcheck.interval_nanoseconds,
                "timeout_nanoseconds": assertions.healthcheck.timeout_nanoseconds,
                "start_period_nanoseconds": (
                    assertions.healthcheck.start_period_nanoseconds
                ),
                "start_interval_nanoseconds": (
                    assertions.healthcheck.start_interval_nanoseconds
                ),
                "retries": assertions.healthcheck.retries,
            }
        ),
    }
    return {
        "result": "passed",
        "verified_platforms": list(target.platforms),
        "contract_digest": _canonical_digest(raw_contract),
        "runtime": {
            "user": target.required_user,
            "entrypoint": _sequence_summary(target.required_entrypoint),
            "command": _sequence_summary(target.required_command),
            "ports": list(target.required_ports),
        },
        "filesystem": raw_contract["filesystem"],
        "healthcheck": _healthcheck_summary(assertions.healthcheck),
    }


def _healthcheck(value: Any) -> HealthcheckAssertion | None:
    if value is None:
        return None
    raw = _mapping(value)
    _require(set(raw) == _HEALTHCHECK_FIELDS, "invalid_contract")
    test = _strings(raw["test"])
    _require(
        bool(test)
        and test[0] in {"CMD", "CMD-SHELL", "NONE"}
        and (test[0] != "NONE" or len(test) == 1)
        and (test[0] != "CMD-SHELL" or len(test) == 2)
        and (test[0] != "CMD" or len(test) >= 2),
        "invalid_contract",
    )
    integers: dict[str, int] = {}
    for field in _HEALTHCHECK_FIELDS - {"test"}:
        item = raw[field]
        upper_bound = (
            2_147_483_647
            if field == "retries"
            else 9_223_372_036_854_775_807
        )
        _require(
            type(item) is int and 0 <= item <= upper_bound,
            "invalid_contract",
        )
        integers[field] = item
    return HealthcheckAssertion(
        test=test,
        interval_nanoseconds=integers["interval_nanoseconds"],
        timeout_nanoseconds=integers["timeout_nanoseconds"],
        start_period_nanoseconds=integers["start_period_nanoseconds"],
        start_interval_nanoseconds=integers["start_interval_nanoseconds"],
        retries=integers["retries"],
    )


def _publication_assertions(
    contract: Mapping[str, Any],
    plan: PublishPlan,
    target: PublishTarget,
) -> PublicationAssertions:
    """Resolve one exact publication assertion row from checked-in inventory."""

    products = _mapping(contract.get("products"))
    all_assertions = _mapping(contract.get("publication_assertions"))
    _require(set(all_assertions) == set(products), "invalid_contract")
    parsed: dict[tuple[str, str], PublicationAssertions] = {}
    for product_id, raw_product in products.items():
        _require(isinstance(product_id, str), "invalid_contract")
        product = _mapping(raw_product)
        raw_targets = product.get("targets")
        _require(isinstance(raw_targets, list) and bool(raw_targets), "invalid_contract")
        target_ids: set[str] = set()
        for raw_target in raw_targets:
            target_id = _mapping(raw_target).get("target_id")
            _require(isinstance(target_id, str), "invalid_contract")
            target_ids.add(target_id)
        _require(len(target_ids) == len(raw_targets), "invalid_contract")
        assertion_targets = _mapping(all_assertions.get(product_id))
        _require(set(assertion_targets) == target_ids, "invalid_contract")
        for target_id in target_ids:
            raw = _mapping(assertion_targets.get(target_id))
            _require(
                set(raw)
                == {"required_executables", "forbidden_paths", "healthcheck"},
                "invalid_contract",
            )
            parsed[(product_id, target_id)] = PublicationAssertions(
                required_executables=_absolute_paths(raw["required_executables"]),
                forbidden_paths=_absolute_paths(raw["forbidden_paths"]),
                healthcheck=_healthcheck(raw["healthcheck"]),
            )
    key = (plan.product_id, target.target_id)
    _require(key in parsed, "invalid_contract")
    return parsed[key]


def _build_target(
    contract: Mapping[str, Any],
    plan: PublishPlan,
    target: PublishTarget,
) -> OciTarget:
    """Resolve one publication target through the authoritative #16 parser."""

    platform_sets = _mapping(contract.get("platform_sets"))
    products = _mapping(contract.get("products"))
    product = _mapping(products.get(plan.product_id))
    raw_targets = product.get("targets")
    _require(isinstance(raw_targets, list) and bool(raw_targets), "invalid_contract")
    matches = [
        _mapping(raw)
        for raw in raw_targets
        if isinstance(raw, Mapping) and raw.get("target_id") == target.target_id
    ]
    _require(len(matches) == 1, "invalid_contract")
    try:
        build_target = oci_contract._validate_target(  # noqa: SLF001
            matches[0], platform_sets
        )
    except OciBuildError as error:
        raise OciPublishError(error.code) from error
    _require(build_target.platforms == target.platforms, "platform_mismatch")
    return build_target


def _assert_filesystem_inventory(
    layout: Path,
    target: OciTarget,
    assertions: PublicationAssertions,
) -> None:
    try:
        inventories = [
            _layer_inventory(layout, layers)
            for layers in oci_execution_safe._manifest_layer_sets(layout)  # noqa: SLF001
        ]
    except OciBuildError as error:
        raise OciPublishError(error.code) from error
    _require(bool(inventories), "oci_layout_malformed")
    _require(
        {PurePosixPath(path).name for path in assertions.required_executables}
        >= set(target.required_tools),
        "invalid_contract",
    )
    for entries in inventories:
        _require(
            all(
                _resolved_entry(entries, path) is not None
                for path in target.required_files
            ),
            "assertion_failed",
        )
        basenames = {PurePosixPath(path).name for path in entries}
        _require(
            all(tool in basenames for tool in target.required_tools),
            "assertion_failed",
        )
        _require(
            all(
                _is_executable(entries, path)
                for path in assertions.required_executables
            ),
            "assertion_failed",
        )
        _require(
            all(tool not in basenames for tool in target.forbidden_tools),
            "assertion_failed",
        )
        for forbidden in assertions.forbidden_paths:
            _require(
                not _forbidden_path_present(entries, forbidden),
                "assertion_failed",
            )


def _resolve_inventory_path(
    entries: Mapping[str, _FilesystemEntry],
    path: str,
) -> str | None:
    """Resolve every existing symlink component within the container root."""

    pending = list(PurePosixPath(path).parts[1:])
    resolved: list[str] = []
    followed: set[str] = set()
    while pending:
        resolved.append(pending.pop(0))
        candidate = "/" + "/".join(resolved)
        entry = entries.get(candidate)
        if entry is None:
            continue
        if entry.symlink_target is not None:
            _require(candidate not in followed, "assertion_failed")
            followed.add(candidate)
            pending = list(PurePosixPath(entry.symlink_target).parts[1:]) + pending
            resolved = []
            continue
        if pending and not entry.directory:
            return None
    return "/" + "/".join(resolved)


def _resolved_entry(
    entries: Mapping[str, _FilesystemEntry],
    path: str,
) -> _FilesystemEntry | None:
    resolved = _resolve_inventory_path(entries, path)
    if resolved is None:
        return None
    return entries.get(resolved)


def _forbidden_path_present(
    entries: Mapping[str, _FilesystemEntry],
    path: str,
) -> bool:
    logical_prefix = path.rstrip("/") + "/"
    if any(
        candidate == path or candidate.startswith(logical_prefix)
        for candidate in entries
    ):
        return True
    resolved = _resolve_inventory_path(entries, path)
    if resolved is None:
        return False
    resolved_prefix = resolved.rstrip("/") + "/"
    return any(
        candidate == resolved or candidate.startswith(resolved_prefix)
        for candidate in entries
    )


def _is_executable(
    entries: Mapping[str, _FilesystemEntry],
    path: str,
) -> bool:
    entry = _resolved_entry(entries, path)
    return bool(entry is not None and entry.regular_file and entry.executable)


def _container_link_target(path: PurePosixPath, linkname: str) -> str:
    if not linkname or linkname.startswith("//"):
        raise OciBuildError("oci_layout_malformed")
    link = PurePosixPath(linkname)
    combined = link if link.is_absolute() else PurePosixPath("/") / path.parent / link
    parts: list[str] = []
    for part in combined.parts:
        if part in {"", "/", "."}:
            continue
        if part == "..":
            if not parts:
                raise OciBuildError("oci_layout_malformed")
            parts.pop()
            continue
        parts.append(part)
    if not parts:
        raise OciBuildError("oci_layout_malformed")
    return "/" + "/".join(parts)


def _layer_inventory(
    layout: Path,
    layer_digests: Sequence[str],
) -> Mapping[str, _FilesystemEntry]:
    """Stream an OCI layer stack into one final whiteout-aware inventory."""

    entries: dict[str, _FilesystemEntry] = {}
    for digest in layer_digests:
        blob = oci_execution._blob(layout, digest)  # noqa: SLF001
        try:
            archive = tarfile.open(name=blob, mode="r:*")
        except (OSError, tarfile.TarError) as error:
            raise OciBuildError("oci_layout_malformed") from error
        additions: list[tuple[str, _FilesystemEntry]] = []
        opaque_prefixes: set[str] = set()
        removed_paths: set[str] = set()
        with archive:
            for member in archive:
                pure = PurePosixPath(member.name)
                if pure.is_absolute() or ".." in pure.parts:
                    raise OciBuildError("oci_layout_malformed")
                if member.islnk():
                    if not member.linkname:
                        raise OciBuildError("oci_layout_malformed")
                    hardlink = PurePosixPath(member.linkname)
                    if hardlink.is_absolute() or ".." in hardlink.parts:
                        raise OciBuildError("oci_layout_malformed")
                name = pure.as_posix()
                if name in {"", "."}:
                    continue
                basename = pure.name
                parent = pure.parent.as_posix()
                directory = "/" if parent == "." else f"/{parent.rstrip('/')}"
                if basename == ".wh..wh..opq":
                    opaque_prefixes.add(directory.rstrip("/") + "/")
                    continue
                if basename.startswith(".wh."):
                    removed_name = basename.removeprefix(".wh.")
                    if not removed_name:
                        raise OciBuildError("oci_layout_malformed")
                    removed_paths.add(
                        directory.rstrip("/") + "/" + removed_name
                    )
                    continue
                additions.append(
                    (
                        "/" + name.rstrip("/"),
                        _FilesystemEntry(
                            regular_file=member.isfile(),
                            directory=member.isdir(),
                            executable=bool(member.mode & 0o111),
                            symlink_target=(
                                _container_link_target(pure, member.linkname)
                                if member.issym()
                                else None
                            ),
                        ),
                    ),
                )
        for prefix in opaque_prefixes:
            entries = {
                path: entry
                for path, entry in entries.items()
                if not path.startswith(prefix)
            }
        for removed in removed_paths:
            prefix = removed.rstrip("/") + "/"
            entries = {
                path: entry
                for path, entry in entries.items()
                if path != removed and not path.startswith(prefix)
            }
        for path, entry in additions:
            for ancestor in PurePosixPath(path).parents:
                if ancestor == PurePosixPath("/"):
                    break
                prior = entries.get(ancestor.as_posix())
                if prior is not None and not prior.directory:
                    raise OciBuildError("oci_layout_malformed")
            if not entry.directory:
                prefix = path.rstrip("/") + "/"
                entries = {
                    existing_path: existing_entry
                    for existing_path, existing_entry in entries.items()
                    if not existing_path.startswith(prefix)
                }
            entries[path] = entry
    return entries


def _image_configs(layout: Path) -> tuple[Mapping[str, Any], ...]:
    try:
        index = oci_execution._read_json(layout / "index.json")  # noqa: SLF001
        descriptors = oci_execution._image_manifest_descriptors(  # noqa: SLF001
            layout, index
        )
        configs: list[Mapping[str, Any]] = []
        for descriptor in descriptors:
            manifest_blob, _ = oci_execution._descriptor_blob(  # noqa: SLF001
                layout,
                descriptor,
                frozenset({oci_execution._MANIFEST_MEDIA_TYPE}),  # noqa: SLF001
            )
            manifest = oci_execution._read_json(manifest_blob)  # noqa: SLF001
            _require(isinstance(manifest, Mapping), "oci_layout_malformed")
            config_blob, _ = oci_execution._descriptor_blob(  # noqa: SLF001
                layout,
                manifest.get("config"),
                frozenset({oci_execution._CONFIG_MEDIA_TYPE}),  # noqa: SLF001
            )
            config = oci_execution._read_json(config_blob)  # noqa: SLF001
            _require(isinstance(config, Mapping), "oci_layout_malformed")
            configs.append(config)
    except OciPublishError:
        raise
    except OciBuildError as error:
        raise OciPublishError(error.code) from error
    except OSError as error:
        raise OciPublishError("oci_layout_malformed") from error
    _require(bool(configs), "oci_layout_malformed")
    return tuple(configs)


def _normalized_healthcheck(value: Any) -> Mapping[str, object]:
    _require(isinstance(value, Mapping), "assertion_failed")
    raw = value
    _require(set(raw) <= _IMAGE_HEALTHCHECK_FIELDS, "assertion_failed")
    test = raw.get("Test")
    _require(isinstance(test, list), "assertion_failed")
    _require(all(isinstance(item, str) for item in test), "assertion_failed")
    normalized: dict[str, object] = {"Test": test}
    for field in _IMAGE_HEALTHCHECK_FIELDS - {"Test"}:
        item = raw.get(field, 0)
        _require(type(item) is int and item >= 0, "assertion_failed")
        normalized[field] = item
    return normalized


def _assert_healthcheck(
    layout: Path,
    expected: HealthcheckAssertion | None,
) -> None:
    wanted = None if expected is None else expected.image_config()
    for config in _image_configs(layout):
        runtime = config.get("config")
        _require(isinstance(runtime, Mapping), "oci_layout_malformed")
        if expected is None:
            _require("Healthcheck" not in runtime, "assertion_failed")
        else:
            healthcheck = runtime.get("Healthcheck")
            _require(healthcheck is not None, "assertion_failed")
            _require(
                _normalized_healthcheck(healthcheck) == wanted,
                "assertion_failed",
            )


def assert_filesystem_contract(
    repository_root: Path,
    plan: PublishPlan,
    target: PublishTarget,
    layout: Path,
) -> Mapping[str, object]:
    """Prove checked-in filesystem/runtime state on a verified OCI layout."""

    contract = _load_contract(repository_root)
    build_target = _build_target(contract, plan, target)
    assertions = _publication_assertions(contract, plan, target)
    if (
        build_target.required_files
        or build_target.required_tools
        or build_target.forbidden_tools
        or assertions.required_executables
        or assertions.forbidden_paths
    ):
        _assert_filesystem_inventory(
            layout,
            build_target,
            assertions,
        )
    _assert_healthcheck(layout, assertions.healthcheck)
    return _assertion_evidence(build_target, assertions)
