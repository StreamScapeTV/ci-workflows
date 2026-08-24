"""Public workflow API registry validation, rendering, and compatibility checks."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

API_NAME = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
SEMVER_TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
WORKFLOW_FILE = re.compile(r"^\.github/workflows/reusable-[A-Za-z0-9._-]+\.(?:yml|yaml)$")
PROFILE = re.compile(r"^[a-z][a-z0-9:-]*$")
PERMISSION_LEVELS = {"read", "write", "none"}
STATUSES = {"planned", "migration-pending", "implemented", "deprecated-bootstrap-exception"}
PRIVILEGED_TRUST = {
    "physical-device-validation",
    "trusted-publication",
    "flux-authorized",
    "trusted-maintenance",
}
FORBIDDEN_RUNNER_FRAGMENTS = {
    "self-hosted",
    "homelab-",
    "buildah-",
    "docker-linux",
    "macos-",
    "flux-control-plane",
}
MAX_PUBLIC_DEPTH = 2


class ContractError(RuntimeError):
    """Raised when a public API contract fails closed."""


@dataclass(frozen=True)
class ContractData:
    root: Path
    index: Mapping[str, Any]
    types: Mapping[str, Any]
    permissions: Mapping[str, Any]
    workflows: tuple[Mapping[str, Any], ...]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot read {path}: {error}") from error


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise ContractError(f"cannot read {path}: {error}") from error


def nonempty(value: Any, field: str) -> str:
    require(isinstance(value, str) and bool(value.strip()), f"{field} must be non-empty")
    return value


def unique_strings(value: Any, field: str, *, allow_empty: bool = True) -> list[str]:
    require(isinstance(value, list), f"{field} must be an array")
    require(all(isinstance(item, str) and item for item in value), f"{field} must contain strings")
    require(len(value) == len(set(value)), f"{field} contains duplicates")
    require(allow_empty or bool(value), f"{field} must not be empty")
    return list(value)


def permission_map(value: Any, field: str) -> dict[str, str]:
    require(isinstance(value, dict), f"{field} must be an object")
    result: dict[str, str] = {}
    for key, level in value.items():
        require(isinstance(key, str) and key, f"{field} contains an invalid permission")
        require(level in PERMISSION_LEVELS, f"{field}.{key} has an invalid level")
        result[key] = level
    return dict(sorted(result.items()))


def _load_fragments(root: Path, index: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    fragment_paths = unique_strings(index.get("fragment_contracts"), "fragment_contracts", allow_empty=False)
    workflows: list[Mapping[str, Any]] = []
    for relative in fragment_paths:
        require(relative.startswith("contracts/public-workflows/") and relative.endswith(".json"), f"invalid fragment path: {relative}")
        fragment = read_json(root / relative)
        require(isinstance(fragment, dict), f"{relative} must be an object")
        require(fragment.get("schema_version") == 1, f"unsupported fragment schema: {relative}")
        require(fragment.get("organization") == "StreamScapeTV", f"fragment organization mismatch: {relative}")
        nonempty(fragment.get("group"), f"{relative}.group")
        rows = fragment.get("workflows")
        require(isinstance(rows, list) and rows, f"{relative}.workflows must be non-empty")
        for row in rows:
            require(isinstance(row, dict), f"{relative} contains a non-object workflow")
            copy = dict(row)
            copy["fragment"] = relative
            workflows.append(copy)
    return tuple(sorted(workflows, key=lambda row: str(row.get("api_name", ""))))


def load_contract(root: Path) -> ContractData:
    root = root.resolve()
    index = read_json(root / "contracts/public-workflows.json")
    require(isinstance(index, dict), "public workflow index must be an object")
    require(index.get("schema_version") == 1, "unsupported public workflow index schema")
    require(index.get("organization") == "StreamScapeTV", "public workflow organization mismatch")
    require(SEMVER.fullmatch(nonempty(index.get("contract_version"), "contract_version")) is not None, "invalid contract_version")
    types_path = nonempty(index.get("types_contract"), "types_contract")
    permissions_path = nonempty(index.get("permission_contract"), "permission_contract")
    require(types_path == "contracts/public-workflow-types.json", "unexpected types contract path")
    require(permissions_path == "contracts/permission-profiles.json", "unexpected permission profile contract path")
    types = read_json(root / types_path)
    permissions = read_json(root / permissions_path)
    require(isinstance(types, dict) and types.get("schema_version") == 1 and types.get("organization") == "StreamScapeTV", "public workflow types contract is invalid")
    require(isinstance(permissions, dict) and permissions.get("schema_version") == 1 and permissions.get("organization") == "StreamScapeTV", "permission profile contract is invalid")
    workflows = _load_fragments(root, index)
    require(index.get("workflow_count") == len(workflows), "workflow_count disagrees with fragments")
    index_rows = index.get("workflows")
    require(isinstance(index_rows, list), "public workflow index rows must be an array")
    require([row.get("api_name") for row in index_rows] == sorted(row.get("api_name") for row in index_rows), "public workflow index must be sorted")
    expected_index = [
        {
            "api_name": row.get("api_name"),
            "api_version": row.get("api_version"),
            "file": row.get("file"),
            "fragment": row.get("fragment"),
            "status": row.get("status"),
            "trust_class": row.get("trust_class"),
        }
        for row in workflows
    ]
    require(index_rows == expected_index, "public workflow index disagrees with fragments")
    return ContractData(root, index, types, permissions, workflows)


def _validate_policy(types: Mapping[str, Any]) -> None:
    reference = types.get("reference_policy")
    require(isinstance(reference, dict), "reference_policy must be an object")
    require(reference.get("bootstrap_mutable_reference") == "main", "bootstrap mutable reference must be main")
    allowed = unique_strings(reference.get("bootstrap_mutable_allowed_trust_classes"), "bootstrap_mutable_allowed_trust_classes", allow_empty=False)
    require(set(allowed) == {"source-admission", "read-only-validation"}, "mutable bootstrap trust classes changed")
    require(reference.get("privileged_mutable_references_forbidden") is True, "privileged mutable references must fail closed")
    require(reference.get("consumer_updates") == "reviewable-pull-request", "consumer updates must be reviewable")
    require(reference.get("rollback_reference_required") is True, "rollback reference must be required")
    require(reference.get("delete_referenced_release_forbidden") is True, "referenced releases must not be deleted")
    compatibility = types.get("compatibility_policy")
    require(isinstance(compatibility, dict), "compatibility_policy must be an object")
    for name in ("compatible", "conditional", "breaking"):
        unique_strings(compatibility.get(name), f"compatibility_policy.{name}", allow_empty=False)
    required_ack = unique_strings(compatibility.get("acknowledgement_required_fields"), "compatibility_policy.acknowledgement_required_fields", allow_empty=False)
    require(set(required_ack) == {"id", "api_name", "kind", "reason", "migration_issue", "effective_version"}, "breaking acknowledgement fields changed")
    defaults = types.get("defaults")
    require(isinstance(defaults, dict), "public API defaults are missing")
    require(defaults.get("artifact_policy") == "zero-default-named-exception-only", "artifact policy changed")
    require(defaults.get("cleanup_policy") == "always-residue-checked", "cleanup policy changed")


def permission_profiles(data: ContractData) -> dict[str, Mapping[str, Any]]:
    token_model = data.permissions.get("token_model")
    require(token_model == {
        "called_workflow_cannot_elevate": True,
        "caller_declares_minimum": True,
        "secrets_inherit_forbidden": True,
        "default_unspecified_permission": "none",
    }, "token model changed")
    trust_classes = data.types.get("trust_classes")
    secret_catalog = data.types.get("secret_catalog")
    require(isinstance(trust_classes, dict), "trust class catalog is missing")
    require(isinstance(secret_catalog, dict), "secret catalog is missing")
    rows = data.permissions.get("profiles")
    require(isinstance(rows, list) and rows, "permission profiles must be non-empty")
    profiles: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        require(isinstance(row, dict), "permission profile must be an object")
        identifier = nonempty(row.get("id"), "permission profile id")
        require(PROFILE.fullmatch(identifier) is not None, f"invalid permission profile: {identifier}")
        require(identifier not in profiles, f"duplicate permission profile: {identifier}")
        trust = nonempty(row.get("trust_class"), f"{identifier}.trust_class")
        require(trust in trust_classes, f"{identifier} uses unknown trust class")
        caller = permission_map(row.get("caller_permissions"), f"{identifier}.caller_permissions")
        workflow = permission_map(row.get("workflow_permissions"), f"{identifier}.workflow_permissions")
        require(caller == workflow, f"{identifier} workflow permissions exceed or differ from caller permissions")
        forbidden = unique_strings(row.get("forbidden_permissions"), f"{identifier}.forbidden_permissions")
        for value in forbidden:
            require(re.fullmatch(r"[a-z-]+:(?:read|write|none)", value) is not None, f"{identifier} has invalid forbidden permission")
            key, level = value.split(":", 1)
            require(caller.get(key) != level, f"{identifier} simultaneously requires and forbids {value}")
        secrets = unique_strings(row.get("named_secrets_allowed"), f"{identifier}.named_secrets_allowed")
        require(set(secrets) <= set(secret_catalog), f"{identifier} uses an unknown secret")
        nonempty(row.get("notes"), f"{identifier}.notes")
        profiles[identifier] = row
    return profiles


def _validate_input(row: Mapping[str, Any], catalog: Mapping[str, Any], label: str) -> tuple[str, bool]:
    name = nonempty(row.get("name"), f"{label}.name")
    require(name in catalog, f"{label} uses unknown input {name}")
    required = row.get("required")
    require(isinstance(required, bool), f"{label}.{name}.required must be boolean")
    if "default" in row:
        require(not required, f"{label}.{name} cannot be required and defaulted")
    return name, required


def validate_workflows(data: ContractData, profiles: Mapping[str, Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    trust_classes = data.types.get("trust_classes")
    input_catalog = data.types.get("input_catalog")
    secret_catalog = data.types.get("secret_catalog")
    output_catalog = data.types.get("output_catalog")
    defaults = data.types.get("defaults")
    require(isinstance(trust_classes, dict), "trust class catalog is missing")
    require(isinstance(input_catalog, dict), "input catalog is missing")
    require(isinstance(secret_catalog, dict), "secret catalog is missing")
    require(isinstance(output_catalog, dict), "public API output catalog is missing")
    require(isinstance(defaults, dict), "public API defaults are missing")
    forbidden_inputs = set(unique_strings(defaults.get("forbidden_caller_fields"), "forbidden_caller_fields"))
    by_api: dict[str, Mapping[str, Any]] = {}
    files: set[str] = set()
    checks: set[str] = set()
    represented_trust: set[str] = set()
    for row in data.workflows:
        api = nonempty(row.get("api_name"), "workflow.api_name")
        require(API_NAME.fullmatch(api) is not None, f"invalid api name: {api}")
        require(api not in by_api, f"duplicate api name: {api}")
        require("supported_consumers" not in row and "supported_products" not in row, f"{api} contains application identity metadata")
        version = nonempty(row.get("api_version"), f"{api}.api_version")
        require(SEMVER.fullmatch(version) is not None, f"{api} has invalid version")
        file = nonempty(row.get("file"), f"{api}.file")
        require(WORKFLOW_FILE.fullmatch(file) is not None, f"{api} has invalid public workflow file")
        require(file not in files, f"duplicate public workflow file: {file}")
        files.add(file)
        status = nonempty(row.get("status"), f"{api}.status")
        require(status in STATUSES, f"{api} has invalid status {status}")
        trust = nonempty(row.get("trust_class"), f"{api}.trust_class")
        require(trust in trust_classes, f"{api} has unknown trust class")
        represented_trust.add(trust)
        profile_id = nonempty(row.get("permission_profile"), f"{api}.permission_profile")
        profile = profiles.get(profile_id)
        require(profile is not None, f"{api} uses unknown permission profile")
        require(profile.get("trust_class") == trust, f"{api} permission profile trust mismatch")
        semantic = nonempty(row.get("semantic_runner_profile"), f"{api}.semantic_runner_profile")
        require(PROFILE.fullmatch(semantic) is not None, f"{api} has invalid semantic runner profile")
        require(not any(part in semantic.casefold() for part in FORBIDDEN_RUNNER_FRAGMENTS), f"{api} exposes a concrete runner selector")
        events = unique_strings(row.get("permitted_events"), f"{api}.permitted_events", allow_empty=False)
        allowed_events = trust_classes[trust].get("allowed_events")
        require(isinstance(allowed_events, list) and set(events) <= set(allowed_events), f"{api} permits an event outside its trust class")
        input_rows = row.get("inputs")
        require(isinstance(input_rows, list), f"{api}.inputs must be an array")
        inputs: dict[str, bool] = {}
        for item in input_rows:
            require(isinstance(item, dict), f"{api}.inputs contains a non-object")
            name, required = _validate_input(item, input_catalog, f"{api}.inputs")
            require(name not in inputs, f"{api} has duplicate input {name}")
            require(name not in forbidden_inputs, f"{api} exposes forbidden caller field {name}")
            inputs[name] = required
        secrets = unique_strings(row.get("secrets"), f"{api}.secrets")
        require(set(secrets) <= set(secret_catalog), f"{api} uses unknown secrets")
        require(set(secrets) <= set(profile.get("named_secrets_allowed", ())), f"{api} secret exceeds its permission profile")
        outputs = unique_strings(row.get("outputs"), f"{api}.outputs", allow_empty=False)
        require(set(outputs) <= set(output_catalog), f"{api} uses unknown outputs")
        check = nonempty(row.get("stable_check_name"), f"{api}.stable_check_name")
        require(check not in checks, f"duplicate stable check name: {check}")
        checks.add(check)
        timeout = row.get("timeout_minutes")
        matrix = row.get("matrix_max_jobs")
        require(isinstance(timeout, int) and 1 <= timeout <= 240, f"{api} timeout is invalid")
        require(isinstance(matrix, int) and 1 <= matrix <= 16, f"{api} matrix maximum is invalid")
        artifact_policy = row.get("artifact_policy", "zero-default")
        require(artifact_policy in {"zero-default", "bounded-evidence"}, f"{api} artifact policy is invalid")
        if artifact_policy == "bounded-evidence":
            retention_max = row.get("artifact_retention_max_days")
            require(type(retention_max) is int and 1 <= retention_max <= 7, f"{api} artifact retention maximum is invalid")
            require("artifact_manifest_json" in outputs, f"{api} bounded artifact policy requires artifact_manifest_json output")
        else:
            require("artifact_retention_max_days" not in row, f"{api} zero-artifact policy may not declare artifact retention")
        depth = row.get("max_reusable_workflow_depth", 1)
        require(isinstance(depth, int) and 1 <= depth <= MAX_PUBLIC_DEPTH, f"{api} call depth is invalid")
        components = unique_strings(row.get("implementation_components"), f"{api}.implementation_components", allow_empty=False)
        if depth == 2:
            require(any(component.startswith("internal-") for component in components), f"{api} depth two requires one named internal leaf")
        hooks = unique_strings(row.get("repository_owned_hooks"), f"{api}.repository_owned_hooks")
        require(set(hooks) <= set(inputs), f"{api} has a repository-owned hook that is not an input")
        by_api[api] = row
    require(set(trust_classes) <= represented_trust, "not every trust class is represented")
    for api, row in by_api.items():
        deprecation = row.get("deprecation")
        if isinstance(deprecation, dict):
            replacement = nonempty(deprecation.get("replacement"), f"{api}.deprecation.replacement")
            require(replacement in by_api, f"{api} has an unknown deprecation replacement")
    return by_api


def immutable_reference(value: str) -> bool:
    return FULL_SHA.fullmatch(value) is not None or SEMVER_TAG.fullmatch(value) is not None


def validate_bootstrap_workflow(data: ContractData, workflows: Mapping[str, Mapping[str, Any]], profiles: Mapping[str, Mapping[str, Any]]) -> None:
    bootstrap = workflows.get("release.tag-image-chart-bootstrap")
    require(bootstrap is not None, "existing bootstrap workflow is not represented in the public API")
    require(bootstrap.get("status") == "deprecated-bootstrap-exception", "bootstrap workflow must remain explicitly deprecated")
    require(bootstrap.get("trust_class") == "trusted-publication", "bootstrap workflow trust class changed")
    profile = profiles.get(str(bootstrap.get("permission_profile")))
    require(profile is not None, "bootstrap workflow permission profile is missing")
    secrets = set(bootstrap.get("secrets", ()))
    require(secrets == {"registry_username", "registry_token"}, "bootstrap workflow secret interface changed")
    require(permission_map(profile.get("caller_permissions"), "bootstrap permissions") == {"actions": "read", "contents": "read"}, "bootstrap workflow permission interface changed")
    events = set(bootstrap.get("permitted_events", ()))
    require(events == {"tag-push", "workflow_call", "workflow_dispatch-existing-tag"}, "bootstrap workflow event compatibility changed")
    inputs = _input_map(bootstrap)
    require(inputs.get("release_mode", {}).get("default") == "tag-push", "bootstrap release_mode default changed")
    for optional in ("release_version", "release_source_sha"):
        require(inputs.get(optional, {}).get("required") is False, f"bootstrap {optional} must remain optional")
    workflow = read_text(data.root / str(bootstrap["file"]))
    require("workflow_call:" in workflow, "bootstrap workflow_call support is missing")
    require("actions: read" in workflow and "contents: read" in workflow, "bootstrap workflow permissions changed")
    require("secrets: inherit" not in workflow, "bootstrap workflow may not inherit secrets")


def validate_release_schema(root: Path) -> None:
    schema = read_json(root / "contracts/release-manifest.schema.json")
    require(isinstance(schema, dict), "release manifest schema must be an object")
    require(schema.get("type") == "object" and schema.get("additionalProperties") is False, "release manifest schema must fail closed")
    properties = schema.get("properties")
    required = schema.get("required")
    require(isinstance(properties, dict) and isinstance(required, list), "release manifest schema shape is invalid")
    require(properties.get("schema_version", {}).get("const") == 2, "release manifest schema version must be 2")
    require("products" not in required and "products" not in properties, "release manifest must not carry a central product catalog")
    shared = properties.get("shared_release")
    require(isinstance(shared, dict) and shared.get("additionalProperties") is False, "shared release contract must fail closed")


def validate_caller(case: Mapping[str, Any], data: ContractData, workflows: Mapping[str, Mapping[str, Any]], profiles: Mapping[str, Mapping[str, Any]]) -> str | None:
    api = case.get("api_name")
    if not isinstance(api, str) or api not in workflows:
        return "unknown-api"
    row = workflows[api]
    trust = case.get("trust_class")
    if trust != row.get("trust_class"):
        return "trust-class-mismatch"
    reference = case.get("reference")
    if not isinstance(reference, str):
        return "invalid-reference"
    reference_policy = data.types.get("reference_policy", {})
    allowed_mutable = set(reference_policy.get("bootstrap_mutable_allowed_trust_classes", ()))
    if reference == reference_policy.get("bootstrap_mutable_reference"):
        if trust not in allowed_mutable:
            return "invalid-reference"
    elif not immutable_reference(reference):
        return "invalid-reference"
    if case.get("event") not in row.get("permitted_events", ()):
        return "event-not-permitted"
    profile = profiles[str(row["permission_profile"])]
    try:
        supplied_permissions = permission_map(case.get("permissions"), "caller permissions")
    except ContractError:
        return "permission-mismatch"
    if supplied_permissions != permission_map(profile.get("caller_permissions"), "profile permissions"):
        return "permission-mismatch"
    supplied_secrets = case.get("secrets")
    if not isinstance(supplied_secrets, list) or not all(isinstance(value, str) for value in supplied_secrets):
        return "secret-mismatch"
    allowed_secrets = set(row.get("secrets", ()))
    if not set(supplied_secrets) <= allowed_secrets:
        return "secret-mismatch"
    if trust in PRIVILEGED_TRUST and set(supplied_secrets) != allowed_secrets:
        return "secret-mismatch"
    inputs = case.get("inputs")
    if not isinstance(inputs, dict):
        return "invalid-inputs"
    forbidden = set(data.types["defaults"]["forbidden_caller_fields"])
    if set(inputs) & forbidden:
        return "forbidden-caller-field"
    contract_inputs = {item["name"]: item for item in row["inputs"]}
    if set(inputs) - set(contract_inputs):
        return "unknown-input"
    if any(definition.get("required") is True and name not in inputs for name, definition in contract_inputs.items()):
        return "missing-required-input"
    return None


def validate_caller_fixtures(data: ContractData, workflows: Mapping[str, Mapping[str, Any]], profiles: Mapping[str, Mapping[str, Any]]) -> None:
    fixtures = read_json(data.root / "tests/fixtures/public-api/callers.json")
    require(isinstance(fixtures, dict) and fixtures.get("schema_version") == 1, "caller fixture schema is invalid")
    valid = fixtures.get("valid")
    invalid = fixtures.get("invalid")
    require(isinstance(valid, list) and valid, "valid caller fixtures are missing")
    require(isinstance(invalid, list) and invalid, "invalid caller fixtures are missing")
    represented_trust: set[str] = set()
    for case in valid:
        require(isinstance(case, dict), "valid caller fixture is not an object")
        error = validate_caller(case, data, workflows, profiles)
        require(error is None, f"valid caller fixture {case.get('id')} failed: {error}")
        represented_trust.add(str(case.get("trust_class")))
    require(set(data.types["trust_classes"]) <= represented_trust, "valid caller fixtures do not cover every trust class")
    for case in invalid:
        require(isinstance(case, dict), "invalid caller fixture is not an object")
        expected = nonempty(case.get("expected_error"), "invalid caller expected_error")
        actual = validate_caller(case, data, workflows, profiles)
        require(actual == expected, f"invalid caller fixture {case.get('id')} expected {expected}, got {actual}")


def _input_map(record: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = record.get("inputs", [])
    return {str(row.get("name")): row for row in rows if isinstance(row, dict)}


def valid_acknowledgement(value: Any, api_name: str) -> bool:
    if not isinstance(value, dict):
        return False
    required = {"id", "api_name", "kind", "reason", "migration_issue", "effective_version"}
    if set(value) != required or value.get("api_name") != api_name:
        return False
    return all(isinstance(value[field], str) and value[field] for field in required) and SEMVER.fullmatch(str(value["effective_version"])) is not None


def classify_change(baseline: Mapping[str, Any], current: Mapping[str, Any], acknowledgement: Any = None) -> str:
    api = str(current.get("api_name") or baseline.get("api_name") or "")
    breaking = False
    conditional = False
    for field in ("api_name", "file", "trust_class", "permission_profile", "semantic_runner_profile", "stable_check_name"):
        if baseline.get(field) != current.get(field):
            breaking = True
    if int(current.get("max_reusable_workflow_depth", 1)) > int(baseline.get("max_reusable_workflow_depth", 1)):
        breaking = True
    base_inputs = _input_map(baseline)
    current_inputs = _input_map(current)
    if set(base_inputs) - set(current_inputs):
        breaking = True
    for name in set(base_inputs) & set(current_inputs):
        before = base_inputs[name]
        after = current_inputs[name]
        if before.get("required") != after.get("required") or before.get("default") != after.get("default"):
            breaking = True
    for name in set(current_inputs) - set(base_inputs):
        if current_inputs[name].get("required") is True:
            breaking = True
    if set(baseline.get("outputs", ())) - set(current.get("outputs", ())):
        breaking = True
    if set(baseline.get("secrets", ())) != set(current.get("secrets", ())):
        breaking = True
    if baseline.get("timeout_minutes") != current.get("timeout_minutes") or baseline.get("matrix_max_jobs") != current.get("matrix_max_jobs"):
        conditional = True
    if breaking:
        return "breaking-acknowledged" if valid_acknowledgement(acknowledgement, api) else "breaking-unacknowledged"
    if conditional:
        return "conditional"
    return "compatible"


def validate_compatibility_fixtures(root: Path) -> None:
    fixtures = read_json(root / "tests/fixtures/public-api/compatibility.json")
    require(isinstance(fixtures, dict) and fixtures.get("schema_version") == 1, "compatibility fixture schema is invalid")
    cases = fixtures.get("cases")
    require(isinstance(cases, list) and cases, "compatibility fixtures are missing")
    represented: set[str] = set()
    for case in cases:
        require(isinstance(case, dict), "compatibility fixture is not an object")
        expected = nonempty(case.get("expected"), "compatibility expected")
        actual = classify_change(case.get("baseline", {}), case.get("current", {}), case.get("acknowledgement"))
        require(actual == expected, f"compatibility fixture {case.get('id')} expected {expected}, got {actual}")
        represented.add(actual)
    require(represented == {"compatible", "conditional", "breaking-unacknowledged", "breaking-acknowledged"}, "compatibility fixtures do not cover every decision")


def validate_docs(root: Path) -> None:
    architecture = read_text(root / "docs/architecture/public-api.md")
    upgrades = read_text(root / "docs/consumers/versioning-and-upgrades.md")
    for phrase in (
        "consumer caller → public reusable workflow → named function",
        "cannot elevate",
        "Agent State",
        "Flux",
        "zero routine Actions artifacts",
        "repository-owned",
        "application identity",
    ):
        require(phrase in architecture, f"public API architecture is missing: {phrase}")
    for phrase in (
        "reviewable pull requests",
        "immutable full commit SHA",
        "immutable SemVer tag",
        "known-good rollback",
        "@main",
        "breaking",
        "revocation",
        "technology contract",
    ):
        require(phrase in upgrades, f"versioning guide is missing: {phrase}")


def validate(root: Path) -> ContractData:
    data = load_contract(root)
    _validate_policy(data.types)
    profiles = permission_profiles(data)
    workflows = validate_workflows(data, profiles)
    validate_bootstrap_workflow(data, workflows, profiles)
    validate_release_schema(data.root)
    validate_caller_fixtures(data, workflows, profiles)
    validate_compatibility_fixtures(data.root)
    validate_docs(data.root)
    reference = data.root / "docs/workflows/public-api-reference.md"
    if reference.exists():
        require(read_text(reference) == render(data), "generated public API reference is stale")
    return data


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _input_label(item: Mapping[str, Any]) -> str:
    label = f"`{item['name']}`"
    if item.get("required") is True:
        label += " (required)"
    if "default" in item:
        label += f" (default `{item['default']}`)"
    return label


def render(data: ContractData) -> str:
    lines = [
        "# Public workflow API reference",
        "",
        f"Contract version: `{data.index['contract_version']}`",
        "",
        "Generated from `contracts/public-workflows.json` and its checked-in fragments. Application repository/product identity is intentionally not part of this compatibility contract.",
        "",
        "## Workflow APIs",
        "",
        "| API | File | Status | Trust | Check |",
        "|---|---|---|---|---|",
    ]
    for row in data.workflows:
        lines.append(
            "| "
            + " | ".join(
                (
                    f"`{_cell(row['api_name'])}` `{_cell(row['api_version'])}`",
                    f"`{_cell(row['file'])}`",
                    f"`{_cell(row['status'])}`",
                    f"`{_cell(row['trust_class'])}`",
                    _cell(row["stable_check_name"]),
                )
            )
            + " |"
        )
    lines += ["", "## API details", ""]
    for row in data.workflows:
        inputs = ", ".join(_input_label(item) for item in row["inputs"]) or "none"
        secrets = ", ".join(f"`{value}`" for value in row["secrets"]) or "none"
        outputs = ", ".join(f"`{value}`" for value in row["outputs"])
        hooks = ", ".join(f"`{value}`" for value in row["repository_owned_hooks"]) or "none"
        lines += [
            f"### `{row['api_name']}`",
            "",
            f"- Events: {', '.join(f'`{value}`' for value in row['permitted_events'])}",
            f"- Inputs: {inputs}",
            f"- Secrets: {secrets}",
            f"- Outputs: {outputs}",
            f"- Repository-owned hooks: {hooks}",
            "",
        ]
    lines += [
        "## Compatibility",
        "",
        "The supported catalogue contains demonstrated callable APIs only. Planned designs and future migrations remain in GitHub issues until implementation, consumer need, and evidence justify publishing them. Application repositories/products are not admission fields; compatibility is determined from API surface, trust, permissions, technology inputs/outputs, and acknowledged breaking changes.",
        "",
    ]
    return "\n".join(lines)


def compare_contracts(baseline: ContractData, current: ContractData) -> list[dict[str, Any]]:
    old = {row["api_name"]: row for row in baseline.workflows}
    new = {row["api_name"]: row for row in current.workflows}
    acknowledgements = {
        row.get("api_name"): row
        for row in current.types.get("breaking_change_acknowledgements", [])
        if isinstance(row, dict)
    }
    changes: list[dict[str, Any]] = []
    for api in sorted(set(old) | set(new)):
        if api not in old:
            decision = "compatible"
        elif api not in new:
            if old[api].get("status") in {"planned", "migration-pending"}:
                decision = "compatible"
            else:
                decision = (
                    "breaking-acknowledged"
                    if valid_acknowledgement(acknowledgements.get(api), api)
                    else "breaking-unacknowledged"
                )
        else:
            decision = classify_change(old[api], new[api], acknowledgements.get(api))
        changes.append({"api_name": api, "decision": decision})
    return changes
