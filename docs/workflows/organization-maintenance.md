# Organization maintenance and Flux reconciliation

Issue #20 centralizes the domain-neutral GitHub maintenance operations that were previously duplicated or hosted in product repositories. The public reusable workflows are deliberately trigger-free: schedules, pull-request-close events, protected environments, and repository-specific cadence remain thin-caller responsibilities.

## Public maintenance APIs

The checked-in public contract remains authoritative. This implementation supplies the existing planned files without adding caller-selected repositories, runner labels, shell commands, callbacks, credentials, or product configuration.

| API | Public workflow | Default | Authority |
|---|---|---|---|
| `maintenance.artifacts` | `.github/workflows/reusable-artifact-cleanup.yml` | dry-run | delete only expired, non-retained Actions artifacts after exact-state revalidation |
| `maintenance.branches` | `.github/workflows/reusable-branch-hygiene.yml` | dry-run | delete only the exact unchanged unprotected head of one same-repository merged PR |
| `maintenance.conformance` | `.github/workflows/reusable-conformance.yml` | dry-run | compare live workflow trees with the checked-in inventory and create/update one reviewable report issue |
| `maintenance.runner-retry` | `.github/workflows/reusable-runner-infrastructure-retry.yml` | dry-run | rerun failed jobs once only when exact-head evidence proves self-hosted runner infrastructure loss |
| `flux.reconcile` | `.github/workflows/reusable-flux-reconcile.yml` | dry-run | resolve a structured plan from exact Flux-owned policy and optionally execute it on `flux-control` capacity |

Maintenance jobs run on `[linux, amd64, general]`. The Flux wrapper runs only on `[linux, amd64, flux-control]`. These are central/infrastructure selectors; callers never provide runner labels.

## Thin-caller cadence

Reusable workflows expose only `workflow_call`. Existing schedules and event triggers migrate into thin callers rather than into the shared implementation:

- organization artifact cleanup may retain the reviewed `17,47 * * * *` cadence;
- branch hygiene is most useful after a pull request closes, passing the exact merged head SHA and optional PR number;
- conformance may run on a reviewed maintenance schedule and on demand;
- runner-infrastructure retry may be invoked after a failed run is identified or by a thin bounded observer;
- Flux continues to own protected dispatch/environment authorization before calling `flux.reconcile`.

A caller must pass a bounded `request_id`. No replay ledger is created. Idempotency comes from re-reading GitHub immediately before mutation: missing artifacts/branches converge to no-op, changed state fails closed, and runner retries require `run_attempt == 1` plus a still-current target SHA.

## Artifact retention

Routine artifacts remain zero by default. `maintenance.artifacts` reads `contracts/artifact-exceptions.json`; an artifact covered by a still-active named exception is preserved for that exception's bounded retention period. Active workflow-run artifacts are also preserved. Deletion candidates are capped and individually re-fetched immediately before deletion.

The operation does not infer evidence policy from artifact names outside the central exception contract, and it never deletes an artifact merely because a listing is old.

## Exact merged-branch hygiene

The branch name is never a public input. `project_id` resolves to one checked-in repository/integration-branch tuple, and the workflow derives the issue branch from an exact same-repository merged pull request. Before deletion it verifies:

- the PR is closed and merged to the expected integration branch;
- the PR head repository is the same repository;
- the head name starts with `issue/`;
- the branch is not protected or the integration branch;
- the current branch tip equals `expected_head_sha`;
- the PR and branch snapshots are unchanged at the mutation boundary.

There is no Agent State cancellation-receipt compatibility path. Closed-but-unmerged branches are preserved.

## Conformance reporting

Conformance uses `contracts/workflow-inventory.json` as the classification source. It detects missing inventory workflows, newly added live workflows, workflows still classified `retire`, legacy Agent State transport that remains present, and mutable/immutable references to central reusable workflows.

Dry-run returns findings only. Update mode creates or replaces one deterministic report issue in `StreamScapeTV/ci-workflows`; it does not directly rewrite consumer repositories. A separate owner may turn a finding into a reviewable migration PR.

## Runner-infrastructure retry

The retry API is intentionally narrower than a generic rerun command. Eligibility requires all of the following:

- a workflow path classified in the checked-in inventory and not marked `retire`;
- an allowed read-only workflow trust class and supported event;
- completed attempt 1 with conclusion `failure` or `cancelled`;
- exact still-current same-repository PR or integration-branch source;
- at most 20 failed jobs;
- every failed job is self-hosted, has no failed user step, and has an allowlisted infrastructure-loss signature;
- no deterministic product/test/compiler/lint/dependency/timeout/manual-cancellation signature;
- the workflow run and current target are unchanged immediately before rerun.

Only GitHub's `rerun-failed-jobs` operation is used, once. Product failures are never converted into retries.

## Flux reconciliation

`flux.reconcile` checks out exact `StreamScapeTV/flux` source at `admitted_sha` and accepts only the contract-matched `policy_path` and `allowlist_path`. Central code contains no namespaces, release names, workloads, clusters, or target catalog.

The Flux-owned policy adapter contract is `central-flux-policy-v1`. Central supplies a JSON request containing only `target_id`, `product_id`, and `operation`. The adapter returns a bounded structured resource plan; command strings and extra fields are rejected. Central hashes the policy, allowlist, and executor, verifies the Flux Git tree stayed clean, and revalidates all hashes immediately before any live apply.

The current Flux rollout validator predates this adapter interface. Consumer adoption therefore requires a Flux-owned change that teaches the reviewed policy path the `central-flux-policy-v1` request/plan interface while preserving Flux as the sole target/allowlist authority. Issue #20 does not rewrite Flux policy data or claim live reconciliation evidence before that adoption is reviewed.

Live mode requires both named Flux credentials and pre-provisioned `flux`/`kubectl` tools. Credentials are written only to workflow-scoped state for the apply call and removed in the terminal path. The exact Flux-owned executor performs the actual reconcile/wait operations.

## Evidence and cleanup

`.github/workflows/maintenance-contract-smoke.yml` is an unprivileged pull-request-only focused check. It runs the maintenance/Flux unit suites against the exact PR head, verifies a clean source tree, and confirms the run retained zero Actions artifacts. It does not receive maintenance or Flux credentials and cannot perform organization or cluster mutations.
