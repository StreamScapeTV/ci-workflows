"""Public typed facade for organization issue-dependency reconciliation."""
from __future__ import annotations

from .issue_dependency_manifest import load_manifest, validate_json_schema
from .issue_dependency_reconcile import (
    apply_plans,
    build_plans,
    discover_manifests,
    sync_organization,
    sync_organization_resilient,
    validate_semantics,
)
from .issue_dependency_types import (
    AGENTS_PATH,
    MANIFEST_PATH,
    ConvergenceError,
    DependencyPlan,
    DependencySyncError,
    IssueDependencyGateway,
    IssueRecord,
    IssueRef,
    ManagedIssue,
    ManifestValidationError,
    NativeDependency,
    RepositoryManifest,
    RepositoryRecord,
    SyncSummary,
    parse_protected_integration_branch,
)

__all__ = (
    "AGENTS_PATH",
    "MANIFEST_PATH",
    "ConvergenceError",
    "DependencyPlan",
    "DependencySyncError",
    "IssueDependencyGateway",
    "IssueRecord",
    "IssueRef",
    "ManagedIssue",
    "ManifestValidationError",
    "NativeDependency",
    "RepositoryManifest",
    "RepositoryRecord",
    "SyncSummary",
    "apply_plans",
    "build_plans",
    "discover_manifests",
    "load_manifest",
    "parse_protected_integration_branch",
    "sync_organization",
    "sync_organization_resilient",
    "validate_json_schema",
    "validate_semantics",
)
