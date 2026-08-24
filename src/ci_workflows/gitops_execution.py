"""Hermetic orchestration for source-only GitOps validation."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .gitops_composition import (
    selected_targets,
    source_render_overlap_allowed,
    style_paths_for_plan,
    yaml_target,
)
from .gitops_render import (
    _helm_target,
    _kustomize_target,
    _policy,
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
    _source_snapshot,
)
from .gitops_types import (
    GitOpsPlan,
    GitOpsResult,
    GitOpsTarget,
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
    targets = selected_targets(plan, source_root)
    style_paths = style_paths_for_plan(plan, source_root)
    owners: dict[
        ObjectIdentity,
        tuple[tuple[GitOpsTarget, Path | None], ...],
    ] = {}
    all_documents: list[Any] = []
    validated_files = 0
    rendered_objects = 0
    for target in targets:
        if target.kind is GitOpsTargetKind.YAML:
            sourced_documents, count = yaml_target(
                target,
                source_root,
                tools.yaml,
                style_paths=style_paths,
            )
            documents = [document for document, _ in sourced_documents]
            source_paths: list[Path | None] = [
                source_path
                for _, source_path in sourced_documents
            ]
        elif target.kind is GitOpsTargetKind.HELM:
            documents, count = _helm_target(
                target,
                source_root,
                state_root,
                tools,
            )
            source_paths = [None] * len(documents)
        else:
            documents, count = _kustomize_target(
                target,
                source_root,
                state_root,
                tools,
            )
            source_paths = [None] * len(documents)
        validated_files += count
        target_identities: set[ObjectIdentity] = set()
        for document, source_path in zip(documents, source_paths, strict=True):
            identity = _object_identity(document)
            if identity is not None:
                _require(
                    identity not in target_identities,
                    "duplicate_object_ownership",
                    identity.label,
                )
                target_identities.add(identity)
                previous = owners.get(identity, ())
                if previous:
                    previous_target, previous_source_path = previous[0]
                    _require(
                        len(previous) == 1
                        and source_render_overlap_allowed(
                            previous_target,
                            previous_source_path,
                            target,
                            source_path,
                            source_root,
                        ),
                        "duplicate_object_ownership",
                        identity.label,
                    )
                owners[identity] = (
                    *previous,
                    (target, source_path),
                )
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
