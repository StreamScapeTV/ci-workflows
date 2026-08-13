"""Strict YAML and JSON-Schema manifest parsing for issue dependencies."""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from .issue_dependency_types import (
    DependencySyncError,
    IssueRef,
    ManagedIssue,
    ManifestValidationError,
    RepositoryManifest,
)

def _path_text(path: tuple[object, ...]) -> str:
    if not path:
        return "$"
    rendered = "$"
    for part in path:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += f".{part}"
    return rendered


def _schema_failure(path: tuple[object, ...], message: str) -> None:
    raise ManifestValidationError(f"{_path_text(path)}: {message}")


def _resolve_local_ref(root_schema: Mapping[str, Any], ref: str) -> Mapping[str, Any]:
    if not ref.startswith("#/"):
        raise ManifestValidationError(f"unsupported JSON Schema reference: {ref!r}")
    current: Any = root_schema
    for encoded in ref[2:].split("/"):
        key = encoded.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, Mapping) or key not in current:
            raise ManifestValidationError(f"unresolved JSON Schema reference: {ref!r}")
        current = current[key]
    if not isinstance(current, Mapping):
        raise ManifestValidationError(f"JSON Schema reference is not an object: {ref!r}")
    return current


def _is_schema_type(instance: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(instance, Mapping)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    raise ManifestValidationError(f"unsupported JSON Schema type: {expected!r}")


def _stable_unique_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _validate_json_schema_node(
    instance: Any,
    schema: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    path: tuple[object, ...],
) -> None:
    if "$ref" in schema:
        target = _resolve_local_ref(root_schema, str(schema["$ref"]))
        _validate_json_schema_node(instance, target, root_schema, path)
        return

    one_of = schema.get("oneOf")
    if one_of is not None:
        if not isinstance(one_of, list) or not one_of:
            raise ManifestValidationError("invalid oneOf in trusted JSON Schema")
        matches = 0
        for candidate in one_of:
            if not isinstance(candidate, Mapping):
                raise ManifestValidationError("invalid oneOf branch in trusted JSON Schema")
            try:
                _validate_json_schema_node(instance, candidate, root_schema, path)
            except ManifestValidationError:
                continue
            matches += 1
        if matches != 1:
            _schema_failure(path, f"must match exactly one schema branch (matched {matches})")
        return

    if "const" in schema and instance != schema["const"]:
        _schema_failure(path, f"must equal {schema['const']!r}")

    if "enum" in schema:
        values = schema["enum"]
        if not isinstance(values, list) or not values:
            raise ManifestValidationError("invalid enum in trusted JSON Schema")
        if instance not in values:
            _schema_failure(path, "must equal one of the enumerated values")

    expected_type = schema.get("type")
    if expected_type is not None:
        if not isinstance(expected_type, str):
            raise ManifestValidationError("unsupported non-string JSON Schema type")
        if not _is_schema_type(instance, expected_type):
            _schema_failure(path, f"must be of type {expected_type}")

    if isinstance(instance, str):
        pattern = schema.get("pattern")
        if pattern is not None and re.search(str(pattern), instance) is None:
            _schema_failure(path, f"does not match required pattern {pattern!r}")
        min_length = schema.get("minLength")
        if min_length is not None and len(instance) < int(min_length):
            _schema_failure(path, f"must contain at least {min_length} characters")
        max_length = schema.get("maxLength")
        if max_length is not None and len(instance) > int(max_length):
            _schema_failure(path, f"must contain at most {max_length} characters")

    if isinstance(instance, int) and not isinstance(instance, bool):
        minimum = schema.get("minimum")
        if minimum is not None and instance < int(minimum):
            _schema_failure(path, f"must be >= {minimum}")
        maximum = schema.get("maximum")
        if maximum is not None and instance > int(maximum):
            _schema_failure(path, f"must be <= {maximum}")

    if isinstance(instance, list):
        min_items = schema.get("minItems")
        if min_items is not None and len(instance) < int(min_items):
            _schema_failure(path, f"must contain at least {min_items} item(s)")
        max_items = schema.get("maxItems")
        if max_items is not None and len(instance) > int(max_items):
            _schema_failure(path, f"must contain at most {max_items} item(s)")
        if schema.get("uniqueItems"):
            seen: set[str] = set()
            for item in instance:
                key = _stable_unique_key(item)
                if key in seen:
                    _schema_failure(path, "contains duplicate array items")
                seen.add(key)
        item_schema = schema.get("items")
        if item_schema is not None:
            if not isinstance(item_schema, Mapping):
                raise ManifestValidationError("invalid items in trusted JSON Schema")
            for index, item in enumerate(instance):
                _validate_json_schema_node(
                    item, item_schema, root_schema, (*path, index)
                )

    if isinstance(instance, Mapping):
        for key in instance:
            if not isinstance(key, str):
                _schema_failure(path, "object keys must be strings after normalization")

        min_properties = schema.get("minProperties")
        if min_properties is not None and len(instance) < int(min_properties):
            _schema_failure(
                path, f"must contain at least {min_properties} propert(ies)"
            )
        max_properties = schema.get("maxProperties")
        if max_properties is not None and len(instance) > int(max_properties):
            _schema_failure(
                path, f"must contain at most {max_properties} propert(ies)"
            )

        required = schema.get("required", [])
        if not isinstance(required, list):
            raise ManifestValidationError("invalid required list in trusted JSON Schema")
        for key in required:
            if key not in instance:
                _schema_failure((*path, str(key)), "is required")

        properties = schema.get("properties", {})
        pattern_properties = schema.get("patternProperties", {})
        if not isinstance(properties, Mapping) or not isinstance(
            pattern_properties, Mapping
        ):
            raise ManifestValidationError(
                "invalid object property map in trusted JSON Schema"
            )

        matched_keys: set[str] = set()
        for key, subschema in properties.items():
            if key in instance:
                if not isinstance(subschema, Mapping):
                    raise ManifestValidationError(
                        "invalid property schema in trusted JSON Schema"
                    )
                _validate_json_schema_node(
                    instance[key], subschema, root_schema, (*path, key)
                )
                matched_keys.add(str(key))

        for pattern, subschema in pattern_properties.items():
            if not isinstance(subschema, Mapping):
                raise ManifestValidationError(
                    "invalid pattern property in trusted JSON Schema"
                )
            compiled = re.compile(str(pattern))
            for key, value in instance.items():
                if compiled.search(key):
                    _validate_json_schema_node(
                        value, subschema, root_schema, (*path, key)
                    )
                    matched_keys.add(key)

        if schema.get("additionalProperties") is False:
            extras = sorted(set(instance) - matched_keys)
            if extras:
                _schema_failure(path, f"contains unsupported key(s): {', '.join(extras)}")


def validate_json_schema(
    instance: Any, schema: Mapping[str, Any]
) -> None:
    """Validate the manifest with the checked-in deterministic JSON Schema subset."""
    _validate_json_schema_node(instance, schema, schema, ())


def _load_yaml_document(text: str) -> Any:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - workflow bootstraps locked PyYAML
        raise DependencySyncError("locked PyYAML runtime is unavailable") from exc

    class StrictSafeLoader(yaml.SafeLoader):
        pass

    def construct_mapping(loader: Any, node: Any, deep: bool = False) -> dict[Any, Any]:
        loader.flatten_mapping(node)
        result: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                duplicate = key in result
            except TypeError as exc:
                raise ManifestValidationError("YAML mapping key is not scalar/hashable") from exc
            if duplicate:
                raise ManifestValidationError(f"duplicate YAML mapping key: {key!r}")
            result[key] = loader.construct_object(value_node, deep=deep)
        return result

    StrictSafeLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping
    )
    try:
        documents = list(yaml.load_all(text, Loader=StrictSafeLoader))
    except yaml.YAMLError as exc:
        raise ManifestValidationError("ISSUE_DEPENDENCIES.yml is not valid YAML") from exc

    if len(documents) != 1:
        raise ManifestValidationError(
            "ISSUE_DEPENDENCIES.yml must contain exactly one YAML document"
        )
    return documents[0]


