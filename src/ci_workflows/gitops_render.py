"""Deterministic YAML, Helm, Kustomize, and policy rendering."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from .gitops_contract import bounded_path, file_sha256
from .gitops_runtime import (
    GitOpsTools,
    _FORBIDDEN_POLICY_ENV,
    _fail,
    _require,
    _run,
    _tool_environment,
)
from .gitops_source import (
    _canonical_documents,
    _glob_files,
    _load_schema,
    _tree_digest,
    _unique_loader,
    _validate_schema,
    _validate_sops,
    _yaml_documents,
)
from .gitops_types import (
    GitOpsPlan,
    GitOpsTarget,
    GitOpsValidationError,
)


def _is_helm_template_source(path: Path, root: Path) -> bool:
    """Classify only template source beneath an actual checked-in Helm chart."""

    relative = path.relative_to(root)
    for index, part in enumerate(relative.parts[:-1]):
        if part != "templates":
            continue
        chart_root = root.joinpath(*relative.parts[:index])
        chart_path = chart_root / "Chart.yaml"
        if chart_path.is_file() and not chart_path.is_symlink():
            return True
    return False


def _yaml_target(
    target: GitOpsTarget,
    source_root: Path,
    yaml: Any,
) -> tuple[list[Any], int]:
    # Canonicalize once so macOS's /var -> /private/var alias cannot turn a
    # previously bounded path into an apparent path-containment escape.
    source_root = source_root.resolve()
    root = bounded_path(
        source_root,
        target.root,
        must_exist=True,
        kind="directory",
    )
    matched_files = _glob_files(root, target.include)
    _require(matched_files, "yaml_invalid", target.target_id)
    files = tuple(
        path
        for path in matched_files
        if not _is_helm_template_source(path, root)
    )
    schema = (
        _load_schema(source_root, target.schema_path, yaml)
        if target.schema_path
        else None
    )
    documents: list[Any] = []
    sops_paths = {
        bounded_path(
            source_root,
            relative,
            must_exist=True,
            kind="file",
        ).resolve()
        for relative in target.sops_files
    }
    for path in files:
        loaded = _yaml_documents(path, yaml)
        for document in loaded:
            if schema is not None:
                _validate_schema(document, schema)
            if path.resolve() in sops_paths:
                _validate_sops(
                    document,
                    path.relative_to(source_root).as_posix(),
                )
        documents.extend(loaded)
    _require(
        sops_paths <= {path.resolve() for path in files},
        "sops_plaintext_rejected",
    )
    return documents, len(files)


def _nested_value(value: Any, dotted: str) -> Any:
    current = value
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            _fail("required_value_missing", dotted)
        current = current[part]
    _require(
        current not in {None, ""},
        "required_value_missing",
        dotted,
    )
    return current


def _canonical_dependency_digest(dependencies: Any) -> str:
    encoded = json.dumps(
        dependencies,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _helm_target(
    target: GitOpsTarget,
    source_root: Path,
    state_root: Path,
    tools: GitOpsTools,
) -> tuple[list[Any], int]:
    yaml = tools.yaml
    helm = tools.binaries.get("helm")
    _require(helm is not None, "tool_identity_mismatch", "helm")
    root = bounded_path(
        source_root,
        target.root,
        must_exist=True,
        kind="directory",
    )
    chart_path = root / "Chart.yaml"
    _require(
        chart_path.is_file() and not chart_path.is_symlink(),
        "helm_lock_invalid",
    )
    charts = _yaml_documents(chart_path, yaml)
    _require(
        len(charts) == 1 and isinstance(charts[0], dict),
        "helm_lock_invalid",
    )
    chart = charts[0]
    dependencies = chart.get("dependencies", [])
    _require(isinstance(dependencies, list), "helm_lock_invalid")
    lock_path = root / "Chart.lock"
    if dependencies:
        _require(
            lock_path.is_file() and not lock_path.is_symlink(),
            "helm_lock_invalid",
        )
        locks = _yaml_documents(lock_path, yaml)
        _require(
            len(locks) == 1 and isinstance(locks[0], dict),
            "helm_lock_invalid",
        )
        lock = locks[0]
        _require(
            lock.get("dependencies") == dependencies,
            "helm_lock_invalid",
        )
        _require(
            lock.get("digest")
            == _canonical_dependency_digest(dependencies),
            "helm_lock_invalid",
        )
    else:
        _require(
            not target.vendored_dependencies,
            "helm_lock_invalid",
        )
    _require(
        all(isinstance(row, dict) for row in dependencies),
        "helm_lock_invalid",
    )
    declared = {
        (
            str(row.get("name")),
            str(row.get("version")),
        ): str(row.get("repository", ""))
        for row in dependencies
    }
    _require(
        len(declared) == len(dependencies),
        "helm_lock_invalid",
    )
    _require(
        set(declared)
        == {
            (dependency.name, dependency.version)
            for dependency in target.vendored_dependencies
        },
        "helm_lock_invalid",
    )
    for row in dependencies:
        version = str(row.get("version", ""))
        repository = str(row.get("repository", ""))
        _require(
            re.fullmatch(
                r"[0-9]+\.[0-9]+\.[0-9]+",
                version,
            )
            is not None,
            "helm_lock_invalid",
        )
        _require(
            repository.startswith("file://")
            and ".." not in PurePosixPath(repository[7:]).parts,
            "helm_lock_invalid",
        )
    for dependency in target.vendored_dependencies:
        _require(
            (dependency.name, dependency.version) in declared,
            "helm_lock_invalid",
        )
        path = bounded_path(
            source_root,
            dependency.path,
            must_exist=True,
            kind="directory",
        )
        _require(
            root == path or root in path.parents,
            "helm_lock_invalid",
            dependency.name,
        )
        _require(
            declared[(dependency.name, dependency.version)]
            == f"file://{path.relative_to(root).as_posix()}",
            "helm_lock_invalid",
            dependency.name,
        )
        _require(
            _tree_digest(path) == dependency.tree_sha256,
            "helm_lock_invalid",
            dependency.name,
        )
        metadata = _yaml_documents(path / "Chart.yaml", yaml)
        _require(
            len(metadata) == 1
            and isinstance(metadata[0], dict)
            and metadata[0].get("name") == dependency.name
            and str(metadata[0].get("version"))
            == dependency.version,
            "helm_lock_invalid",
            dependency.name,
        )
    values: dict[str, Any] = {}
    value_args: list[str] = []
    for relative in target.values_files:
        path = bounded_path(
            source_root,
            relative,
            must_exist=True,
            kind="file",
        )
        loaded = _yaml_documents(path, yaml)
        _require(
            len(loaded) == 1 and isinstance(loaded[0], dict),
            "helm_failed",
        )
        values.update(loaded[0])
        value_args.extend(("--values", str(path)))
    for dotted in target.required_values:
        _nested_value(values, dotted)
    environment = _tool_environment(state_root)
    lint = (
        str(helm),
        "lint",
        str(root),
        "--strict",
        *value_args,
    )
    _run(
        lint,
        cwd=source_root,
        environment=environment,
        timeout=120,
        max_output=512_000,
        code="helm_failed",
    )
    template = (
        str(helm),
        "template",
        "gitops-validation",
        str(root),
        "--namespace",
        "gitops-validation",
        "--include-crds",
        *value_args,
    )
    first = _run(
        template,
        cwd=source_root,
        environment=environment,
        timeout=120,
        max_output=4_000_000,
        code="helm_failed",
    )
    second = _run(
        template,
        cwd=source_root,
        environment=environment,
        timeout=120,
        max_output=4_000_000,
        code="helm_failed",
    )
    _require(first == second, "render_drift", target.target_id)
    try:
        documents = [
            value
            for value in yaml.load_all(
                first,
                Loader=_unique_loader(yaml),
            )
            if value is not None
        ]
    except Exception as error:
        raise GitOpsValidationError("helm_failed") from error
    _compare_expected(target, source_root, documents, yaml)
    count = (
        2
        + len(target.values_files)
        + len(target.vendored_dependencies)
    )
    return documents, count


def _kustomization_path(root: Path) -> Path:
    candidates = [
        root / name
        for name in (
            "kustomization.yaml",
            "kustomization.yml",
            "Kustomization",
        )
        if (root / name).exists()
    ]
    _require(
        len(candidates) == 1,
        "kustomize_invalid",
        root.name,
    )
    _require(
        candidates[0].is_file()
        and not candidates[0].is_symlink(),
        "kustomize_invalid",
    )
    return candidates[0]


def _validate_kustomization(
    root: Path,
    yaml: Any,
    visited: set[Path],
) -> None:
    root = root.resolve()
    _require(root not in visited, "kustomize_invalid", "cycle")
    visited.add(root)
    path = _kustomization_path(root)
    documents = _yaml_documents(path, yaml)
    _require(
        len(documents) == 1
        and isinstance(documents[0], dict),
        "kustomize_invalid",
    )
    data = documents[0]
    forbidden = {
        "helmCharts",
        "generators",
        "transformers",
        "plugins",
        "exec",
    }
    _require(
        not (set(data) & forbidden),
        "kustomize_invalid",
        "plugin or Helm escape",
    )
    references: list[str] = []
    for field in (
        "resources",
        "bases",
        "components",
        "patchesStrategicMerge",
    ):
        value = data.get(field, [])
        _require(
            isinstance(value, list),
            "kustomize_invalid",
            field,
        )
        string_values = [
            item
            for item in value
            if isinstance(item, str)
        ]
        _require(
            len(string_values) == len(value),
            "kustomize_invalid",
            field,
        )
        references.extend(string_values)
    patches = data.get("patches", [])
    _require(isinstance(patches, list), "kustomize_invalid")
    for patch in patches:
        if (
            isinstance(patch, dict)
            and isinstance(patch.get("path"), str)
        ):
            references.append(patch["path"])
        elif isinstance(patch, str):
            references.append(patch)
        else:
            _fail("kustomize_invalid", "patch")
    for reference in references:
        _require(
            "://" not in reference
            and not reference.startswith("git::")
            and not PurePosixPath(reference).is_absolute()
            and ".." not in PurePosixPath(reference).parts
            and "\\" not in reference,
            "kustomize_invalid",
            reference,
        )
        candidate = root.joinpath(
            *PurePosixPath(reference).parts
        )
        current = root
        for part in PurePosixPath(reference).parts:
            current /= part
            _require(
                not current.is_symlink(),
                "path_symlink_rejected",
                reference,
            )
        resolved = candidate.resolve(strict=False)
        _require(
            root == resolved or root in resolved.parents,
            "path_escape_rejected",
            reference,
        )
        _require(
            resolved.exists(),
            "kustomize_invalid",
            reference,
        )
        if resolved.is_dir():
            _validate_kustomization(resolved, yaml, visited)


def _kustomize_target(
    target: GitOpsTarget,
    source_root: Path,
    state_root: Path,
    tools: GitOpsTools,
) -> tuple[list[Any], int]:
    yaml = tools.yaml
    binary = tools.binaries.get("kustomize")
    _require(
        binary is not None,
        "tool_identity_mismatch",
        "kustomize",
    )
    root = bounded_path(
        source_root,
        target.root,
        must_exist=True,
        kind="directory",
    )
    _validate_kustomization(root, yaml, set())
    environment = _tool_environment(state_root)
    command = (
        str(binary),
        "build",
        str(root),
        "--load-restrictor=LoadRestrictionsRootOnly",
    )
    first = _run(
        command,
        cwd=source_root,
        environment=environment,
        timeout=120,
        max_output=4_000_000,
        code="kustomize_failed",
    )
    second = _run(
        command,
        cwd=source_root,
        environment=environment,
        timeout=120,
        max_output=4_000_000,
        code="kustomize_failed",
    )
    _require(first == second, "render_drift", target.target_id)
    try:
        documents = [
            value
            for value in yaml.load_all(
                first,
                Loader=_unique_loader(yaml),
            )
            if value is not None
        ]
    except Exception as error:
        raise GitOpsValidationError("kustomize_failed") from error
    _compare_expected(target, source_root, documents, yaml)
    files = _glob_files(root, target.include)
    return documents, len(files)


def _compare_expected(
    target: GitOpsTarget,
    source_root: Path,
    documents: Sequence[Any],
    yaml: Any,
) -> None:
    if target.expected_render_path is None:
        return
    expected_path = bounded_path(
        source_root,
        target.expected_render_path,
        must_exist=True,
        kind="file",
    )
    expected = _yaml_documents(expected_path, yaml)
    _require(
        _canonical_documents(expected)
        == _canonical_documents(documents),
        "render_drift",
        target.target_id,
    )


def _policy(
    plan: GitOpsPlan,
    source_root: Path,
    state_root: Path,
) -> str:
    policy = plan.policy_script
    if policy is None:
        return "skipped"
    _require(
        plan.request.validation_profile.value
        in policy.allowed_profiles,
        "policy_profile_rejected",
    )
    script = bounded_path(
        source_root,
        policy.path,
        must_exist=True,
        kind="file",
    )
    _require(
        file_sha256(script) == policy.sha256,
        "policy_profile_rejected",
    )
    environment = _tool_environment(state_root)
    environment.update(
        {
            "GITOPS_VALIDATION_PROFILE": (
                plan.request.validation_profile.value
            ),
            "GITOPS_ADMITTED_SHA": plan.request.admitted_sha,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    _require(
        not (set(environment) & set(_FORBIDDEN_POLICY_ENV)),
        "policy_failed",
    )
    argv = (
        sys.executable
        if policy.argv[0] == "python3"
        else policy.argv[0],
        str(script),
    )
    _run(
        argv,
        cwd=source_root,
        environment=environment,
        timeout=policy.timeout_seconds,
        max_output=policy.max_output_bytes,
        code="policy_failed",
    )
    return "success"
