"""Organization-wide semantic validation and native dependency convergence."""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from .issue_dependency_manifest import load_manifest
from .issue_dependency_types import (
    AGENTS_PATH,
    MANIFEST_PATH,
    ConvergenceError,
    DependencyPlan,
    DependencySyncError,
    IssueDependencyGateway,
    IssueRecord,
    IssueRef,
    ManifestValidationError,
    NativeDependency,
    RepositoryManifest,
    RepositoryRecord,
    SyncSummary,
    _REPOSITORY_RE,
    parse_protected_integration_branch,
)

_MAX_STALE_BLOCKERS_PER_WARNING = 5


def discover_manifests(
    gateway: IssueDependencyGateway, schema: Mapping[str, Any]
) -> tuple[tuple[RepositoryRecord, ...], tuple[RepositoryManifest, ...]]:
    """Discover opted-in repositories without treating unmanaged repositories as errors."""
    repositories = tuple(sorted(gateway.list_repositories()))
    manifests: list[RepositoryManifest] = []
    for repository in repositories:
        if not _REPOSITORY_RE.fullmatch(repository.full_name):
            continue
        if not repository.default_branch:
            continue
        agents = gateway.read_file(
            repository.full_name, AGENTS_PATH, repository.default_branch
        )
        if agents is None:
            continue
        integration_branch = parse_protected_integration_branch(agents)
        if integration_branch is None:
            continue
        manifest_text = gateway.read_file(
            repository.full_name, MANIFEST_PATH, integration_branch
        )
        if manifest_text is None:
            continue
        manifests.append(
            load_manifest(
                manifest_text,
                schema,
                expected_repository=repository.full_name,
                integration_branch=integration_branch,
            )
        )
    return repositories, tuple(manifests)


def _graph_from_manifests(
    manifests: Sequence[RepositoryManifest],
) -> dict[IssueRef, tuple[IssueRef, ...]]:
    graph: dict[IssueRef, tuple[IssueRef, ...]] = {}
    for manifest in manifests:
        for managed in manifest.issues:
            if managed.dependent in graph:
                raise ManifestValidationError(
                    f"duplicate managed dependent: {managed.dependent.url}"
                )
            graph[managed.dependent] = managed.blockers
    return graph


def _detect_cycles(graph: Mapping[IssueRef, Sequence[IssueRef]]) -> None:
    visiting: set[IssueRef] = set()
    visited: set[IssueRef] = set()
    stack: list[IssueRef] = []

    def visit(node: IssueRef) -> None:
        if node in visited:
            return
        if node in visiting:
            try:
                start = stack.index(node)
            except ValueError:
                start = 0
            cycle = stack[start:] + [node]
            raise ManifestValidationError(
                "dependency cycle detected: " + " -> ".join(ref.url for ref in cycle)
            )
        visiting.add(node)
        stack.append(node)
        for blocker in sorted(graph.get(node, ())):
            if blocker in graph:
                visit(blocker)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for node in sorted(graph):
        visit(node)


