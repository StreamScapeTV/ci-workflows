"""Typed Flux reconciliation plan grammar and exact-source helpers."""
from __future__ import annotations

import hashlib
import json
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .maintenance_contract import MaintenanceError

_DNS = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_PRODUCT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_SOURCE_KINDS = {"oci", "git", "helm", "bucket"}
_WORKLOAD_KINDS = {"deployment", "statefulset", "daemonset"}
_MAX_PLAN_BYTES = 64 * 1024

@dataclass(frozen=True)
class ResourceRef:
    name: str
    namespace: str
    kind: str = ""


@dataclass(frozen=True)
class WorkloadRef:
    kind: str
    name: str
    namespace: str


@dataclass(frozen=True)
class FluxPlan:
    admitted_sha: str
    target_id: str
    product_id: str
    operation: str
    request_id: str
    policy_sha256: str
    allowlist_sha256: str
    executor_sha256: str
    flux_source: ResourceRef | None
    kustomization: ResourceRef | None
    helm_release: ResourceRef | None
    workloads: tuple[WorkloadRef, ...]


def _bounded_path(root: Path, relative: str) -> Path:
    base = root.resolve()
    current = base
    for part in relative.split("/"):
        current /= part
        if current.is_symlink():
            raise MaintenanceError("flux_source_path_rejected")
    resolved = current.resolve(strict=False)
    if resolved == base or base not in resolved.parents:
        raise MaintenanceError("flux_source_path_rejected")
    return resolved


def _regular(root: Path, relative: str) -> Path:
    path = _bounded_path(root, relative)
    if not path.is_file() or path.is_symlink():
        raise MaintenanceError("flux_source_path_rejected")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(source: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=source,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MaintenanceError("flux_source_identity_invalid") from error
    if completed.returncode:
        raise MaintenanceError("flux_source_identity_invalid")
    return completed.stdout.strip()


def _clean(source: Path) -> bool:
    return not _git(source, "status", "--porcelain", "--untracked-files=all")


def _dns(value: Any, field: str) -> str:
    if not isinstance(value, str) or _DNS.fullmatch(value) is None:
        raise MaintenanceError(f"flux_plan_{field}_invalid")
    return value


def _resource(raw: Any, field: str, source: bool = False) -> ResourceRef | None:
    if raw is None or raw == "":
        return None
    if not isinstance(raw, Mapping):
        raise MaintenanceError("flux_policy_plan_invalid")
    expected = {"name", "namespace", "kind"} if source else {"name", "namespace"}
    if set(raw) != expected:
        raise MaintenanceError("flux_policy_plan_invalid")
    kind = str(raw.get("kind", ""))
    if source and kind not in _SOURCE_KINDS:
        raise MaintenanceError("flux_policy_plan_invalid")
    return ResourceRef(
        _dns(raw.get("name"), field + "_name"),
        _dns(raw.get("namespace"), field + "_namespace"),
        kind,
    )


def _workloads(raw: Any) -> tuple[WorkloadRef, ...]:
    if raw is None or raw == "":
        return ()
    if not isinstance(raw, list) or len(raw) > 20:
        raise MaintenanceError("flux_policy_plan_invalid")
    result: list[WorkloadRef] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"kind", "name", "namespace"}
            or item.get("kind") not in _WORKLOAD_KINDS
        ):
            raise MaintenanceError("flux_policy_plan_invalid")
        value = WorkloadRef(
            str(item["kind"]),
            _dns(item.get("name"), "workload_name"),
            _dns(item.get("namespace"), "workload_namespace"),
        )
        key = (value.kind, value.namespace, value.name)
        if key in seen:
            raise MaintenanceError("flux_policy_plan_invalid")
        seen.add(key)
        result.append(value)
    return tuple(result)


def _read_plan(
    path: Path,
    *,
    admitted_sha: str,
    target_id: str,
    product_id: str,
    operation: str,
    request_id: str,
    policy_sha256: str,
    allowlist_sha256: str,
    executor_sha256: str,
) -> FluxPlan:
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise MaintenanceError("flux_policy_plan_invalid")
        if metadata.st_size > _MAX_PLAN_BYTES:
            raise MaintenanceError("flux_policy_plan_invalid")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MaintenanceError("flux_policy_plan_invalid") from error
    expected = {
        "schema_version",
        "target_id",
        "product_id",
        "operation",
        "flux_source",
        "kustomization",
        "helm_release",
        "workloads",
    }
    if (
        not isinstance(raw, Mapping)
        or set(raw) != expected
        or raw.get("schema_version") != 1
        or raw.get("target_id") != target_id
        or raw.get("product_id") != product_id
        or raw.get("operation") != operation
    ):
        raise MaintenanceError("flux_policy_plan_invalid")
    source = _resource(raw.get("flux_source"), "flux_source", True)
    kustomization = _resource(raw.get("kustomization"), "kustomization")
    helm_release = _resource(raw.get("helm_release"), "helm_release")
    workloads = _workloads(raw.get("workloads"))
    if operation == "deploy" and not any((source, kustomization, helm_release)):
        raise MaintenanceError("flux_policy_plan_invalid")
    if operation == "restart" and not workloads:
        raise MaintenanceError("flux_policy_plan_invalid")
    return FluxPlan(
        admitted_sha,
        target_id,
        product_id,
        operation,
        request_id,
        policy_sha256,
        allowlist_sha256,
        executor_sha256,
        source,
        kustomization,
        helm_release,
        workloads,
    )


def _args(plan: FluxPlan) -> list[str]:
    source = plan.flux_source or ResourceRef("", "", "")
    kustomization = plan.kustomization or ResourceRef("", "")
    helm_release = plan.helm_release or ResourceRef("", "")
    workloads = json.dumps(
        [
            {"kind": item.kind, "name": item.name, "namespace": item.namespace}
            for item in plan.workloads
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return [
        "--operation",
        plan.operation,
        "--flux-source-kind",
        source.kind,
        "--flux-source-name",
        source.name,
        "--flux-source-namespace",
        source.namespace,
        "--kustomization-name",
        kustomization.name,
        "--kustomization-namespace",
        kustomization.namespace,
        "--helm-release-name",
        helm_release.name,
        "--helm-release-namespace",
        helm_release.namespace,
        "--workloads-json",
        workloads,
    ]
