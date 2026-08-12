"""Exact source snapshots, YAML, schema, and SOPS validation."""
from __future__ import annotations

import hashlib
import json
import re
import stat
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from .gitops_contract import bounded_path
from .gitops_runtime import _require
from .gitops_types import (
    GitOpsPlan,
    GitOpsTarget,
    GitOpsValidationError,
    ObjectIdentity,
)


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    paths = sorted(
        root.rglob("*"),
        key=lambda item: item.relative_to(root).as_posix(),
    )
    for path in paths:
        relative = path.relative_to(root).as_posix()
        parts = PurePosixPath(relative).parts
        if parts and parts[0] == ".git":
            continue
        info = path.lstat()
        _require(
            not stat.S_ISLNK(info.st_mode),
            "path_symlink_rejected",
            relative,
        )
        if path.is_dir():
            continue
        _require(path.is_file(), "invalid_path", relative)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _source_snapshot(source_root: Path, admitted_sha: str) -> str:
    git = source_root / ".git"
    _require(git.exists(), "source_mismatch", "git metadata missing")
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=source_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=source_root,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise GitOpsValidationError("source_mismatch") from error
    _require(head == admitted_sha, "source_mismatch")
    _require(status == "", "source_dirty")
    return _tree_digest(source_root)


def _changed_paths(
    source_root: Path,
    base_sha: str,
    admitted_sha: str,
) -> tuple[str, ...]:
    try:
        output = subprocess.check_output(
            [
                "git",
                "diff",
                "--name-only",
                "--diff-filter=ACMR",
                f"{base_sha}...{admitted_sha}",
            ],
            cwd=source_root,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise GitOpsValidationError("change_base_invalid") from error
    return tuple(
        sorted(
            {
                line.strip()
                for line in output.splitlines()
                if line.strip()
            }
        )
    )


def _target_matches(
    target: GitOpsTarget,
    changed: Sequence[str],
) -> bool:
    prefix = target.root.rstrip("/") + "/"
    return any(
        path == target.root or path.startswith(prefix)
        for path in changed
    )


def _selected_targets(
    plan: GitOpsPlan,
    source_root: Path,
) -> tuple[GitOpsTarget, ...]:
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


def _unique_loader(yaml: Any) -> Any:
    class UniqueLoader(yaml.SafeLoader):
        pass

    def construct_mapping(
        loader: Any,
        node: Any,
        deep: bool = False,
    ) -> dict[Any, Any]:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in mapping:
                raise GitOpsValidationError(
                    "yaml_invalid",
                    "duplicate key",
                )
            mapping[key] = loader.construct_object(
                value_node,
                deep=deep,
            )
        return mapping

    UniqueLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_mapping,
    )
    return UniqueLoader


def _yaml_documents(path: Path, yaml: Any) -> list[Any]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise GitOpsValidationError("yaml_invalid") from error
    _require(len(raw) <= 4_000_000, "yaml_invalid", path.name)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GitOpsValidationError("yaml_invalid") from error
    _require("\t" not in text, "yaml_style_failed", path.name)
    _require(
        not text or text.endswith("\n"),
        "yaml_style_failed",
        path.name,
    )
    for line in text.splitlines():
        _require(
            line.rstrip() == line,
            "yaml_style_failed",
            path.name,
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
            path.name,
        ) from error
    return [
        document
        for document in documents
        if document is not None
    ]