def _normalized_state_reason(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _load_issue_records(
    gateway: IssueDependencyGateway,
    graph: Mapping[IssueRef, Sequence[IssueRef]],
) -> dict[IssueRef, IssueRecord]:
    dependents = set(graph)
    blockers = {blocker for values in graph.values() for blocker in values}
    all_refs = tuple(sorted(dependents | blockers))
    records: dict[IssueRef, IssueRecord] = {}
    for ref in all_refs:
        record = gateway.get_issue(ref)
        if record is None:
            raise ManifestValidationError(f"missing issue reference: {ref.url}")
        if record.ref != ref:
            raise DependencySyncError(
                f"GitHub issue identity mismatch for expected {ref.url}"
            )
        if record.is_pull_request:
            raise ManifestValidationError(
                f"pull request references are forbidden: {ref.url}"
            )
        records[ref] = record
    return records


def _validate_closed_blockers(
    blockers: Sequence[IssueRef],
    records: Mapping[IssueRef, IssueRecord],
) -> None:
    for blocker in sorted(blockers):
        record = records[blocker]
        if record.state.lower() != "closed":
            continue
        reason = _normalized_state_reason(record.state_reason)
        if reason == "completed":
            continue
        if reason in {"duplicate", "not_planned"}:
            raise ManifestValidationError(
                f"blocker {blocker.url} is closed {reason}; update the manifest"
            )
        raise ManifestValidationError(
            f"blocker {blocker.url} has unsupported closed state reason "
            f"{record.state_reason!r}"
        )


def validate_semantics(
    gateway: IssueDependencyGateway,
    manifests: Sequence[RepositoryManifest],
) -> dict[IssueRef, IssueRecord]:
    """Validate all desired issue references and blocker states before any write."""
    graph = _graph_from_manifests(manifests)
    _detect_cycles(graph)
    records = _load_issue_records(gateway, graph)
    blockers = tuple({blocker for values in graph.values() for blocker in values})
    _validate_closed_blockers(blockers, records)
    return records


def _partition_stale_manifests(
    manifests: Sequence[RepositoryManifest],
    records: Mapping[IssueRef, IssueRecord],
) -> tuple[
    tuple[RepositoryManifest, ...],
    dict[str, tuple[tuple[IssueRef, str], ...]],
]:
    """Quarantine manifests whose blockers became duplicate/not-planned after authoring."""
    stale_by_repository: dict[str, dict[IssueRef, str]] = {}
    for manifest in manifests:
        for managed in manifest.issues:
            for blocker in managed.blockers:
                record = records[blocker]
                if record.state.lower() != "closed":
                    continue
                reason = _normalized_state_reason(record.state_reason)
                if reason == "completed":
                    continue
                if reason in {"duplicate", "not_planned"}:
                    stale_by_repository.setdefault(manifest.repository, {})[blocker] = reason
                    continue
                raise ManifestValidationError(
                    f"blocker {blocker.url} has unsupported closed state reason "
                    f"{record.state_reason!r}"
                )

    healthy = tuple(
        manifest
        for manifest in manifests
        if manifest.repository not in stale_by_repository
    )
    stale = {
        repository: tuple(sorted(items.items()))
        for repository, items in sorted(stale_by_repository.items())
    }
    return healthy, stale


def _stale_manifest_warning(
    repository: str,
    blockers: Sequence[tuple[IssueRef, str]],
) -> str:
    visible = blockers[:_MAX_STALE_BLOCKERS_PER_WARNING]
    rendered = ", ".join(f"{ref.url} ({reason})" for ref, reason in visible)
    remaining = len(blockers) - len(visible)
    if remaining:
        rendered += f", +{remaining} more"
    return (
        f"{repository}: skipped issue-dependency reconciliation because desired "
        f"blocker state is stale: {rendered}; update {MANIFEST_PATH}"
    )


def build_plans(
    gateway: IssueDependencyGateway,
    manifests: Sequence[RepositoryManifest],
    issue_records: Mapping[IssueRef, IssueRecord],
) -> tuple[DependencyPlan, ...]:
    """Read native state and build a deterministic organization-wide mutation plan."""
    graph = _graph_from_manifests(manifests)
    plans: list[DependencyPlan] = []
    for dependent in sorted(graph):
        desired_refs = tuple(sorted(graph[dependent]))
        desired_urls = tuple(ref.url for ref in desired_refs)

        native_by_url: dict[str, NativeDependency] = {}
        for native in gateway.list_blocked_by(dependent):
            if native.url in native_by_url:
                raise DependencySyncError(
                    f"GitHub returned duplicate native blocker for "
                    f"{dependent.url}: {native.url}"
                )
            native_by_url[native.url] = native

        desired_set = set(desired_urls)
        native_set = set(native_by_url)
        additions = tuple(
            issue_records[ref] for ref in desired_refs if ref.url not in native_set
        )
        removals = tuple(
            native_by_url[url] for url in sorted(native_set - desired_set)
        )
        plans.append(
            DependencyPlan(
                dependent=dependent,
                desired_urls=desired_urls,
                additions=additions,
                removals=removals,
            )
        )
    return tuple(plans)


def apply_plans(
    gateway: IssueDependencyGateway,
    plans: Sequence[DependencyPlan],
) -> tuple[int, int]:
    """Apply the already-validated plan, then require exact readback convergence."""
    additions = 0
    removals = 0
    for plan in plans:
        # Add desired blockers before removing stale blockers. During a replacement
        # this avoids a transient dependency-free window visible to other readers.
        for blocker in plan.additions:
            gateway.add_blocked_by(plan.dependent, blocker)
            additions += 1
        for native in plan.removals:
            gateway.remove_blocked_by(plan.dependent, native)
            removals += 1

    for plan in plans:
        readback = gateway.list_blocked_by(plan.dependent)
        current = tuple(sorted(native.url for native in readback))
        desired = tuple(sorted(plan.desired_urls))
        if current != desired:
            raise ConvergenceError(
                f"dependency readback did not converge for {plan.dependent.url}: "
                f"desired={list(desired)!r}, current={list(current)!r}"
            )
    return additions, removals


def _summary(
    repositories: Sequence[RepositoryRecord],
    manifests: Sequence[RepositoryManifest],
    additions: int,
    removals: int,
) -> SyncSummary:
    managed_issues = sum(len(manifest.issues) for manifest in manifests)
    desired_edges = sum(
        len(managed.blockers)
        for manifest in manifests
        for managed in manifest.issues
    )
    return SyncSummary(
        repositories_scanned=len(repositories),
        managed_repositories=len(manifests),
        managed_issues=managed_issues,
        desired_edges=desired_edges,
        additions=additions,
        removals=removals,
        mutations=additions + removals,
    )


def sync_organization(
    gateway: IssueDependencyGateway, schema: Mapping[str, Any]
) -> SyncSummary:
    """Strictly discover, validate, reconcile, and read back every opted-in repository."""
    repositories, manifests = discover_manifests(gateway, schema)
    # All manifests are parsed/schema-validated by discover_manifests before any
    # issue/native state lookup or mutation begins.
    issue_records = validate_semantics(gateway, manifests)
    plans = build_plans(gateway, manifests, issue_records)
    additions, removals = apply_plans(gateway, plans)
    return _summary(repositories, manifests, additions, removals)


def sync_organization_resilient(
    gateway: IssueDependencyGateway,
    schema: Mapping[str, Any],
    *,
    warn: Callable[[str], None] | None = None,
) -> SyncSummary:
    """Reconcile healthy manifests while quarantining stale closed-blocker drift.

    A blocker becoming ``duplicate`` or ``not_planned`` is expected repository
    drift after a previously valid manifest was authored. The desired edge is
    still invalid and is never treated as satisfied, but it must not prevent
    unrelated repositories from converging during the hourly organization run.
    Schema errors, missing/PR references, cycles, unsupported closed reasons,
    GitHub API failures, and convergence failures remain fatal.
    """
    repositories, manifests = discover_manifests(gateway, schema)
    graph = _graph_from_manifests(manifests)
    _detect_cycles(graph)
    issue_records = _load_issue_records(gateway, graph)
    healthy_manifests, stale_manifests = _partition_stale_manifests(
        manifests, issue_records
    )

    if warn is not None:
        for repository, blockers in stale_manifests.items():
            warn(_stale_manifest_warning(repository, blockers))

    plans = build_plans(gateway, healthy_manifests, issue_records)
    additions, removals = apply_plans(gateway, plans)
    return _summary(repositories, manifests, additions, removals)
