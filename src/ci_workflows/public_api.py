"""Public API contract facade for the owner-selected initial ``@main`` channel.

The reusable schema, permission, workflow, caller, compatibility, rendering,
and release-manifest validators live in :mod:`ci_workflows.public_api_contract`.
This facade changes only the repository's current reference policy: every trust
class may use protected ``main`` during the initial migration, while full commit
SHAs and immutable SemVer tags remain valid alternatives.
"""
from __future__ import annotations

import copy
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
validate_caller = core.validate_caller
validate_caller_fixtures = core.validate_caller_fixtures
validate_compatibility_fixtures = core.validate_compatibility_fixtures
validate_docs = core.validate_docs
validate_release_schema = core.validate_release_schema
validate_workflows = core.validate_workflows


def validate_reference_policy(types: Mapping[str, Any]) -> None:
    """Validate protected ``main`` plus fixed SHA and SemVer alternatives.

    The shared validator owns compatibility, acknowledgement, artifact, cleanup,
    consumer-update, rollback, and release-retention rules. A normalized copy is
    used only to reuse those common checks without weakening the actual
    owner-selected initial-channel assertions below.
    """

    normalized = copy.deepcopy(dict(types))
    normalized_reference = normalized.get("reference_policy")
    require(
        isinstance(normalized_reference, dict),
        "reference_policy must be an object",
    )
    normalized_reference["bootstrap_mutable_allowed_trust_classes"] = [
        "source-admission",
        "read-only-validation",
    ]
    normalized_reference["privileged_mutable_references_forbidden"] = True
    core._validate_policy(normalized)

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
        all(
            isinstance(policy, dict)
            and policy.get("reference_policy") == "bootstrap-main-or-immutable"
            for policy in trust_classes.values()
        ),
        "every trust class must document main-or-immutable reference support",
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
