"""Public API contract facade for the owner-selected initial ``@main`` channel.

The reusable schema, permission, workflow, compatibility, rendering, and
release-manifest validators live in :mod:`ci_workflows.public_api_contract`.
This facade applies the repository's current reference policy: every trust class
may use protected ``main`` during the initial migration, while full commit SHAs
and immutable SemVer tags remain valid alternatives.
"""
from __future__ import annotations

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
permission_profiles = core.permission_profiles
read_text = core.read_text
render = core.render
require = core.require
validate_bootstrap_workflow = core.validate_bootstrap_workflow
validate_compatibility_fixtures = core.validate_compatibility_fixtures
validate_docs = core.validate_docs
validate_release_schema = core.validate_release_schema
validate_workflows = core.validate_workflows


def validate_reference_policy(types: Mapping[str, Any]) -> None:
    """Validate the initial protected-main policy and immutable alternatives."""

    reference = types.get("reference_policy")
    require(isinstance(reference, dict), "reference_policy must be an object")
    require(
        reference.get("bootstrap_mutable_reference") == "main",
        "initial mutable reference must be main",
    )
    trust_classes = types.get("trust_classes")
    require(isinstance(trust_classes, dict), "trust class catalog is missing")
    allowed = set(
        core.unique_strings(
            reference.get("bootstrap_mutable_allowed_trust_classes"),
            "bootstrap_mutable_allowed_trust_classes",
            allow_empty=False,
        )
    )
    require(
        allowed == set(trust_classes),
        "protected main must be available to every initial trust class",
    )
    require(
        reference.get("privileged_mutable_references_forbidden") is False,
        "initial protected-main use must include privileged APIs",
    )
    require(
        reference.get("immutable_references")
        == ["full-sha", "immutable-semver-tag"],
        "full SHA and immutable SemVer alternatives must remain supported",
    )
    require(
        reference.get("consumer_updates") == "reviewable-pull-request",
        "consumer updates must be reviewable",
    )
    require(
        reference.get("rollback_reference_required") is True,
        "rollback reference must be required",
    )
    require(
        reference.get("delete_referenced_release_forbidden") is True,
        "referenced releases must not be deleted",
    )
    require(
        all(
            isinstance(policy, dict)
            and policy.get("reference_policy") == "bootstrap-main-or-immutable"
            for policy in trust_classes.values()
        ),
        "every trust class must document main-or-immutable reference support",
    )

    compatibility = types.get("compatibility_policy")
    require(isinstance(compatibility, dict), "compatibility_policy must be an object")
    for name in ("compatible", "conditional", "breaking"):
        core.unique_strings(
            compatibility.get(name),
            f"compatibility_policy.{name}",
            allow_empty=False,
        )
    acknowledgement_fields = set(
        core.unique_strings(
            compatibility.get("acknowledgement_required_fields"),
            "compatibility_policy.acknowledgement_required_fields",
            allow_empty=False,
        )
    )
    require(
        acknowledgement_fields
        == {"id", "api_name", "kind", "reason", "migration_issue", "effective_version"},
        "breaking acknowledgement fields changed",
    )
    defaults = types.get("defaults")
    require(isinstance(defaults, dict), "public API defaults are missing")
    require(
        defaults.get("artifact_policy") == "zero-default-named-exception-only",
        "artifact policy changed",
    )
    require(
        defaults.get("cleanup_policy") == "always-residue-checked",
        "cleanup policy changed",
    )


def validate_caller(
    case: Mapping[str, Any],
    data: ContractData,
    workflows: Mapping[str, Mapping[str, Any]],
    profiles: Mapping[str, Mapping[str, Any]],
) -> str | None:
    """Validate one caller while treating protected ``main`` as admitted.

    The core validator already owns all event, permission, secret, input, and
    immutable-reference checks. For the protected initial channel, substitute a
    syntactically immutable reference only for that one reference-shape check;
    every other validation remains exactly the core implementation.
    """

    if case.get("reference") != "main":
        return core.validate_caller(case, data, workflows, profiles)

    trust = case.get("trust_class")
    allowed = set(
        data.types["reference_policy"][
            "bootstrap_mutable_allowed_trust_classes"
        ]
    )
    if trust not in allowed:
        return "mutable-reference-forbidden"
    normalized = dict(case)
    normalized["reference"] = "0" * 40
    return core.validate_caller(normalized, data, workflows, profiles)


def validate_caller_fixtures(
    data: ContractData,
    workflows: Mapping[str, Mapping[str, Any]],
    profiles: Mapping[str, Mapping[str, Any]],
) -> None:
    fixtures = core.read_json(data.root / "tests/fixtures/public-api/callers.json")
    require(
        isinstance(fixtures, dict) and fixtures.get("schema_version") == 1,
        "caller fixture schema is invalid",
    )
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
    require(
        set(data.types["trust_classes"]) <= represented_trust,
        "valid caller fixtures do not cover every trust class",
    )

    for case in invalid:
        require(isinstance(case, dict), "invalid caller fixture is not an object")
        expected = core.nonempty(
            case.get("expected_error"), "invalid caller expected_error"
        )
        actual = validate_caller(case, data, workflows, profiles)
        require(
            actual == expected,
            f"invalid caller fixture {case.get('id')} expected {expected}, got {actual}",
        )


def validate(root: Path) -> ContractData:
    """Validate the complete API registry under the current reference policy."""

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
        require(
            read_text(reference) == render(data),
            "generated public API reference is stale",
        )
    return data
