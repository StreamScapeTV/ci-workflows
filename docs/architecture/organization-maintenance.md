# Organization maintenance architecture

The maintenance architecture separates shared orchestration from durable authority. `ci-workflows` owns reviewed mutation mechanics, exact-state checks, bounded GitHub transport, typed request validation, and reusable workflow shape. Consumer repositories retain event triggers and product policy. Flux retains all cluster desired state, target catalogs, allowlists, Kubernetes/SOPS credentials, and health semantics.

## Data and control flow

```text
thin trusted caller
  -> workflow_call with bounded intent + named credential
  -> exact ci-workflows checkout
  -> checked-in organization-maintenance contract
  -> typed Python operation
  -> GitHub/Flux expected-state revalidation
  -> bounded mutation or dry-run report
```

No operation accepts an arbitrary repository URL, branch name, runner label, shell body, callback, secret name, cluster, namespace, service account, kubeconfig path, or unrestricted matrix. Project IDs resolve through `contracts/organization-maintenance.json` to reviewed repository and integration-branch tuples.

## GitHub maintenance transport

The GitHub client uses only the REST endpoints required by each operation. It enforces HTTPS, the reviewed API version, bounded pagination, a maximum page count, bounded retry attempts, `Retry-After`/exponential delay for 429 and transient 5xx responses, and bounded job-log bytes. Malformed response shapes fail closed.

Mutation safety does not depend on a second database or replay ledger. Every destructive operation has an expected-state boundary:

- artifact: immutable listing fields are re-fetched before delete;
- branch: exact merged PR and branch tip are re-fetched before ref delete;
- conformance: one deterministic report issue is updated instead of appending duplicate reports;
- runner retry: exact run snapshot and current PR/branch source are revalidated before the one rerun request.

This keeps the operating surface replay-safe without introducing a new coordination authority.

## Agent State retirement boundary

Issue #20 does not restore Agent State GitHub transport. The central contract names `agent-state-claim`, `agent-state-lifecycle`, and `agent-state-ownership` only as retired/forbidden boundaries so conformance can report stale files. There is no claim decision, ownership decision, receipt parser, lifecycle dispatcher, compatibility API, Agent State credential, or Supabase access in maintenance code.

The old Agent State branch-hygiene implementation contained cancellation-receipt logic. That logic is deliberately not centralized. The shared branch operation deletes merged heads only.

## Credential and permission model

Maintenance and Flux credentials are explicit named reusable-workflow secrets; `secrets: inherit` is forbidden.

- artifact cleanup: `actions: write`, `contents: read`;
- branch hygiene: `contents: write`, `pull-requests: read`;
- conformance: `actions: read`, `contents: read`, `issues: write`, `pull-requests: write` as fixed by the reviewed permission profile;
- runner retry: `actions: write`, `contents: read`;
- Flux reconciliation: `actions: read`, `contents: read`; cluster credentials come only from the protected Flux caller.

Dry-run is the default for every operation. The focused PR smoke is read-only and receives no maintenance/cluster credential.

## Flux policy boundary

Central Flux orchestration is intentionally data-blind. The contract records only:

- the Flux repository identity;
- allowed high-level operations (`deploy`, `restart`);
- exact repository-owned policy, allowlist, and executor paths;
- the versioned policy-adapter interface name;
- the fact that live mode requires the two named Flux credentials.

Target IDs, product IDs, source objects, Kustomizations, HelmReleases, workloads, namespaces, and live acceptance remain Flux-owned data. The exact policy source maps opaque intent to a structured plan. The plan grammar allows only typed resource references and at most 20 workloads. An injected command field, unexpected key, unsupported resource kind, symlink path, dirty source tree, or changed hash is rejected.

The policy adapter runs before credentials are materialized. Immediately before live execution, central rechecks exact Git HEAD, source cleanliness, and SHA-256 of policy, allowlist, and executor. Credentials are then placed below workflow state with private permissions, exposed through only `KUBECONFIG` / `SOPS_AGE_KEY_FILE`, and deleted in `finally` cleanup.

The executor path is Flux-owned and receives a fixed structured argument list. Central never synthesizes `kubectl`, `helm`, or Flux command strings from caller text.

## Integration boundary with public registration

The operations public API and permission records already exist as `planned`. Issue #20 intentionally does not edit shared public registries, CIW command registration, bootstrap inventories, action locks, or runner mappings because those are collision-prone integration surfaces assigned separately.

Consequently, the #20 implementation checkpoint can prove its focused issue-owned behavior independently while canonical Central may still report expected shared-registration findings such as planned APIs whose workflow files now exist, missing shared inventory/action registration, the `maintenance-control` runner mapping, or the historical `ci_workflows.flux.*` component names. Those findings are integration work for the designated shared-registration owner; they are not bypassed or hidden by issue-owned code.

## Testing without live mutation

Unit tests use synthetic GitHub responses and a temporary local Git repository containing a synthetic Flux policy adapter/executor. They prove pagination/backoff, artifact retention, expected-state failures, exact merged-branch behavior, conformance idempotency, infrastructure-only retry, structured Flux planning, source-mutation rejection, executor hash revalidation, and credential cleanup.

No issue-#20 test calls the live GitHub mutation API or a Kubernetes cluster. Real organization maintenance or Flux reconciliation requires separately authorized thin-caller execution after consumer adoption.
