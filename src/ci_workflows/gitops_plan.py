"""Source-bound plan validation for checked-in GitOps consumer contracts."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .gitops_contract import (
    bounded_path,
    build_plan as _build_contract_plan,
    file_sha256,
)
from .gitops_runtime import _require
from .gitops_source import _glob_files
from .gitops_types import GitOpsPlan, GitOpsRequest

_GLOB_MARKERS = frozenset("*?[")


def _is_glob(value: str) -> bool:
    return any(marker in value for marker in _GLOB_MARKERS)


def build_gitops_plan(
    contract: Mapping[str, Any],
    request: GitOpsRequest,
    source_root: Path | None,
) -> GitOpsPlan:
    """Build one contract plan and validate its source-bound paths when present.

    The canonical contract parser owns target and policy shape.  This layer keeps
    source existence checks separate so reviewed ``sops_files`` entries may be
    bounded repository-relative glob patterns as well as exact paths.
    """

    plan = _build_contract_plan(contract, request, None)
    if source_root is None:
        return plan

    _require(
        source_root.is_dir() and not source_root.is_symlink(),
        "invalid_path",
    )
    for target in plan.targets:
        bounded_path(
            source_root,
            target.root,
            must_exist=True,
            kind="directory",
        )
        if target.schema_path:
            bounded_path(
                source_root,
                target.schema_path,
                must_exist=True,
                kind="file",
            )
        if target.expected_render_path:
            bounded_path(
                source_root,
                target.expected_render_path,
                must_exist=True,
                kind="file",
            )
        for relative in target.values_files:
            bounded_path(
                source_root,
                relative,
                must_exist=True,
                kind="file",
            )

        matched_sops = set()
        for relative in target.sops_files:
            if _is_glob(relative):
                matched_sops.update(_glob_files(source_root, (relative,)))
            else:
                matched_sops.add(
                    bounded_path(
                        source_root,
                        relative,
                        must_exist=True,
                        kind="file",
                    )
                )
        if target.sops_files:
            _require(
                bool(matched_sops),
                "sops_plaintext_rejected",
                target.target_id,
            )

        for dependency in target.vendored_dependencies:
            bounded_path(
                source_root,
                dependency.path,
                must_exist=True,
                kind="directory",
            )

    if plan.policy_script:
        path = bounded_path(
            source_root,
            plan.policy_script.path,
            must_exist=True,
            kind="file",
        )
        _require(
            file_sha256(path) == plan.policy_script.sha256,
            "policy_profile_rejected",
        )
    return plan
