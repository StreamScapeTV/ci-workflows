# Organization maintenance architecture

The maintenance architecture separates shared orchestration from durable authority. `ci-workflows` owns reviewed mutation mechanics, exact-state checks, bounded GitHub transport, typed request validation, reusable workflow shape, and public/component registration. Consumer repositories retain event triggers and product policy. Flux retains desired state, target catalogs, allowlists, Kubernetes/SOPS credentials, and live health/rollback acceptance.

## Data and control flow

```text
thin trusted caller
  -> workflow_call with bounded intent + named credential
  -> immutable ci-workflows composite action
  -> canonical scripts/ci/ciw.py command registry
  -> checked-in organization-maintenance contract
  -> named typed maintenance/Flux implementation
  -> GitHub/Flux expected-state revalidation
  -> bounded mutation or dry-run report
  -> terminal cleanup / zero routine artifacts
```

The canonical CIW registry is the only executable dispatch boundary for issue #20. The five composite actions call `scripts/ci/ciw.py` directly, and the retained `scripts/ci/maintenance.py` / `scripts/ci/flux_reconcile.py` files are compatibility adapters that delegate into the same registry rather than implementing a second execution path. The Flux compatibility adapter accepts only the fixed `source` checkout of `StreamScapeTV/flux`; it cannot make repository or source-location selection caller-controlled.

No operation accepts an arbitrary repository URL, branch name, runner label, shell body, callback, secret name, cluster, namespace, service account, kubeconfig path, or unrestricted matrix. Project IDs resolve through `contracts/organization-maintenance.json` to reviewed repository and integration-branch tuples.

## GitHub transport

The GitHub client uses only operation-required REST endpoints. It enforces HTTPS, the reviewed API version, bounded pages, bounded retry attempts, `Retry-After`/exponential delay for 429/transient 5xx responses, and bounded job-log bytes. Response-shape failures are stable fail-closed errors.

The configured API origin is an authority boundary. Absolute pagination links must remain same-origin (and within the configured enterprise API path when one exists), so organization credentials cannot be sent to an attacker-controlled pagination URL. GitHub's signed log redirects are handled separately: only HTTPS GET/HEAD may cross origin and `Authorization` is stripped before the redirected request. Cross-origin mutation redirects are rejected.

Mutation safety uses no second database or replay ledger. Every operation has an expected-state boundary:

- artifact: listing fields plus referenced workflow-run snapshot are re-fetched before delete;
- branch: exact merged PR and branch tip are re-fetched before ref delete;
- conformance: one deterministic report issue converges to no-op when content is unchanged and is re-read before update/create;
- runner retry: exact run snapshot and current same-repository PR/branch source are revalidated before the one rerun request;
- status/comment/label projection: exact commit or issue state is re-read before the single bounded transport mutation.

## Agent State retirement boundary

Issue #20 does not restore Agent State GitHub transport. The central contract names `agent-state-claim`, `agent-state-lifecycle`, and `agent-state-ownership` only as retired/forbidden boundaries so conformance can report stale files. There is no claim/ownership decision, receipt parser, lifecycle dispatcher, compatibility API, Agent State credential, or Supabase access in maintenance runtime code.

The historical cancellation-receipt branch-deletion path is deliberately not centralized. Shared branch hygiene deletes exact merged heads only.

## Credential and permission model

Maintenance and Flux credentials are explicit named reusable-workflow secrets; `secrets: inherit` is forbidden.

- artifact cleanup: `actions: write`, `contents: read`;
- branch hygiene: `contents: write`, `pull-requests: read`;
- conformance: reviewed read/report permissions with mutually exclusive read-only versus update credentials;
- runner retry: `actions: write`, `contents: read`;
- Flux reconciliation: read-only source permissions; cluster credentials only from the protected Flux caller.

Dry-run is the default. Focused PR validation is read-only and receives no maintenance or cluster credential. The machine-readable contract also records each operation's trust class, caller trigger/cadence, policy-source authority, named credential boundary, and public output set.

Generic status/comment/label projection functions are transport primitives, not automatically privileged workflow steps. The invoking domain must already hold the minimum GitHub permission and supply an already-sanitized bounded decision. Central projection code does not determine what a status means, what comment should be written, or which labels should be selected.

## Maintenance/control decision record

The workflow inventory is the classification source. Issue #20 applies these decisions rather than copying repository-specific policy into central code.

