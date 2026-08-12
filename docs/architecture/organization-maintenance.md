# Organization maintenance architecture

The maintenance architecture separates shared orchestration from durable authority. `ci-workflows` owns reviewed mutation mechanics, exact-state checks, bounded GitHub transport, typed request validation, reusable workflow shape, and public/component registration. Consumer repositories retain event triggers and product policy. Flux retains desired state, target catalogs, allowlists, Kubernetes/SOPS credentials, and live health/rollback acceptance.

## Data and control flow

```text
thin trusted caller
  -> workflow_call with bounded intent + named credential
  -> exact ci-workflows source
  -> checked-in organization-maintenance contract
  -> typed Python operation
  -> GitHub/Flux expected-state revalidation
  -> bounded mutation or dry-run report
  -> terminal cleanup / zero routine artifacts
```

No operation accepts an arbitrary repository URL, branch name, runner label, shell body, callback, secret name, cluster, namespace, service account, kubeconfig path, or unrestricted matrix. Project IDs resolve through `contracts/organization-maintenance.json` to reviewed repository and integration-branch tuples.

## GitHub transport

The GitHub client uses only operation-required REST endpoints. It enforces HTTPS, the reviewed API version, bounded pages, bounded retry attempts, `Retry-After`/exponential delay for 429/transient 5xx responses, and bounded job-log bytes. Response-shape failures are stable fail-closed errors.

The configured API origin is an authority boundary. Absolute pagination links must remain same-origin (and within the configured enterprise API path when one exists), so organization credentials cannot be sent to an attacker-controlled pagination URL. GitHub's signed log redirects are handled separately: only HTTPS GET/HEAD may cross origin and `Authorization` is stripped before the redirected request. Cross-origin mutation redirects are rejected.

Mutation safety uses no second database or replay ledger. Every operation has an expected-state boundary:

- artifact: listing fields plus referenced workflow-run snapshot are re-fetched before delete;
- branch: exact merged PR and branch tip are re-fetched before ref delete;
- conformance: one deterministic report issue converges to no-op when content is unchanged;
- runner retry: exact run snapshot and current same-repository PR/branch source are revalidated before the one rerun request.

## Agent State retirement boundary

Issue #20 does not restore Agent State GitHub transport. The central contract names `agent-state-claim`, `agent-state-lifecycle`, and `agent-state-ownership` only as retired/forbidden boundaries so conformance can report stale files. There is no claim/ownership decision, receipt parser, lifecycle dispatcher, compatibility API, Agent State credential, or Supabase access in maintenance runtime code.

The historical cancellation-receipt branch-deletion path is deliberately not centralized. Shared branch hygiene deletes exact merged heads only.

## Credential and permission model

Maintenance and Flux credentials are explicit named reusable-workflow secrets; `secrets: inherit` is forbidden.

- artifact cleanup: `actions: write`, `contents: read`;
- branch hygiene: `contents: write`, `pull-requests: read`;
- conformance: reviewed read/report permissions;
- runner retry: `actions: write`, `contents: read`;
- Flux reconciliation: read-only source permissions; cluster credentials only from the protected Flux caller.

Dry-run is the default. Focused PR validation is read-only and receives no maintenance or cluster credential.

## Flux policy and filesystem boundary

Central Flux orchestration is intentionally data-blind. The contract records only the Flux repository, allowed high-level operations, exact repository-owned policy/allowlist/executor paths, the adapter-interface name, and the two named credentials required for live mode.

Target/product IDs and all resource objects remain Flux-owned data. Exact policy source maps opaque intent to a structured plan with typed resource references and a bounded workload list. An injected command field, unexpected key, unsupported resource kind, symlink source path, dirty tree, oversized plan, or changed hash is rejected.

The policy adapter runs before credentials exist. Immediately before live execution, central rechecks exact Git HEAD, source cleanliness, and SHA-256 of policy, allowlist, and executor. Runtime state uses `lstat`/no-follow checks: a symlink state root is never followed, credential files are created exclusively with `O_NOFOLLOW` when available and mode `0600`, and pre-existing credential paths fail closed. Cleanup removes only the bounded state paths and treats credential residue as an error.

The executor is Flux-owned and receives a fixed argument vector. Central never synthesizes shell, `kubectl`, Helm, or Flux command text from caller fields.

## Public/component integration

The maintenance and `flux.reconcile` workflows are not complete merely because their YAML/source exists. Final issue #20 integration also requires the public workflow registry, implementation-component/CIW command surfaces, semantic runner resolution/generated mapping, generated reference documentation, and corresponding contract tests to agree with the exact final candidate.

Those high-collision surfaces must be sequenced through Agent State resource ownership. A worker never edits another current owner's registration files to clear Central. Once ownership is reconciled, the same issue branch is updated with `[skip push ci]` implementation checkpoints, self-reviewed against current `main`, and only then returned to a ready-for-review PR for exact-head Central validation.

## Testing without live mutation

Unit/integration tests use synthetic GitHub responses and a temporary local Git repository containing a synthetic Flux adapter/executor. They prove pagination/backoff, redirect credential safety, artifact/run expected-state failures, exact merged-branch behavior, conformance replay safety, infrastructure-only retry, structured Flux planning, source-mutation rejection, no-follow state/credential behavior, executor hash revalidation, private secret-file modes, and terminal cleanup.

No issue-#20 test calls the live GitHub mutation API or Kubernetes cluster. Real organization maintenance or Flux reconciliation requires separately authorized thin-caller execution after integration and consumer adoption.