def _normalize_manifest_document(document: Any) -> dict[str, Any]:
    if not isinstance(document, Mapping):
        raise ManifestValidationError("manifest root must be an object")
    normalized = dict(document)
    issues = normalized.get("issues")
    if not isinstance(issues, Mapping):
        # Let JSON Schema render the canonical error.
        return normalized  # type: ignore[return-value]

    normalized_issues: dict[str, Any] = {}
    for raw_key, value in issues.items():
        if isinstance(raw_key, bool):
            raise ManifestValidationError("issue keys must be positive decimal numbers")
        if isinstance(raw_key, int):
            if raw_key < 1:
                raise ManifestValidationError("issue keys must be positive decimal numbers")
            key = str(raw_key)
        elif isinstance(raw_key, str):
            key = raw_key
        else:
            raise ManifestValidationError("issue keys must be positive decimal numbers")
        if key in normalized_issues:
            raise ManifestValidationError(
                f"duplicate normalized issue key in manifest: {key}"
            )
        normalized_issues[key] = value
    normalized["issues"] = normalized_issues
    return normalized  # type: ignore[return-value]


def load_manifest(
    text: str,
    schema: Mapping[str, Any],
    *,
    expected_repository: str,
    integration_branch: str,
) -> RepositoryManifest:
    """Parse, schema-validate and semantically normalize one repository manifest."""
    document = _normalize_manifest_document(_load_yaml_document(text))
    validate_json_schema(document, schema)

    repository = document["repository"]
    if repository != expected_repository:
        raise ManifestValidationError(
            f"manifest repository {repository!r} does not match {expected_repository!r}"
        )

    managed: list[ManagedIssue] = []
    for number_text, payload in sorted(
        document["issues"].items(), key=lambda item: int(item[0])
    ):
        dependent = IssueRef(repository=repository, number=int(number_text))
        blockers: list[IssueRef] = []
        seen: set[IssueRef] = set()
        for raw in payload["blocked_by"]:
            if isinstance(raw, int) and not isinstance(raw, bool):
                blocker = IssueRef(repository=repository, number=raw)
            elif isinstance(raw, str):
                blocker = IssueRef.from_url(raw)
            else:  # schema already rejected this
                raise ManifestValidationError(
                    f"{dependent.url}: unsupported blocker value {raw!r}"
                )
            if blocker == dependent:
                raise ManifestValidationError(
                    f"{dependent.url}: self-dependency is forbidden"
                )
            if blocker in seen:
                raise ManifestValidationError(
                    f"{dependent.url}: duplicate blocker edge {blocker.url}"
                )
            seen.add(blocker)
            blockers.append(blocker)
        managed.append(
            ManagedIssue(dependent=dependent, blockers=tuple(sorted(blockers)))
        )
    return RepositoryManifest(
        repository=repository,
        integration_branch=integration_branch,
        issues=tuple(managed),
    )