def _schema_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _validate_schema(
    value: Any,
    schema: Mapping[str, Any],
    location: str = "$",
) -> None:
    allowed = {
        "type",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "enum",
        "pattern",
        "minLength",
        "minimum",
    }
    _require(set(schema) <= allowed, "schema_invalid", location)
    expected = schema.get("type")
    if expected is not None:
        if isinstance(expected, list):
            _require(
                _schema_type(value) in expected,
                "schema_invalid",
                location,
            )
        else:
            _require(
                _schema_type(value) == expected,
                "schema_invalid",
                location,
            )
    if "enum" in schema:
        _require(value in schema["enum"], "schema_invalid", location)
    if isinstance(value, str):
        if "minLength" in schema:
            _require(
                len(value) >= int(schema["minLength"]),
                "schema_invalid",
                location,
            )
        if "pattern" in schema:
            _require(
                re.search(str(schema["pattern"]), value) is not None,
                "schema_invalid",
                location,
            )
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and "minimum" in schema
    ):
        _require(
            value >= schema["minimum"],
            "schema_invalid",
            location,
        )
    if isinstance(value, list) and "items" in schema:
        item_schema = schema["items"]
        _require(
            isinstance(item_schema, dict),
            "schema_invalid",
            location,
        )
        for index, item in enumerate(value):
            _validate_schema(
                item,
                item_schema,
                f"{location}[{index}]",
            )
    if isinstance(value, dict):
        required = schema.get("required", [])
        _require(
            isinstance(required, list),
            "schema_invalid",
            location,
        )
        for key in required:
            _require(
                key in value,
                "schema_invalid",
                f"{location}.{key}",
            )
        properties = schema.get("properties", {})
        _require(
            isinstance(properties, dict),
            "schema_invalid",
            location,
        )
        for key, child in properties.items():
            _require(
                isinstance(child, dict),
                "schema_invalid",
                location,
            )
            if key in value:
                _validate_schema(
                    value[key],
                    child,
                    f"{location}.{key}",
                )
        if schema.get("additionalProperties") is False:
            _require(
                set(value) <= set(properties),
                "schema_invalid",
                location,
            )


def _load_schema(
    source_root: Path,
    relative: str,
    yaml: Any,
) -> Mapping[str, Any]:
    del yaml
    path = bounded_path(
        source_root,
        relative,
        must_exist=True,
        kind="file",
    )
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GitOpsValidationError("schema_invalid") from error
    _require(isinstance(schema, dict), "schema_invalid")
    return schema


def _object_identity(value: Any) -> ObjectIdentity | None:
    if not isinstance(value, dict):
        return None
    api_version = value.get("apiVersion")
    kind = value.get("kind")
    metadata = value.get("metadata")
    if (
        not isinstance(api_version, str)
        or not isinstance(kind, str)
        or not isinstance(metadata, dict)
    ):
        return None
    name = metadata.get("name")
    namespace = metadata.get("namespace", "")
    _require(
        isinstance(name, str) and bool(name),
        "yaml_invalid",
        "metadata.name",
    )
    _require(
        isinstance(namespace, str),
        "yaml_invalid",
        "metadata.namespace",
    )
    return ObjectIdentity(api_version, kind, namespace, name)


def _canonical_documents(documents: Iterable[Any]) -> bytes:
    normalized = sorted(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        for value in documents
    )
    return (
        "\n".join(normalized)
        + ("\n" if normalized else "")
    ).encode("utf-8")


def _validate_sops(document: Any, relative: str) -> None:
    _require(
        isinstance(document, dict),
        "sops_plaintext_rejected",
        relative,
    )
    sops = document.get("sops")
    _require(
        isinstance(sops, dict),
        "sops_plaintext_rejected",
        relative,
    )
    _require(
        isinstance(sops.get("mac"), str)
        and str(sops["mac"]).startswith("ENC["),
        "sops_plaintext_rejected",
        relative,
    )
    _require(
        isinstance(sops.get("version"), str),
        "sops_plaintext_rejected",
        relative,
    )
    for section in ("data", "stringData"):
        values = document.get(section, {})
        _require(
            isinstance(values, dict),
            "sops_plaintext_rejected",
            relative,
        )
        for value in values.values():
            _require(
                isinstance(value, str)
                and value.startswith("ENC[AES256_GCM,"),
                "sops_plaintext_rejected",
                relative,
            )
    text = json.dumps(document, sort_keys=True)
    _require(
        "PRIVATE KEY" not in text
        and "sops decrypt" not in text.lower(),
        "sops_plaintext_rejected",
        relative,
    )


def _glob_files(
    root: Path,
    patterns: Sequence[str],
) -> tuple[Path, ...]:
    found: set[Path] = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_dir():
                continue
            _require(
                path.is_file() and not path.is_symlink(),
                "path_symlink_rejected",
                path.name,
            )
            _require(
                root.resolve() in path.resolve().parents,
                "path_escape_rejected",
            )
            found.add(path)
    return tuple(
        sorted(
            found,
            key=lambda item: item.relative_to(root).as_posix(),
        )
    )
