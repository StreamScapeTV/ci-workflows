# Organization maintenance and Flux reconciliation

Issue #20 centralizes domain-neutral GitHub maintenance and trusted Flux reconciliation orchestration. Public reusable workflows expose only `workflow_call`; schedules, close events, environments, and repository-specific cadence remain in thin trusted callers.

## Public APIs

| API | Workflow | Default | Bounded authority |
|---|---|---|---|
| `maintenance.artifacts` | `reusable-artifact-cleanup.yml` | dry-run | remove only expired, non-retained artifacts after artifact **and workflow-run** expected-state revalidation |
| `maintenance.branches` | `reusable-branch-hygiene.yml` | dry-run | remove only an unchanged, unprotected same-repository head proven merged to its checked-in integration branch |
| `maintenance.conformance` | `reusable-conformance.yml` | dry-run | compare live workflow trees with inventory, optionally propose review-only repins to one exact central SHA, and maintain one deterministic report issue |
| `maintenance.runner-retry` | `reusable-runner-infrastructure-retry.yml` | dry-run | rerun attempt-1 failed jobs only for proven self-hosted runner-infrastructure loss |
| `flux.reconcile` | `reusable-flux-reconcile.yml` | dry-run | resolve a typed plan from exact Flux-owned policy and optionally apply it on `flux-control` capacity |

Maintenance runs on `[linux, amd64, general]`; Flux reconciliation runs on `[linux, amd64, flux-control]`. Callers never choose runner labels, repositories, shell commands, callbacks, cluster targets, or secret names.

## GitHub transport and replay safety

The maintenance client accepts HTTPS only, bounds retries/pages/log bytes, honors bounded `Retry-After`, and rejects absolute pagination links outside the configured GitHub API origin. GitHub log-download redirects may cross origin only for GET/HEAD over HTTPS; `Authorization` is stripped before the redirected request so the maintenance credential is never forwarded to signed-object hosts.

Mutations re-read state at the mutation boundary rather than relying on a replay database:

- artifact cleanup re-fetches the artifact and its associated workflow-run snapshot immediately before delete; a rerun/status/source change rejects deletion;
- branch hygiene re-fetches the merged PR and branch tip immediately before ref delete;
- conformance creates one deterministic issue and exact replay becomes a no-op when its body is already current;
- runner retry revalidates run snapshot plus exact PR head ref/repository/base or protected integration-branch SHA before the one rerun request;
- generic status/comment/label projection revalidates the exact commit or issue snapshot immediately before its one bounded write.

Dry-run is the default. Missing already-deleted resources converge to no-op; changed state fails closed.

## Artifact retention

`maintenance.artifacts` reads `contracts/artifact-exceptions.json`. A still-valid named exception is preserved for its bounded retention period. Artifacts from non-completed runs are preserved. Candidates are capped and an old listing alone is never sufficient deletion authority.

## Branch hygiene

The caller supplies a checked-in `project_id`, exact expected head SHA, and optional PR number—not a branch name. Central derives the `issue/` branch from an exact same-repository merged PR. Closed-but-unmerged, protected, integration, changed, ambiguous, or foreign-repository branches are preserved. No retired Agent State cancellation-receipt path exists.

## Conformance, review-only repins, and retry

Conformance uses `contracts/workflow-inventory.json` and reports missing/new/retired workflows, retired Agent State transport, and mutable/immutable shared-workflow references. An optional `shared_reference_target_sha` must be a full 40-hex SHA that resolves in `StreamScapeTV/ci-workflows`. When supplied, the report adds concrete `current_reference` → `proposed_reference` entries only when a consumer reference differs from that exact target.

Those entries are **review-only proposals**. `maintenance.conformance` never rewrites a consumer workflow, opens a consumer branch, pushes a commit, or creates a consumer pull request. Consumer cutovers remain separately authorized repository work. Update mode only creates or updates the deterministic report issue in `ci-workflows` after expected-state revalidation.

Runner retry requires a non-retired allowlisted workflow, supported event/trust, completed attempt 1, same-repository current source, bounded failed-job count/logs, self-hosted evidence, no failed user step, an allowlisted infrastructure-loss signature, and no deterministic product/test/compiler/lint/dependency/timeout/manual-cancel signature.

## Sanitized projection helpers

Issue #20 also exposes typed Python transport helpers for already-sanitized domain decisions. They are not a generic callback workflow and are not invoked automatically by `maintenance.conformance`.

- `project_status` accepts a contract-allowlisted `project_id`, exact commit SHA, one GitHub status state, a bounded context, and a bounded description. It exposes no target URL or callback. Exact replay is a no-op; a changed commit/status snapshot fails closed.
- `project_comment` accepts a contract-allowlisted project, positive issue/PR number, exact expected `updated_at`, bounded deterministic marker, and bounded body. It creates or updates one marked comment only after revalidation; duplicate marked comments fail closed.
- `project_labels` accepts a contract-allowlisted project, positive issue number, exact expected `updated_at`, the complete expected label set, and the complete desired label set. If current state differs from the caller's expected snapshot, no label mutation occurs.

These functions own transport mechanics only. They do not decide product state, release state, issue policy, labels, status meaning, or comment content. A calling domain must supply the already-reviewed bounded decision and the minimum GitHub permission needed for that specific projection.

## Flux boundary

`flux.reconcile` checks out exact `StreamScapeTV/flux@admitted_sha`. `contracts/organization-maintenance.json` fixes the Flux-owned policy, allowlist and executor paths plus the `central-flux-policy-v1` interface. Central stores no target catalog, namespaces, workloads, desired state, credentials, or health policy.

The policy receives only `target_id`, `product_id`, and `operation` and must emit the bounded typed plan. Plan files are regular non-symlink files with a size limit; extra command fields and unsupported resource kinds fail closed. Exact source cleanliness and policy/allowlist/executor hashes are revalidated before live apply.

Workflow state must be a real private directory, never a symlink. Kubeconfig and SOPS age-key files are created exclusively with no-follow semantics and mode `0600`, supplied only through `KUBECONFIG` / `SOPS_AGE_KEY_FILE`, and verified removed in terminal cleanup. Live mutation remains prohibited until the Flux-owned adapter/caller authorization is separately reviewed.

## Validation

`maintenance-contract-smoke.yml` is pull-request-only, unprivileged, exact-head, and receives no maintenance/cluster secrets. It runs maintenance/Flux contract, runtime, proposal, projection, workflow, and security suites, removes Python state, verifies the source tree remains clean, and confirms zero Actions artifacts.

No issue-#20 implementation test or this recovered Agent 4 delivery runs live GitHub projection, maintenance deletion/retry, or Flux/Kubernetes mutation. Required final CI validates synthetic and contract behavior; real privileged execution remains separately authorized.

Before final merge, shared public/action/bootstrap/source-identity/lifecycle registrations must agree with these workflows and the full canonical repository suite must pass on the unchanged final candidate.
