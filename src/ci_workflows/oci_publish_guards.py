"""Fail-closed OCI publication guards over the issue-17 registry runtime."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from . import oci_publish as _runtime
from . import oci_publish_assertions as _assertions
from . import oci_contract as _build_contract
from . import oci_input_contract as _inputs
from .oci_types import oci_build_evidence_id

OciPublishError = _runtime.OciPublishError
PublishPlan = _runtime.PublishPlan
PublishTarget = _runtime.PublishTarget
cleanup = _runtime.cleanup
publication_state_root = _runtime.publication_state_root
replay_decision = _runtime.replay_decision
residue = _runtime.residue

ROOT = Path(__file__).resolve().parents[2]
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_EVIDENCE_ID = re.compile(r"^[0-9a-f]{64}$")
_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_ABSENCE_MARKERS = (b"manifest unknown", b"manifest_unknown", b"name unknown")
_MAXIMUM_TAG_LIST_BYTES = 1024 * 1024
_MAXIMUM_BUILD_RESULT_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class _PublishPreflight:
    target: PublishTarget
    layout: Path
    local: Mapping[str, Any]
    assertions: Mapping[str, object]
    local_digest: str
    publish_version: bool
    publish_source: bool
    replayed: bool
    layout_identity: tuple[int, int]


@dataclass(frozen=True)
class _BuildEvidence:
    identity: Mapping[str, str]
    resolved_inputs: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class _RepositoryTagListing:
    repository: str
    tags: tuple[str, ...]

    def contains_reference(self, reference: str) -> bool:
        prefix = f"{self.repository}:"
        _require(reference.startswith(prefix), "registry_inspection_failed")
        tag = reference[len(prefix) :]
        _require(_TAG.fullmatch(tag) is not None, "registry_inspection_failed")
        case_matches = tuple(
            candidate for candidate in self.tags if candidate.casefold() == tag.casefold()
        )
        _require(
            not case_matches or case_matches == (tag,),
            "registry_inspection_failed",
        )
        return bool(case_matches)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise OciPublishError(code)


def _mapping(value: Any, code: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), code)
    return value


def _encoded_mapping(value: Any, code: str) -> Mapping[str, Any]:
    _require(isinstance(value, str), code)
    try:
        return _mapping(json.loads(value), code)
    except json.JSONDecodeError as error:
        raise OciPublishError(code) from error


def _encoded_list(value: Any, code: str) -> list[Any]:
    _require(isinstance(value, str), code)
    try:
        decoded = json.loads(value, object_pairs_hook=_unique_json_object)
    except (json.JSONDecodeError, ValueError) as error:
        raise OciPublishError(code) from error
    _require(isinstance(decoded, list), code)
    return decoded


def _layout_identity(path: Path) -> tuple[int, int]:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise OciPublishError("oci_layout_malformed") from error
    _require(
        stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode),
        "oci_layout_malformed",
    )
    return metadata.st_dev, metadata.st_ino


def _read_build_result(root_fd: int) -> Mapping[str, Any]:
    """Read bounded private build state through no-follow descriptors."""

    descriptor = -1
    try:
        root_info = os.fstat(root_fd)
        _require(
            stat.S_ISDIR(root_info.st_mode)
            and stat.S_IMODE(root_info.st_mode) & 0o022 == 0,
            "build_evidence_missing",
        )
        descriptor = os.open(
            "result.json",
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=root_fd,
        )
        metadata = os.fstat(descriptor)
        linked = os.stat("result.json", dir_fd=root_fd, follow_symlinks=False)
        _require(
            stat.S_ISREG(metadata.st_mode)
            and stat.S_IMODE(metadata.st_mode) == 0o600
            and metadata.st_nlink == 1
            and 0 < metadata.st_size <= _MAXIMUM_BUILD_RESULT_BYTES
            and (metadata.st_dev, metadata.st_ino) == (linked.st_dev, linked.st_ino),
            "build_evidence_missing",
        )
        content = b""
        while len(content) <= _MAXIMUM_BUILD_RESULT_BYTES:
            chunk = os.read(
                descriptor, _MAXIMUM_BUILD_RESULT_BYTES + 1 - len(content)
            )
            if not chunk:
                break
            content += chunk
        _require(
            len(content) == metadata.st_size
            and len(content) <= _MAXIMUM_BUILD_RESULT_BYTES,
            "build_evidence_missing",
        )
        final = os.fstat(descriptor)
        linked_final = os.stat(
            "result.json", dir_fd=root_fd, follow_symlinks=False
        )
        _require(
            (final.st_dev, final.st_ino, final.st_size)
            == (metadata.st_dev, metadata.st_ino, metadata.st_size)
            and (linked_final.st_dev, linked_final.st_ino)
            == (metadata.st_dev, metadata.st_ino)
            and os.fstat(root_fd) == root_info,
            "build_evidence_missing",
        )
        decoded = json.loads(
            content.decode("utf-8"), object_pairs_hook=_unique_json_object
        )
        return _mapping(decoded, "build_evidence_missing")
    except OciPublishError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise OciPublishError("build_evidence_missing") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _build_identity(value: Any, plan: PublishPlan) -> Mapping[str, str]:
    identity = _mapping(value, "publication_state_missing")
    _require(
        set(identity)
        == {
            "source_sha",
            "product_id",
            "release_version",
            "evidence_id",
            "input_locks_validated",
        }
        and identity.get("source_sha") == plan.admitted_sha
        and identity.get("product_id") == plan.product_id
        and identity.get("release_version") == plan.release_version
        and identity.get("input_locks_validated") == "exact-source-v1"
        and isinstance(identity.get("evidence_id"), str)
        and _EVIDENCE_ID.fullmatch(identity["evidence_id"]) is not None,
        "publication_state_missing",
    )
    return {key: str(identity[key]) for key in sorted(identity)}


def _assert_exact_source(source_root: Path, admitted_sha: str) -> None:
    try:
        _runtime._execution.assert_clean_source(  # noqa: SLF001
            source_root, admitted_sha
        )
    except (
        _runtime._execution.OciBuildError,  # noqa: SLF001
        OSError,
        subprocess.CalledProcessError,
    ) as error:
        raise OciPublishError("build_evidence_mismatch") from error


def _tracked_source_path(source_root: Path, relative: str) -> Path:
    try:
        path = _build_contract.bounded_path(source_root, relative)
        tracked = _runtime._execution.execute_command(  # noqa: SLF001
            ["git", "ls-files", "--error-unmatch", "--", relative],
            cwd=source_root,
            capture=True,
        ).stdout.strip()
    except (
        _runtime._execution.OciBuildError,  # noqa: SLF001
        OSError,
        subprocess.CalledProcessError,
    ) as error:
        raise OciPublishError("build_evidence_mismatch") from error
    _require(tracked == relative and path.is_file() and not path.is_symlink(), "build_evidence_mismatch")
    return path


def _validated_source_input_locks(
    repository_root: Path,
    environment: Mapping[str, str],
    plan: PublishPlan,
) -> tuple[Path, Mapping[str, _inputs.OciTargetInputLock]]:
    """Bind publication evidence to the exact checked-out producer lock."""

    workspace_value = environment.get("GITHUB_WORKSPACE")
    _require(
        isinstance(workspace_value, str)
        and bool(workspace_value)
        and Path(workspace_value).is_absolute(),
        "build_evidence_mismatch",
    )
    try:
        source_root = _build_contract.bounded_path(
            Path(workspace_value), "source"
        ).resolve(strict=True)
    except _runtime._execution.OciBuildError as error:  # noqa: SLF001
        raise OciPublishError("build_evidence_mismatch") from error
    _assert_exact_source(source_root, plan.admitted_sha)

    contract = _runtime.load_product_contract(repository_root)
    products = _mapping(contract.get("products"), "build_evidence_mismatch")
    product = _mapping(products.get(plan.product_id), "build_evidence_mismatch")
    raw_targets = product.get("targets")
    _require(isinstance(raw_targets, list), "build_evidence_mismatch")
    targets: dict[str, Mapping[str, Any]] = {}
    for value in raw_targets:
        row = _mapping(value, "build_evidence_mismatch")
        target_id = row.get("target_id")
        _require(
            isinstance(target_id, str) and target_id not in targets,
            "build_evidence_mismatch",
        )
        targets[target_id] = row
    _require(
        set(targets) == {target.target_id for target in plan.targets},
        "build_evidence_mismatch",
    )

    locks: dict[str, _inputs.OciTargetInputLock] = {}
    for target in plan.targets:
        row = targets[target.target_id]
        lock_path = row.get("build_input_lock_path")
        dockerfile_path = row.get("dockerfile_path")
        _require(
            isinstance(lock_path, str)
            and isinstance(dockerfile_path, str),
            "build_evidence_mismatch",
        )
        _tracked_source_path(source_root, lock_path)
        dockerfile = _tracked_source_path(source_root, dockerfile_path)
        try:
            lock = _inputs.load_input_lock_contract(
                source_root,
                lock_path,
                product_id=plan.product_id,
                target_id=target.target_id,
                input_policy_id=target.input_policy_id,
                expected_platforms=target.platforms,
            )
            _inputs.validate_target_dockerfile_lock(
                dockerfile, lock, target.platforms
            )
        except _runtime._execution.OciBuildError as error:  # noqa: SLF001
            raise OciPublishError("build_evidence_mismatch") from error
        locks[target.target_id] = lock
    _assert_exact_source(source_root, plan.admitted_sha)
    return source_root, locks


def _load_build_evidence(
    build_root: Path,
    build_capacity: _runtime._execution.CapacityRoots,  # noqa: SLF001
    plan: PublishPlan,
    preflight: tuple[_PublishPreflight, ...],
    input_locks: Mapping[str, _inputs.OciTargetInputLock],
) -> _BuildEvidence:
    """Bind exact source/input evidence to every inspected local manifest."""

    try:
        parent_fds, _, leaf_fds, _ = (
            _runtime._execution._open_verified_capacity(  # noqa: SLF001
                build_capacity
            )
        )
    except _runtime._execution.OciBuildError as error:  # noqa: SLF001
        raise OciPublishError(error.code) from error
    try:
        payload = _read_build_result(leaf_fds["scratch"])
    finally:
        for descriptor in reversed(tuple(leaf_fds.values())):
            os.close(descriptor)
        for descriptor in reversed(tuple(parent_fds.values())):
            os.close(descriptor)
    _require(
        payload.get("result") == "success"
        and payload.get("source_sha") == plan.admitted_sha
        and payload.get("product_id") == plan.product_id
        and payload.get("release_version") == plan.release_version
        and payload.get("clean_tree") == "true"
        and payload.get("artifact_exception_used") == "false",
        "build_evidence_mismatch",
    )
    evidence_id = payload.get("evidence_id")
    _require(
        isinstance(evidence_id, str)
        and _EVIDENCE_ID.fullmatch(evidence_id) is not None,
        "build_evidence_mismatch",
    )
    manifests = _encoded_mapping(
        payload.get("publication_manifest_digests_json"),
        "build_evidence_mismatch",
    )
    index_manifests = _encoded_mapping(
        payload.get("manifest_digests_json"), "build_evidence_mismatch"
    )
    platform_results = _encoded_mapping(
        payload.get("platform_results_json"), "build_evidence_mismatch"
    )
    inputs = _encoded_mapping(
        payload.get("resolved_inputs_json"), "build_evidence_mismatch"
    )
    target_results = _encoded_list(
        payload.get("target_results_json"), "build_evidence_mismatch"
    )
    expected_targets = {item.target.target_id for item in preflight}
    _require(
        set(manifests) == expected_targets
        and set(index_manifests) == expected_targets
        and set(platform_results) == expected_targets
        and set(inputs) == expected_targets
        and set(input_locks) == expected_targets,
        "build_evidence_mismatch",
    )
    _require(
        payload.get("canary_id") == (plan.canary_id or "")
        and payload.get("previous_known_good")
        == (plan.previous_known_good or "")
        and payload.get("rollback_id") == (plan.rollback_id or "")
        and len(target_results) == len(preflight),
        "build_evidence_mismatch",
    )
    resolved: dict[str, Mapping[str, Any]] = {}
    normalized_targets: list[Mapping[str, object]] = []
    for item, raw_target_result in zip(preflight, target_results, strict=True):
        target_id = item.target.target_id
        target_result = _mapping(raw_target_result, "build_evidence_mismatch")
        _require(
            set(target_result)
            == {
                "target_id",
                "index_digest",
                "publication_manifest_digest",
                "platform_results",
                "labels",
                "smoke_result",
                "build_input_evidence",
            }
            and target_result.get("target_id") == target_id,
            "build_evidence_mismatch",
        )
        _require(
            manifests.get(target_id) == item.local_digest,
            "build_evidence_mismatch",
        )
        resolved[target_id] = _runtime._validate_resolved_input_evidence(  # noqa: SLF001
            inputs[target_id],
            item.target,
            "build_evidence_mismatch",
            expected_lock=input_locks[target_id],
        )
        local_platforms = _mapping(
            item.local.get("platforms"), "build_evidence_mismatch"
        )
        expected_platforms: list[dict[str, object]] = []
        labels: Mapping[str, Any] | None = None
        for platform in sorted(local_platforms):
            row = _mapping(
                local_platforms[platform], "build_evidence_mismatch"
            )
            row_labels = _mapping(row.get("labels"), "build_evidence_mismatch")
            if labels is None:
                labels = row_labels
            _require(row_labels == labels, "build_evidence_mismatch")
            expected_platforms.append(
                {
                    "platform": platform,
                    "manifest_digest": row.get("manifest_digest"),
                    "config_digest": row.get("config_digest"),
                    "layer_digests": row.get("layer_digests"),
                }
            )
        expected_index = _runtime._execution.sha256_file(  # noqa: SLF001
            item.layout / "index.json"
        )
        _require(
            index_manifests.get(target_id) == expected_index
            and platform_results.get(target_id) == expected_platforms
            and target_result.get("index_digest") == expected_index
            and target_result.get("publication_manifest_digest")
            == item.local_digest
            and target_result.get("platform_results") == expected_platforms
            and target_result.get("labels") == labels
            and target_result.get("smoke_result")
            in {
                "inspection-passed",
                "isolated-script-passed",
                "inspection-passed-script-deferred",
            }
            and target_result.get("build_input_evidence") == resolved[target_id],
            "build_evidence_mismatch",
        )
        normalized_targets.append(target_result)
    _require(
        oci_build_evidence_id(
            plan.admitted_sha,
            plan.product_id,
            plan.release_version,
            normalized_targets,
            plan.canary_id,
            plan.previous_known_good,
            plan.rollback_id,
        )
        == evidence_id,
        "build_evidence_mismatch",
    )
    return _BuildEvidence(
        identity={
            "source_sha": plan.admitted_sha,
            "product_id": plan.product_id,
            "release_version": plan.release_version,
            "evidence_id": evidence_id,
            "input_locks_validated": "exact-source-v1",
        },
        resolved_inputs=resolved,
    )


def _validate_layout_marker(layout: Path) -> None:
    marker = layout / "oci-layout"
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OciPublishError("oci_layout_malformed") from error
    _require(
        value == {"imageLayoutVersion": "1.0.0"}
        and marker.is_file()
        and not marker.is_symlink(),
        "oci_layout_malformed",
    )


def inspect_layout(
    layout: Path, target: PublishTarget, ref_name: str
) -> Mapping[str, Any]:
    """Inspect an OCI layout only after validating the OCI layout marker."""

    _validate_layout_marker(layout)
    return _runtime.inspect_layout(layout, target, ref_name)


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _list_repository_tags(
    target: PublishTarget,
    authfile: Path | _runtime._AuthfileBinding,  # noqa: SLF001
    capacity_roots: _runtime._execution.CapacityRoots,  # noqa: SLF001
) -> _RepositoryTagListing:
    """Return one authenticated, exact, ambiguity-free repository tag listing."""

    try:
        result = _runtime._run(  # noqa: SLF001 - bounded shared runtime primitive
            [
                "skopeo",
                "list-tags",
                "--authfile",
                str(_runtime._authfile_path(authfile)),  # noqa: SLF001
                f"docker://{target.registry_repository}",
            ],
            check=False,
            capacity_roots=capacity_roots,
            stdout_limit=_MAXIMUM_TAG_LIST_BYTES,
            stderr_limit=_runtime._MAX_REGISTRY_INSPECTION_STDERR_BYTES,  # noqa: SLF001
            overflow_code="registry_inspection_failed",
            expected_auth_state=_runtime._authfile_expected_state(authfile),  # noqa: SLF001
        )
    except OSError as error:
        raise OciPublishError("registry_inspection_failed") from error
    _require(result.returncode == 0, "registry_inspection_failed")
    _require(
        0 < len(result.stdout) <= _MAXIMUM_TAG_LIST_BYTES,
        "registry_inspection_failed",
    )
    try:
        payload = json.loads(
            result.stdout.decode("utf-8"), object_pairs_hook=_unique_json_object
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise OciPublishError("registry_inspection_failed") from error
    listing = _mapping(payload, "registry_inspection_failed")
    _require(
        set(listing) == {"Repository", "Tags"}
        and listing.get("Repository") == target.registry_repository,
        "registry_inspection_failed",
    )
    raw_tags = listing.get("Tags")
    _require(isinstance(raw_tags, list), "registry_inspection_failed")
    tags: list[str] = []
    unique: set[str] = set()
    for tag in raw_tags:
        _require(
            isinstance(tag, str)
            and _TAG.fullmatch(tag) is not None
            and tag not in unique,
            "registry_inspection_failed",
        )
        tags.append(tag)
        unique.add(tag)
    result_listing = _RepositoryTagListing(
        repository=target.registry_repository,
        tags=tuple(tags),
    )
    # Both requested identities must be either exactly absent or exactly present;
    # contains_reference rejects a case-variant spelling as ambiguous.
    result_listing.contains_reference(target.version_reference)
    result_listing.contains_reference(target.source_reference)
    return result_listing


def _inspect_remote_digest(
    reference: str,
    authfile: Path | _runtime._AuthfileBinding,  # noqa: SLF001
    capacity_roots: _runtime._execution.CapacityRoots,  # noqa: SLF001
) -> str | None:
    """Return None only for explicit registry manifest/name absence."""

    try:
        result = _runtime._run(  # noqa: SLF001 - bounded shared runtime primitive
            [
                "skopeo",
                "inspect",
                "--authfile",
                str(_runtime._authfile_path(authfile)),  # noqa: SLF001
                "--raw",
                f"docker://{reference}",
            ],
            check=False,
            capacity_roots=capacity_roots,
            stdout_limit=_runtime._MAX_REGISTRY_RAW_MANIFEST_BYTES,  # noqa: SLF001
            stderr_limit=_runtime._MAX_REGISTRY_INSPECTION_STDERR_BYTES,  # noqa: SLF001
            overflow_code="registry_inspection_failed",
            expected_auth_state=_runtime._authfile_expected_state(authfile),  # noqa: SLF001
        )
    except OSError as error:
        raise OciPublishError("registry_inspection_failed") from error
    if result.returncode == 0:
        _require(bool(result.stdout), "registry_inspection_failed")
        return "sha256:" + hashlib.sha256(result.stdout).hexdigest()
    stderr = result.stderr.lower()
    if any(marker in stderr for marker in _ABSENCE_MARKERS):
        return None
    raise OciPublishError("registry_inspection_failed")


def _publication_allowed(plan: PublishPlan, environment: Mapping[str, str]) -> bool:
    """Return true only for an exact version-tag push; manual calls are read-only."""

    event = environment.get("GITHUB_EVENT_NAME", "")
    if event == "push":
        _require(
            environment.get("GITHUB_REF_TYPE") == "tag"
            and environment.get("GITHUB_REF_NAME") == plan.release_version
            and environment.get("GITHUB_REF") == f"refs/tags/{plan.release_version}",
            "publication_ref_forbidden",
        )
        return True
    if event == "workflow_dispatch":
        return False
    raise OciPublishError("publication_untrusted")


def authenticate(
    plan: PublishPlan,
    environment: Mapping[str, str],
    username: str,
    token: str,
    *,
    _capacity_roots: _runtime._execution.CapacityRoots | None = None,  # noqa: SLF001
) -> dict[str, str]:
    """Create registry auth only after the caller event is publication-eligible."""

    _publication_allowed(plan, environment)
    return _runtime.authenticate(
        plan,
        environment,
        username,
        token,
        _capacity_roots=_capacity_roots,
    )


def _preflight_publish(
    plan: PublishPlan,
    contract_root: Path,
    build_root: Path,
    authfile: Path,
    capacity_roots: _runtime._execution.CapacityRoots,  # noqa: SLF001
    *,
    allow_publish: bool,
) -> tuple[_PublishPreflight, ...]:
    """Validate every target and decide all remote writes without mutating them."""

    listings = tuple(
        _list_repository_tags(target, authfile, capacity_roots)
        for target in plan.targets
    )
    decisions: list[_PublishPreflight] = []
    for target, listing in zip(plan.targets, listings, strict=True):
        layout = build_root / "layouts" / target.target_id
        layout_identity = _layout_identity(layout)
        local = inspect_layout(layout, target, "validation")
        assertion_evidence = _assertions.assert_filesystem_contract(
            contract_root, plan, target, layout
        )
        _require(
            _layout_identity(layout) == layout_identity,
            "oci_layout_malformed",
        )
        local_digest = str(local["manifest_digest"])
        _require(_DIGEST.fullmatch(local_digest) is not None, "oci_digest_mismatch")
        version_digest = (
            _inspect_remote_digest(
                target.version_reference, authfile, capacity_roots
            )
            if listing.contains_reference(target.version_reference)
            else None
        )
        source_digest = (
            _inspect_remote_digest(
                target.source_reference, authfile, capacity_roots
            )
            if listing.contains_reference(target.source_reference)
            else None
        )
        _require(
            not listing.contains_reference(target.version_reference)
            or version_digest is not None,
            "registry_inspection_failed",
        )
        _require(
            not listing.contains_reference(target.source_reference)
            or source_digest is not None,
            "registry_inspection_failed",
        )
        publish_version, publish_source, replayed = replay_decision(
            local_digest, version_digest, source_digest
        )
        if not allow_publish and (publish_version or publish_source):
            raise OciPublishError("remote_reference_missing")
        decisions.append(
            _PublishPreflight(
                target=target,
                layout=layout,
                local=local,
                assertions=assertion_evidence,
                local_digest=local_digest,
                publish_version=publish_version,
                publish_source=publish_source,
                replayed=replayed,
                layout_identity=layout_identity,
            )
        )
    return tuple(decisions)


def publish(
    plan: PublishPlan,
    environment: Mapping[str, str],
    *,
    allow_publish: bool | None = None,
    repository_root: Path | None = None,
    _capacity_roots: _runtime._execution.CapacityRoots | None = None,  # noqa: SLF001
    _build_capacity_roots: _runtime._execution.CapacityRoots | None = None,  # noqa: SLF001
) -> dict[str, str]:
    """Publish missing immutable refs or verify an existing pair without writes."""

    if allow_publish is None:
        allow_publish = _publication_allowed(plan, environment)
    _require(isinstance(allow_publish, bool), "invalid_request")
    contract_root = ROOT if repository_root is None else repository_root
    capacity_roots = _runtime.publication_capacity_roots(
        environment, _capacity_roots=_capacity_roots
    )
    _runtime._validate_active_publication_capacity(  # noqa: SLF001
        capacity_roots
    )
    root = capacity_roots.scratch_root
    authfile = _runtime._load_publication_plan_state(  # noqa: SLF001
        plan, capacity_roots
    )
    _runtime._require_new_state_path(  # noqa: SLF001
        capacity_roots, "publication.json"
    )
    build_capacity = _runtime.publication_build_capacity_roots(
        environment, _capacity_roots=_build_capacity_roots
    )
    _runtime._validate_active_build_capacity(build_capacity)  # noqa: SLF001
    build_root = build_capacity.scratch_root
    source_root, input_locks = _validated_source_input_locks(
        contract_root, environment, plan
    )
    preflight = _preflight_publish(
        plan,
        contract_root,
        build_root,
        authfile,
        capacity_roots,
        allow_publish=allow_publish,
    )
    _runtime._validate_active_build_capacity(build_capacity)  # noqa: SLF001
    build_evidence = _load_build_evidence(
        build_root, build_capacity, plan, preflight, input_locks
    )
    _runtime._validate_active_build_capacity(build_capacity)  # noqa: SLF001
    for item in preflight:
        _require(
            _layout_identity(item.layout) == item.layout_identity,
            "oci_layout_malformed",
        )
    preflight_manifests = {
        item.target.target_id: item.local_digest for item in preflight
    }
    preflight_platforms = {
        item.target.target_id: item.local["platforms"] for item in preflight
    }
    preflight_immutable: dict[str, Any] = {
        "registry_write_policy": _runtime.registry_write_policy_evidence(plan),
        "targets": {
            item.target.target_id: {
                "repository": item.target.registry_repository,
                "version": item.target.version_reference,
                "source_reference": item.target.source_reference,
                "manifest_digest": item.local_digest,
                "resolved_inputs": build_evidence.resolved_inputs[
                    item.target.target_id
                ],
                "assertions": item.assertions,
            }
            for item in preflight
        },
        "release": {
            "source_sha": plan.admitted_sha,
            "version": plan.release_version,
        },
    }
    if plan.flux_asset:
        preflight_immutable["flux"] = {
            "canary_id": plan.canary_id,
            "previous_known_good": plan.previous_known_good,
            "rollback_id": plan.rollback_id,
        }
    _runtime.public_json_outputs(
        plan.targets,
        preflight_manifests,
        preflight_platforms,
        preflight_immutable,
    )
    _assert_exact_source(source_root, plan.admitted_sha)
    any_replayed = any(item.replayed for item in preflight)
    any_published = False
    for item in preflight:
        target = item.target
        _runtime._validate_active_build_capacity(build_capacity)  # noqa: SLF001
        _require(
            _layout_identity(item.layout) == item.layout_identity,
            "oci_layout_malformed",
        )
        if item.publish_version:
            observed = _inspect_remote_digest(
                target.version_reference, authfile, capacity_roots
            )
            if observed is None:
                _runtime._copy(  # noqa: SLF001
                    f"oci:{item.layout}:validation",
                    f"docker://{target.version_reference}",
                    authfile,
                    capacity_roots,
                )
                any_published = True
            else:
                _require(observed == item.local_digest, "immutable_reference_conflict")
                any_replayed = True
        _runtime._validate_active_build_capacity(build_capacity)  # noqa: SLF001
        _require(
            _layout_identity(item.layout) == item.layout_identity,
            "oci_layout_malformed",
        )
        if item.publish_source:
            observed = _inspect_remote_digest(
                target.source_reference, authfile, capacity_roots
            )
            if observed is None:
                _runtime._copy(  # noqa: SLF001
                    f"oci:{item.layout}:validation",
                    f"docker://{target.source_reference}",
                    authfile,
                    capacity_roots,
                )
                any_published = True
            else:
                _require(observed == item.local_digest, "immutable_reference_conflict")
                any_replayed = True

    post_write_listings = tuple(
        _list_repository_tags(item.target, authfile, capacity_roots)
        for item in preflight
    )
    for item, listing in zip(preflight, post_write_listings, strict=True):
        _require(
            listing.contains_reference(item.target.version_reference)
            and listing.contains_reference(item.target.source_reference),
            "registry_inspection_failed",
        )

    results: dict[str, Any] = {}
    for item in preflight:
        target = item.target
        verified_version = _inspect_remote_digest(
            target.version_reference, authfile, capacity_roots
        )
        verified_source = _inspect_remote_digest(
            target.source_reference, authfile, capacity_roots
        )
        _require(
            verified_version == item.local_digest
            and verified_source == item.local_digest,
            "registry_digest_mismatch",
        )
        results[target.target_id] = {
            "repository": target.registry_repository,
            "version_reference": target.version_reference,
            "source_reference": target.source_reference,
            "manifest_digest": item.local_digest,
            "resolved_inputs": build_evidence.resolved_inputs[target.target_id],
            "assertions": item.assertions,
            "local": item.local,
            "replayed": item.replayed,
        }
    _assert_exact_source(source_root, plan.admitted_sha)
    _runtime._write_state(  # noqa: SLF001
        capacity_roots,
        "publication.json",
        {"build": build_evidence.identity, "targets": results},
    )
    return {
        "result": "published" if any_published else "replayed",
        "manifest_digests_json": json.dumps(
            {key: row["manifest_digest"] for key, row in sorted(results.items())},
            separators=(",", ":"),
        ),
        "replayed": str(any_replayed or not any_published).lower(),
        "failure_code": "",
    }


def read_back(
    plan: PublishPlan,
    environment: Mapping[str, str],
    *,
    repository_root: Path | None = None,
    _capacity_roots: _runtime._execution.CapacityRoots | None = None,  # noqa: SLF001
) -> dict[str, str]:
    """Independently copy exact registry bytes into fresh marker-checked layouts."""

    contract_root = ROOT if repository_root is None else repository_root
    capacity_roots = _runtime.publication_capacity_roots(
        environment, _capacity_roots=_capacity_roots
    )
    _runtime._validate_active_publication_capacity(  # noqa: SLF001
        capacity_roots
    )
    root = capacity_roots.scratch_root
    authfile = _runtime._load_publication_plan_state(  # noqa: SLF001
        plan, capacity_roots
    )
    _runtime._require_new_state_path(  # noqa: SLF001
        capacity_roots, "readback.json"
    )
    published = _runtime._read_state(  # noqa: SLF001
        capacity_roots, "publication.json"
    )
    rows = _mapping(published.get("targets"), "publication_state_missing")
    build_identity = _build_identity(published.get("build"), plan)
    _require(
        set(rows) == {target.target_id for target in plan.targets},
        "publication_state_missing",
    )
    preserved_inputs: dict[str, Mapping[str, Any]] = {}
    for target in plan.targets:
        row = _mapping(rows.get(target.target_id), "publication_state_missing")
        preserved_inputs[target.target_id] = (
            _runtime._validate_resolved_input_evidence(  # noqa: SLF001
                row.get("resolved_inputs"),
                target,
                "publication_state_missing",
            )
        )
    readback_root = root / "readback"
    _require(
        not readback_root.exists() and not readback_root.is_symlink(),
        "residue_detected",
    )
    readback_root.mkdir(mode=0o700)
    verified: dict[str, Any] = {}
    for target in plan.targets:
        row = _mapping(rows.get(target.target_id), "publication_state_missing")
        destination = readback_root / target.target_id
        _runtime._copy(  # noqa: SLF001
            f"docker://{target.version_reference}",
            f"oci:{destination}:readback",
            authfile,
            capacity_roots,
        )
        remote = inspect_layout(destination, target, "readback")
        remote_assertions = _assertions.assert_filesystem_contract(
            contract_root, plan, target, destination
        )
        local = _mapping(row.get("local"), "publication_state_missing")
        local_assertions = _mapping(
            row.get("assertions"), "publication_state_missing"
        )
        _require(remote == local, "registry_readback_mismatch")
        _require(
            remote_assertions == local_assertions,
            "registry_readback_mismatch",
        )
        source_digest = _inspect_remote_digest(
            target.source_reference, authfile, capacity_roots
        )
        _require(
            source_digest == remote["manifest_digest"],
            "registry_readback_mismatch",
        )
        verified[target.target_id] = {
            "repository": target.registry_repository,
            "version_reference": target.version_reference,
            "source_reference": target.source_reference,
            "manifest_digest": remote["manifest_digest"],
            "platforms": remote["platforms"],
            "resolved_inputs": preserved_inputs[target.target_id],
            "assertions": remote_assertions,
            "replayed": bool(row.get("replayed")),
        }
    _runtime._write_state(  # noqa: SLF001
        capacity_roots,
        "readback.json",
        {"build": build_identity, "targets": verified},
    )
    return {
        "result": "read-back",
        "manifest_digests_json": json.dumps(
            {key: row["manifest_digest"] for key, row in sorted(verified.items())},
            separators=(",", ":"),
        ),
        "platform_digests_json": json.dumps(
            {key: row["platforms"] for key, row in sorted(verified.items())},
            sort_keys=True,
            separators=(",", ":"),
        ),
        "assertion_evidence_json": json.dumps(
            {key: row["assertions"] for key, row in sorted(verified.items())},
            sort_keys=True,
            separators=(",", ":"),
        ),
        "failure_code": "",
    }
