"""Hermetic orchestration for source-only GitOps validation."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .gitops_render import (
    _helm_target,
    _kustomize_target,
    _policy,
    _yaml_target,
)
from .gitops_runtime import (
    GitOpsTools,
    _require,
    _safe_tar_member,
    assert_zero_gitops_residue,
    cleanup_gitops_state,
    initialize_gitops_state,
    prepare_gitops_tools,
)
from .gitops_source import (
    _canonical_documents,
    _object_identity,
    _selected_targets,
    _source_snapshot,
)
from .gitops_types import (
    GitOpsPlan,
    GitOpsResult,
    GitOpsTargetKind,
    GitOpsValidationError,
    ObjectIdentity,
    compact_json,
)


def _execute(
    plan: GitOpsPlan,
    source_root: Path,
    state_root: Path,
    tools: GitOpsTools,
) -> GitOpsResult:
    before = _source_snapshot(source_root, plan.request.admitted_sha)
    targets = _selected_targets(plan, source_root)
    owners: dict[ObjectIdentity, str] = {}
    all_documents: list[Any] = []
    validated_files = 0
    rendered_objects = 0
    for target in targets:
        if target.kind is GitOpsTargetKind.YAML:
            documents, count = _yaml_target(
                target,
                source_root,
                tools.yaml,
            )
        elif target.kind is GitOpsTargetKind.HELM:
            documents, count = _helm_target(
                target,
                source_root,
                state_root,
                tools,
            )
        else:
            documents, count = _kustomize_target(
                target,
                source_root,
                state_root,
                tools,
            )
        validated_files += count
        for document in documents:
            identity = _object_identity(document)
            if identity is not None:
                previous = owners.get(identity)
                _require(
                    previous is None,
                    "duplicate_object_ownership",
                    identity.label,
                )
                owners[identity] = target.target_id
                rendered_objects += 1
        all_documents.extend(documents)
    policy_result = _policy(plan, source_root, state_root)
    after = _source_snapshot(source_root, plan.request.admitted_sha)
    _require(before == after, "source_mutated")
    render_digest = hashlib.sha256(
        _canonical_documents(all_documents)
    ).hexdigest()
    evidence = hashlib.sha256(
        compact_json(
            {
                "source": plan.request.admitted_sha,
                "consumer": plan.request.consumer_contract,
                "profile": plan.request.validation_profile.value,
                "targets": [target.target_id for target in targets],
                "render": render_digest,
                "tools": dict(tools.versions),
            }
        ).encode("utf-8")
    ).hexdigest()[:32]
    return GitOpsResult(
        plan=plan,
        rendered_objects=rendered_objects,
        validated_files=validated_files,
        selected_targets=tuple(
            target.target_id
            for target in targets
        ),
        render_digest=render_digest,
        policy_result=policy_result,
        clean_tree=True,
        cleanup_result="not-run",
        evidence_id=f"gitops-{evidence}",
        tool_versions=dict(tools.versions),
    )


def execute_gitops_plan(
    plan: GitOpsPlan,
    source_root: Path,
    state_root: Path,
    *,
    tools: GitOpsTools | None = None,
) -> GitOpsResult:
    """Execute one exact plan and preserve primary plus cleanup failure."""

    if tools is None:
        runtime = prepare_gitops_tools(plan, state_root)
    else:
        initialize_gitops_state(state_root)
        runtime = tools
    try:
        return _execute(plan, source_root, state_root, runtime)
    except BaseException as primary:
        try:
            cleanup_gitops_state(state_root)
        except BaseException as cleanup:
            primary_code = getattr(
                primary,
                "code",
                type(primary).__name__,
            )
            cleanup_code = getattr(
                cleanup,
                "code",
                type(cleanup).__name__,
            )
            raise GitOpsValidationError(
                "primary_and_cleanup_failed",
                f"primary={primary_code};cleanup={cleanup_code}",
            ) from primary
        raise
