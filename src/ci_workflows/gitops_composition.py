"""Composition helpers for bounded GitOps source and render validation."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from .gitops_contract import bounded_path
from .gitops_runtime import _require
from .gitops_source import (
    _changed_paths,
    _glob_files,
    _load_schema,
    _unique_loader,
    _validate_schema,
    _validate_sops,
)
from .gitops_types import (
    GitOpsPlan,
    GitOpsTarget,
    GitOpsTargetKind,
    GitOpsValidationError,
)


def _target_matches(target: GitOpsTarget, changed: Sequence[str]) -> bool:
    """Return whether a repository-relative changed path falls under a target root."""

    if target.root == ".":
        return bool(changed)
    prefix = target.root.rstrip("/") + "/"
    return any(
        path == target.root or path.startswith(prefix)
        for path in changed
    )


def selected_targets(
    plan: GitOpsPlan,
    source_root: Path,
) -> tuple[GitOpsTarget, ...]:
    """Select changed-tree targets without assuming Git paths start with ``./``."""

    if plan.request.validation_profile.value != "changed-tree":
        return plan.targets
    assert plan.request.change_base_sha is not None
    changed = _changed_paths(
        source_root,
        plan.request.change_base_sha,
        plan.request.admitted_sha,
    )
    return tuple(
        target
        for target in plan.targets
        if _target_matches(target, changed)
    )


def style_paths_for_plan(
    plan: GitOpsPlan,
    source_root: Path,
) -> frozenset[Path] | None:
    """Resolve the reviewed YAML style scope for one profile.

    ``None`` means strict style for every YAML source file.  An empty set means
    semantic parsing only.  ``changed-tree`` returns exact changed files, so
    historical formatting debt cannot false-red an unrelated change while a
    changed YAML file still receives strict style checks.
    """

    profile = plan.request.validation_profile.value
    if profile == "full":
        return frozenset()
    if profile != "changed-tree":
        return None

    assert plan.request.change_base_sha is not None
    changed = _changed_paths(
        source_root,
        plan.request.change_base_sha,
        plan.request.admitted_sha,
    )
    paths: set[Path] = set()
    for relative in changed:
        path = bounded_path(
            source_root,
            relative,
            must_exist=True,
            kind="file",
        )
        paths.add(path.resolve())
    return frozenset(paths)


def _yaml_documents(
    path: Path,
    yaml: Any,
    *,
    enforce_style: bool,
    display_path: str,
) -> list[Any]:
    """Parse YAML semantically and apply formatting only when the profile requires it."""

    try:
        raw = path.read_bytes()
    except OSError as error:
        raise GitOpsValidationError("yaml_invalid", display_path) from error
    _require(len(raw) <= 4_000_000, "yaml_invalid", display_path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GitOpsValidationError("yaml_invalid", display_path) from error

    if enforce_style:
        _require("\t" not in text, "yaml_style_failed", display_path)
        _require(
            not text or text.endswith("\n"),
            "yaml_style_failed",
            display_path,
        )
        for line in text.splitlines():
            _require(
                line.rstrip() == line,
                "yaml_style_failed",
                display_path,
            )

    try:
        documents = list(
            yaml.load_all(
                text,
                Loader=_unique_loader(yaml),
            )
        )
    except GitOpsValidationError:
        raise
    except Exception as error:
        raise GitOpsValidationError(
            "yaml_invalid",
            display_path,
        ) from error
    return [document for document in documents if document is not None]


def yaml_target(
    target: GitOpsTarget,
    source_root: Path,
    yaml: Any,
    *,
    style_paths: frozenset[Path] | None,
) -> tuple[list[tuple[Any, Path]], int]:
    """Validate YAML and retain each document's exact bounded source path."""

    source_root = source_root.resolve()
    root = bounded_path(
        source_root,
        target.root,
        must_exist=True,
        kind="directory",
    )
    files = _glob_files(root, target.include)
    _require(files, "yaml_invalid", target.target_id)
    schema = (
        _load_schema(source_root, target.schema_path, yaml)
        if target.schema_path
        else None
    )

    sops_paths: set[Path] = set()
    for relative in target.sops_files:
        if any(marker in relative for marker in "*?["):
            sops_paths.update(
                path.resolve()
                for path in _glob_files(source_root, (relative,))
            )
        else:
            sops_paths.add(
                bounded_path(
                    source_root,
                    relative,
                    must_exist=True,
                    kind="file",
                ).resolve()
            )
    if target.sops_files:
        _require(
            bool(sops_paths),
            "sops_plaintext_rejected",
            target.target_id,
        )

    included = {path.resolve() for path in files}
    _require(
        sops_paths <= included,
        "sops_plaintext_rejected",
        target.target_id,
    )

    documents: list[tuple[Any, Path]] = []
    for path in files:
        resolved = path.resolve()
        relative = path.relative_to(source_root).as_posix()
        loaded = _yaml_documents(
            path,
            yaml,
            enforce_style=(
                style_paths is None or resolved in style_paths
            ),
            display_path=relative,
        )
        for document in loaded:
            if schema is not None:
                _validate_schema(document, schema)
            if resolved in sops_paths:
                _validate_sops(document, relative)
            documents.append((document, resolved))
    return documents, len(files)


def source_render_overlap_allowed(
    previous_target: GitOpsTarget,
    previous_source_path: Path | None,
    current_target: GitOpsTarget,
    current_source_path: Path | None,
    source_root: Path,
) -> bool:
    """Allow only a raw document that physically belongs to one render root.

    Two source targets, two render targets, or a source identity from outside
    the renderer's bounded root still conflict.  The exception therefore
    composes source validation with its own deterministic nested render without
    becoming general duplicate suppression.
    """

    if (previous_target.kind is GitOpsTargetKind.YAML) == (
        current_target.kind is GitOpsTargetKind.YAML
    ):
        return False
    if previous_target.kind is GitOpsTargetKind.YAML:
        source_path = previous_source_path
        rendered = current_target
    else:
        source_path = current_source_path
        rendered = previous_target
    if source_path is None:
        return False
    render_root = bounded_path(
        source_root,
        rendered.root,
        must_exist=True,
        kind="directory",
    ).resolve()
    return render_root in source_path.parents
