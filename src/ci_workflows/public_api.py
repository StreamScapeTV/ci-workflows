"""Public API contract facade for the owner-selected initial ``@main`` channel.

The reusable schema, compatibility, rendering, and release-manifest validators
live in :mod:`ci_workflows.public_api_contract`.  This facade owns the current
supported-catalogue policy: only demonstrated callable APIs are published, every
public permission/trust row is actually represented, and protected ``main`` is
an allowed bootstrap reference for the current supported trust classes.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Mapping

from . import public_api_contract as core

ContractError = core.ContractError
ContractData = core.ContractData
PRIVILEGED_TRUST = core.PRIVILEGED_TRUST
compare_contracts = core.compare_contracts
classify_change = core.classify_change
immutable_reference = core.immutable_reference
load_contract = core.load_contract
read_text = core.read_text
render = core.render
require = core.require
validate_bootstrap_workflow = core.validate_bootstrap_workflow
validate_caller = core.validate_caller
validate_caller_fixtures = core.validate_caller_fixtures
validate_compatibility_fixtures = core.validate_compatibility_fixtures
validate_docs = core.validate_docs
validate_release_schema = core.validate_release_schema


def permission_profiles(data: ContractData) -> dict[str, Mapping[str, Any]]:
    """Validate exactly the permission profiles used by supported public APIs."""

    token_model = data.permissions.get("token_model")
    require(
        token_model
        == {
            "called_workflow_cannot_elevate": True,
            "caller_declares_minimum": True,
            "secrets_inherit_forbidden": True,
            "default_unspecified_permission": "none",
        },
        "token model changed",
    )
    trust_classes = data.types.get("trust_classes")
    secret_catalog = data.types.get("secret_catalog")
    require(isinstance(trust_classes, dict), "trust class catalog is missing")
    require(isinstance(secret_catalog, dict), "secret catalog is missing")
    rows = data.permissions.get("profiles")
    require(isinstance(rows, list) and rows, "permission profiles must be non-empty")
    profiles: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        require(isinstance(row, dict), "permission profile must be an object")
        identifier = core.nonempty(row.get("id"), "permission profile id")
        require(core.PROFILE.fullmatch(identifier) is not None, f"invalid permission profile: {identifier}")
        require(identifier not in profiles, f"duplicate permission profile: {identifier}")
        trust = core.nonempty(row.get("trust_class"), f"{identifier}.trust_class")
        require(trust in trust_classes, f"{identifier} uses unknown trust class")
        caller = core.permission_map(row.get("caller_permissions"), f"{identifier}.caller_permissions")
        workflow = core.permission_map(row.get("workflow_permissions"), f"{identifier}.workflow_permissions")
        require(caller == workflow, f"{identifier} workflow permissions exceed or differ from caller permissions")
        forbidden = core.unique_strings(row.get("forbidden_permissions"), f"{identifier}.forbidden_permissions")
        for value in forbidden:
            require(re.fullmatch(r"[a-z-]+:(?:read|write|none)", value) is not None, f"{identifier} has invalid forbidden permission")
            key, level = value.split(":", 1)
            require(caller.get(key) != level, f"{identifier} simultaneously requires and forbids {value}")
        secrets = core.unique_strings(row.get("named_secrets_allowed"), f"{identifier}.named_secrets_allowed")
        require(set(secrets) <= set(secret_catalog), f"{identifier} uses an unknown secret")
        core.nonempty(row.get("notes"), f"{identifier}.notes")
        profiles[identifier] = row

    referenced = {
        str(row.get("permission_profile"))
        for row in data.workflows
        if isinstance(row, Mapping)
    }
    require(
        set(profiles) == referenced,
        "public permission profile catalog must exactly match supported workflow usage",
    )
    return profiles


def validate_workflows(
    data: ContractData,
    profiles: Mapping[str, Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """Validate only demonstrated callable APIs as the supported catalogue."""

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
    forbidden_inputs = set(core.unique_strings(defaults.get("forbidden_caller_fields"), "forbidden_caller_fields"))
    by_api: dict[str, Mapping[str, Any]] = {}
    files: set[str] = set()
    checks: set[str] = set()
    represented_trust: set[str] = set()

    for row in data.workflows:
        api = core.nonempty(row.get("api_name"), "workflow.api_name")
        require(core.API_NAME.fullmatch(api) is not None, f"invalid api name: {api}")
        require(api not in by_api, f"duplicate api name: {api}")
        require("supported_consumers" not in row and "supported_products" not in row, f"{api} contains application identity metadata")
        version = core.nonempty(row.get("api_version"), f"{api}.api_version")
        require(core.SEMVER.fullmatch(version) is not None, f"{api} has invalid version")
        file = core.nonempty(row.get("file"), f"{api}.file")
        require(core.WORKFLOW_FILE.fullmatch(file) is not None, f"{api} has invalid public workflow file")
        require(file not in files, f"duplicate public workflow file: {file}")
        files.add(file)
        status = core.nonempty(row.get("status"), f"{api}.status")
        require(
            status in {"implemented", "deprecated-bootstrap-exception"},
            f"{api} is not a demonstrated supported public workflow: {status}",
        )
        trust = core.nonempty(row.get("trust_class"), f"{api}.trust_class")
        require(trust in trust_classes, f"{api} has unknown trust class")
        represented_trust.add(trust)
        profile_id = core.nonempty(row.get("permission_profile"), f"{api}.permission_profile")
        profile = profiles.get(profile_id)
        require(profile is not None, f"{api} uses unknown permission profile")
        require(profile.get("trust_class") == trust, f"{api} permission profile trust mismatch")
        semantic = core.nonempty(row.get("semantic_runner_profile"), f"{api}.semantic_runner_profile")
        require(core.PROFILE.fullmatch(semantic) is not None, f"{api} has invalid semantic runner profile")
        require(
            not any(part in semantic.casefold() for part in core.FORBIDDEN_RUNNER_FRAGMENTS),
            f"{api} exposes a concrete runner selector",
        )
        events = core.unique_strings(row.get("permitted_events"), f"{api}.permitted_events", allow_empty=False)
        allowed_events = trust_classes[trust].get("allowed_events")
        require(isinstance(allowed_events, list) and set(events) <= set(allowed_events), f"{api} permits an event outside its trust class")
        input_rows = row.get("inputs")
        require(isinstance(input_rows, list), f"{api}.inputs must be an array")
        inputs: dict[str, bool] = {}
        for item in input_rows:
            require(isinstance(item, dict), f"{api}.inputs contains a non-object")
            name, required = core._validate_input(item, input_catalog, f"{api}.inputs")
            require(name not in inputs, f"{api} has duplicate input {name}")
            require(name not in forbidden_inputs, f"{api} exposes forbidden caller field {name}")
            inputs[name] = required
        secrets = core.unique_strings(row.get("secrets"), f"{api}.secrets")
        require(set(secrets) <= set(secret_catalog), f"{api} uses unknown secrets")
        require(set(secrets) <= set(profile.get("named_secrets_allowed", ())), f"{api} secret exceeds its permission profile")
        outputs = core.unique_strings(row.get("outputs"), f"{api}.outputs", allow_empty=False)
        require(set(outputs) <= set(output_catalog), f"{api} uses unknown outputs")
        check = core.nonempty(row.get("stable_check_name"), f"{api}.stable_check_name")
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
        require(isinstance(depth, int) and 1 <= depth <= core.MAX_PUBLIC_DEPTH, f"{api} call depth is invalid")
        components = core.unique_strings(row.get("implementation_components"), f"{api}.implementation_components", allow_empty=False)
        if depth == 2:
            require(any(component.startswith("internal-") for component in components), f"{api} depth two requires one named internal leaf")
        hooks = core.unique_strings(row.get("repository_owned_hooks"), f"{api}.repository_owned_hooks")
        require(set(hooks) <= set(inputs), f"{api} has a repository-owned hook that is not an input")
        require((data.root / file).is_file(), f"{api} supported workflow is missing")
        if "deprecation" in row:
            deprecation = row["deprecation"]
            require(isinstance(deprecation, dict), f"{api}.deprecation must be an object")
            replacement = core.nonempty(deprecation.get("replacement"), f"{api}.deprecation.replacement")
            require(replacement != api, f"{api} cannot replace itself")
        by_api[api] = row

    require(
        represented_trust == set(trust_classes),
        "public trust class catalog must exactly match supported workflow usage",
    )
    for api, row in by_api.items():
        deprecation = row.get("deprecation")
        if isinstance(deprecation, dict):
            require(deprecation.get("replacement") in by_api, f"{api} has an unknown deprecation replacement")
    return by_api


def validate_reference_policy(types: Mapping[str, Any]) -> None:
    """Validate protected ``main`` plus fixed SHA and SemVer alternatives."""

    normalized = copy.deepcopy(dict(types))
    normalized_reference = normalized.get("reference_policy")
    require(isinstance(normalized_reference, dict), "reference_policy must be an object")
    normalized_reference["bootstrap_mutable_allowed_trust_classes"] = [
        "source-admission",
        "read-only-validation",
    ]
    normalized_reference["privileged_mutable_references_forbidden"] = True
    core._validate_policy(normalized)

    reference = types.get("reference_policy")
    require(isinstance(reference, dict), "reference_policy must be an object")
    require(reference.get("bootstrap_mutable_reference") == "main", "initial mutable reference must be main")
    trust_classes = types.get("trust_classes")
    require(isinstance(trust_classes, dict), "trust class catalog is missing")
    allowed = set(
        core.unique_strings(
            reference.get("bootstrap_mutable_allowed_trust_classes"),
            "bootstrap_mutable_allowed_trust_classes",
            allow_empty=False,
        )
    )
    require(allowed == set(trust_classes), "protected main must be available to every initial trust class")
    require(reference.get("privileged_mutable_references_forbidden") is False, "initial protected-main use must include privileged APIs")
    require(reference.get("immutable_references") == ["full-sha", "immutable-semver-tag"], "full SHA and immutable SemVer alternatives must remain supported")
    require(
        all(
            isinstance(policy, dict)
            and policy.get("reference_policy") == "bootstrap-main-or-immutable"
            for policy in trust_classes.values()
        ),
        "every trust class must document main-or-immutable reference support",
    )


def validate(root: Path) -> ContractData:
    """Validate the complete supported API registry under current policy."""

    data = load_contract(root)
    validate_reference_policy(data.types)
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
