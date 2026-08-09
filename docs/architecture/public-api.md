# Public workflow API architecture

The public workflow registry is the design authority for every organization-facing reusable workflow before implementation. It separates caller-owned triggers and product policy from reusable orchestration, infrastructure selection, and privileged transport.

## Shallow call graph

The normal execution shape is:

```text
consumer caller → public reusable workflow → named function
```

A public workflow may call one reviewed internal reusable-workflow leaf when multi-job orchestration requires it. That internal leaf may not call another reusable workflow. The maximum supported total reusable-workflow depth is therefore two; ordinary APIs remain at depth one.

Public workflow YAML stays readable. Non-trivial algorithms live in named, typed, tested functions under `src/ci_workflows/` and are exposed through thin adapters. Product-specific commands remain repository-owned hooks selected by bounded profiles or checked-in relative paths; callers never submit arbitrary shell.

## Reference channels

During the initial organization migration, protected `StreamScapeTV/ci-workflows@main` is the preferred channel for every public workflow family. A central correction therefore becomes available to migrated repositories without editing every caller. Full commit SHAs and immutable SemVer tags remain supported at all times and can be selected whenever a consumer needs a fixed or rollback reference.

A moving workflow reference never weakens source admission. Product source, release tags, pull-request heads, device commands, and Flux policy are still resolved and validated as exact inputs by the called workflow. Changes to the public API contract remain reviewable in this repository before they reach `main`.

## Caller and called-workflow boundary

The consumer repository owns:

- event triggers, path filters, concurrency, and environment protection;
- the stable required-check surface;
- exact product, project, release, validation, and policy identifiers;
- minimum caller permissions and explicit named secrets;
- product commands, tests, toolchain pins, schemas, fixtures, signing policy, and deployment-specific data.

The called workflow owns:

- exact-source admission and checkout behavior;
- semantic runner intent and internal implementation selection;
- reusable tool setup, orchestration, evidence, cleanup, and redaction;
- bounded OCI/Helm validation, publication, and independent read-back;
- reusable device, maintenance, and Flux transport around the owning system's authority.

The called workflow cannot elevate the caller's `GITHUB_TOKEN`. Each API binds to one permission profile, treats unspecified permissions as `none`, accepts only declared named secrets, and forbids `secrets: inherit`.

## Trust classes

### Source admission

Source resolution reads GitHub metadata and returns one admitted exact SHA. It does not execute caller source and has no product or infrastructure credential.

### Read-only validation

Validation may execute exact admitted caller source with read-only permissions. A private dependency token is optional only where the inventory approves it and is unavailable to untrusted forks. Validation publishes zero routine Actions artifacts.

### Agent State exclusion

Agent State operation is deliberately outside the public workflow registry. `StreamScapeTV/agent-state-supabase` owns project identity, work, claims, replay, receipts, reviews, orchestration, fencing, and readiness through approved direct `agent_api.*` RPCs. This repository exposes no Agent State workflow, runner, secret, or transport fallback.

### Physical-device validation

Device work is explicitly authorized, exact-source, single-resource, time-bounded, and residue-checked. The public API chooses a semantic device capability; callers do not choose hosts or runner labels. Test-environment credentials are the only product-source-visible secret class and are restricted to the approved device command profile.

### Trusted publication

Publication runs only for an exact admitted release tag and source SHA. Image and chart products are selected from the product contract; callers do not choose a registry host, container engine, storage driver, or registry command. Publication and deployment remain separate, and immutable products require independent remote read-back.

The versioned bootstrap image/chart API supports two authority modes with one downstream publication contract. `tag-push` remains the default and derives the immutable version and source from a genuine stable-tag push. `existing-tag` requires the complete explicit `release_mode`, `release_version`, and `release_source_sha` tuple from a same-repository `workflow_dispatch` caller whose workflow is running from the current default branch under a write-authorized actor. Pull requests, forks, issue comments, branches, arbitrary refs, and partial or mixed tuples are rejected before source checkout.

Both modes resolve `refs/tags/<release_version>` through the caller repository's read-only GitHub API. Lightweight tags resolve directly to a commit; annotated tags are dereferenced through a bounded, cycle-checked chain that rejects unsupported object types and missing objects. The final commit must equal the exact lowercase source SHA, and the initially resolved tag object and commit are revalidated before checkout and immediately before registry authentication. The workflow then checks out only the detached exact commit and enters the same Buildah, OCI image, Helm chart, replay, read-back, output, cleanup, and zero-artifact stages for either mode.

### Flux-authorized reconciliation

Flux remains the sole authority for desired state, target and product allowlists, SOPS/Kubernetes credentials, live reconciliation, health, canary selection, rollback, and incident acceptance. The central wrapper executes exact protected Flux source and Flux-owned policy and never accepts arbitrary cluster, namespace, kubeconfig path, object, or command input.

### Trusted maintenance

Maintenance APIs are operation-specific, protected-main-or-immutable-reference, report-first, dry-run-by-default, and fail closed. Artifact cleanup, merged-branch hygiene, organization conformance, and runner-infrastructure retry use separate permission profiles rather than one broad maintenance credential.

## Inputs, outputs, and forbidden fields

Public inputs are typed in `contracts/public-workflow-types.json`. Every workflow selects only cataloged fields and records required/default behavior. Public callers cannot supply:

- concrete runner labels or `runs-on` expressions;
- Docker, Buildah, or other container-engine commands;
- registry hosts or commands;
- secret names;
- kubeconfig paths, clusters, or namespaces;
- arbitrary commands, shells, callbacks, or private API locations.

Outputs are bounded catalog entries suitable for job outputs and concise summaries. Evidence is redacted and structured. Routine logs, result bundles, build products, archives, images, charts, database dumps, private media, and environment snapshots are not retained as evidence.

## Artifacts and cleanup

The default is zero routine Actions artifacts. A named exception must define the producer, consumer, exact content, privacy classification, integrity check, retention, and cleanup behavior. Cleanup runs for success, failure, cancellation, and timeout and fails closed on credential, authentication-file, container, image, chart, cache, device, simulator, or temporary-state residue.

## Compatibility and implementation order

The registry is reviewed before implementation. Compatible additions, conditional changes, and breaking changes are classified by `scripts/ci/public_api_contract.py`. Breaking changes require an explicit acknowledgement with a migration issue and effective version. Implementations, callers, required checks, the protected `main` channel, and immutable release manifests must remain synchronized with the approved records.