| Current responsibility | Classification | Central destination | Authority retained outside central orchestration |
|---|---|---|---|
| Agent State organization artifact cleanup | central public orchestration | `maintenance.artifacts` | caller cadence and credential installation |
| Agent State branch hygiene plus consumer merged-head cleanup variants | central public orchestration + thin callers | `maintenance.branches` | caller close trigger; no repository-specific cancellation receipt is retained |
| Agent State runner-infrastructure retry observer | central public orchestration | `maintenance.runner-retry` | caller cadence; workflow allowlist derives from central inventory |
| organization workflow/shared-reference drift scanning | central report operation | `maintenance.conformance` | repository-specific assertions remain repository-owned; optional exact-SHA repin entries are review-only proposals and consumer edits remain separately authorized work |
| generic commit-status/comment/label transport | central typed function | `ci_workflows.maintenance.project_*` | the owning domain supplies the already-sanitized decision, expected state, and minimum credential scope |
| Flux `reconcile-allowlisted-release` orchestration | central privileged orchestration + thin Flux caller | `flux.reconcile` | target allowlist, desired state, policy adapter, cluster credentials, live health/rollback acceptance |
| Agent State claim/lifecycle/ownership workflow transport | retired coordination transport | none | authoritative Agent State API remains outside GitHub workflow transport |
| runner-retry fixtures, recovery workflows, one-shot diagnostics | retired one-shot/fixture work | none | historical evidence only |
| Flux runner image/chart build/publication control | separate infrastructure-product orchestration | issue #33 / `flux.assets` | Flux product definitions, bootstrap/canary selection, registry/cluster policy |

This decision record intentionally centralizes generic mechanics without turning `ci-workflows` into a store for product, Agent State, or Flux domain decisions.

## Review-only shared-reference proposals

`maintenance.conformance` may receive one optional `shared_reference_target_sha`. The value must be a full exact SHA and must resolve as a commit in `StreamScapeTV/ci-workflows`. The scanner compares each detected `StreamScapeTV/ci-workflows/.github/workflows/...@ref` against that target and emits a concrete current/proposed reference pair when they differ.

This is intentionally **not** an updater. The conformance runtime never writes consumer files, branches, commits, or pull requests. It only places proposal records in the deterministic conformance report so the owning consumer repository can review and perform its own immutable-reference cutover under its normal issue/branch/PR lifecycle.

## Sanitized projection boundary

Projection functions expose fixed GitHub transports rather than a generic callback:

- commit status: one exact SHA, one of GitHub's four bounded status states, a bounded context, and bounded description; no target URL/callback field is accepted;
- comment: one positive issue/PR number, exact expected issue timestamp, bounded deterministic marker, and bounded body; one marked comment is created or updated;
- labels: one positive issue number, exact expected issue timestamp, full expected label set, and full desired label set.

Repository identity is always selected from the checked-in `project_id` mapping. Exact replay is a no-op. A stale source, changed issue, changed relevant status, duplicate marked comment, malformed label set, forged multiline field, or revalidation mismatch fails closed. These functions do not expose shell commands, arbitrary endpoints, repository URLs, branch names, credentials, or domain callbacks.

## Flux policy and filesystem boundary

Central Flux orchestration is intentionally data-blind. The contract records only the Flux repository, allowed high-level operations, exact repository-owned policy/allowlist/executor paths, the adapter-interface name, and the two named credentials required for live mode.

Target/product IDs and all resource objects remain Flux-owned data. Exact policy source maps opaque intent to a structured plan with typed resource references and a bounded workload list. An injected command field, unexpected key, unsupported resource kind, symlink source path, dirty tree, oversized plan, or changed hash is rejected.

The policy adapter runs before credentials exist. Immediately before live execution, central rechecks exact Git HEAD, source cleanliness, and SHA-256 of policy, allowlist, and executor. Runtime state uses `lstat`/no-follow checks: a symlink state root is never followed, credential files are created exclusively with `O_NOFOLLOW` when available and mode `0600`, and pre-existing credential paths fail closed. Cleanup removes only the bounded state paths and treats credential residue as an error.

The executor is Flux-owned and receives a fixed argument vector. Central never synthesizes shell, `kubectl`, Helm, or Flux command text from caller fields.

## Public/component integration

The maintenance and `flux.reconcile` workflows are not complete merely because their YAML/source exists. Final issue #20 integration also requires the public workflow registry, immutable action lock, bootstrap workflow registration, generated reference documentation, and corresponding contract tests to agree with the exact final candidate.

Those high-collision surfaces must be sequenced through Agent State resource ownership. A worker never edits another current owner's registration files to clear Central. Once ownership is reconciled, the same issue branch is updated with `[skip push ci]` implementation checkpoints, self-reviewed against current `main`, and only then returned to a ready-for-review PR for exact-head Central validation.

## Testing without live mutation

Unit/integration tests use synthetic GitHub responses and a temporary local Git repository containing a synthetic Flux adapter/executor. They prove pagination/backoff, redirect credential safety, artifact/run expected-state failures, exact merged-branch behavior, conformance replay safety, immutable repin proposal generation, projection replay/stale-state rejection, infrastructure-only retry, structured Flux planning, source-mutation rejection, no-follow state/credential behavior, executor hash revalidation, private secret-file modes, and terminal cleanup.

The focused maintenance smoke also exercises the checked-in CIW registry contract and the issue #20 typed adapter tests so a direct-script regression cannot hide behind the compatibility wrappers.

No issue-#20 test calls the live GitHub mutation API or Kubernetes cluster. Real organization maintenance, projection, consumer cutover, or Flux reconciliation requires separately authorized thin-caller/domain execution after integration and adoption.
