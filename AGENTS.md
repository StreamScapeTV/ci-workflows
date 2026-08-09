# AGENTS.md — StreamScapeTV/ci-workflows

## Shared organization-policy entry point

Before working in this repository, read `StreamScapeTV/organization-rules@main/AGENTS.md` after this file. It is the only shared organization-policy reference from this local entry point and owns routing to any additional central guidance. Do not add direct references here to organization-rules internal files, Agent State operating documents, Flux operating documents, or unrelated repositories for routine shared policy. Changes to shared organization policy belong in `StreamScapeTV/organization-rules` under a separate bounded change rather than being copied into this repository.

## Repository identity and integration

This private repository owns reusable GitHub Actions orchestration for supported StreamScapeTV repositories, including central source admission, runner resolution, validation, release, and Flux orchestration. Consumer repositories retain thin event callers, minimum permissions, bounded product configuration, and product-owned scripts, contracts, policy, credentials, and deployment data.

`main` is the integration branch and initial bootstrap consumer channel. This repository is released by an exact compatible Git tag; it does not require a GitHub Release object, attached archive, container image, or Helm chart for its own release.

## Agent State authority boundary

- Issue #32 is closed as superseded. Do not implement the reusable GitHub Actions lifecycle and ownership architecture formerly planned by #32.
- `StreamScapeTV/agent-state-supabase` is the sole canonical Agent State schema, migration, RPC, deployment, test, fixture, and operator authority.
- Ordinary Agent State operation follows the direct approved `agent_api.*` Supabase RPC contract routed by `StreamScapeTV/organization-rules@main`; it does not use a GitHub workflow, issue comment, lifecycle commit, runner, local client, or compatibility dispatcher.
- Issue #32 remains superseded. The temporary manual compatibility transport completed by #37 was retired through #55 after canonical production proof, active-state reconciliation, and the separate organization-rules adoption.
- Do not restore an Agent State workflow API, command workflow, runner profile, secret contract, Python transport, project mapping, or compatibility fixture in this repository. A new transport would require a separate owner-reviewed architecture change and must call the same canonical RPC authority without recreating decision logic.
- Never invent an Agent State receipt, ownership grant, schema state, deployment result, or fallback transport.

## Reusable workflow architecture

- Public reusable workflows live directly under `.github/workflows/reusable-*.yml` and expose `workflow_call` only.
- Optional internal multi-job leaf workflows live under `.github/workflows/internal-*.yml`, may not call another reusable workflow, and must preserve the reviewed shallow call graph.
- Consumers own event triggers, concurrency, environments, minimum caller permissions, and bounded product configuration. Reusable workflows must not silently add scheduled, branch, manual publication, or trusted-dispatch paths.
- During bootstrap, consumers may call `@main`; tagged and full-SHA references remain supported. Privileged and production callers should migrate to immutable references after a stable tag when required by reviewed policy.
- Keep workflow YAML as short, ordered orchestration. Put non-trivial algorithms in named, typed, tested functions under `src/ci_workflows/` and expose them through thin composite actions or CLI adapters.
- Public inputs and outputs must match the checked-in contracts and generated reference documentation. Inputs must be bounded and may not accept arbitrary shell commands, callbacks, registry hosts, runner labels, container engines, cluster targets, namespaces, service accounts, secret names, or unrestricted matrices.
- Public/internal workflow calls and composite-action calls must remain acyclic, accessible, shallow, and compatible with the supported consumer and product inventory.

## Authority boundaries

- `StreamScapeTV/agent-state-supabase` owns the canonical Supabase schema, versioned migrations, reviewed RPC contracts, fixtures, tests, deployment configuration, and transactional Agent State decisions. This repository must not duplicate that authority or maintain a competing canonical migration history.
- Flux remains the sole authority for desired state, target and product allowlists, SOPS data, Kubernetes credentials, reconciliation policy, canary selection, live health, and rollback acceptance. This repository owns only the reviewed orchestration around exact Flux-owned policy source.
- Product repositories retain toolchain pins, product commands, schemas, assertions, test selection, signing, release inputs, and deployment-specific data.

## Runner and central self-check boundary

- Semantic runner intent remains authoritative. Ordinary Python, policy, source-admission, and GitOps validation use the `portable` capability; consumers do not select concrete runner labels or hosts.
- Issue #60 is complete. While Flux #268 tracks recovery of portable ARC scheduling, the repository's central self-check has one owner-authorized temporary exception that runs only that merge gate on organization-managed macOS capacity.
- The exception does not reclassify ordinary validation as Apple work and does not change the public runner-profile contract or generated mappings.
- The central self-check verifies an exact pre-provisioned host CPython runtime before checkout and does not install or elevate a runtime on the persistent host.
- The exception grants no signing, provisioning, simulator, physical-device, notarization, store, registry, Kubernetes, production, or Agent State credential or authority.
- Remove the temporary macOS exception through a later bounded reviewed change after Flux #268 proves portable ARC recovery.

## Security, artifacts, and cleanup

- Use explicit workflow and job permissions, explicit named secrets, workflow-scoped authentication files, and no `secrets: inherit`.
- Never execute untrusted pull-request, fork, issue-comment, `pull_request_target`, `workflow_run`, or mutable source in a privileged context.
- Privileged modes require exact admitted source, exact checkout assertions, detached credential-free state where applicable, and `persist-credentials: false`.
- Central workflows select semantic runner profiles and internal implementations. Consumers do not select concrete runner labels, hosts, Docker versus Buildah, storage drivers, devices, clusters, namespaces, or service accounts.
- Routine workflows retain zero GitHub Actions artifacts. Any exception must be named, bounded, justified, redacted, registered in contract, and tested.
- Cleanup runs under `if: always()` and fails closed when credentials, authentication files, containers, images, charts, caches, generated output, device or simulator state, result bundles, or temporary workspace state remain.

## Publication and release safety

- Product publication is admitted only from an exact approved Git tag and exact tagged source SHA.
- Immutable image and chart versions use the exact approved tag. Never publish `latest`.
- Historical tags build the exact historical commit without rewriting branches.
- Publication and deployment remain separate. Product release workflows do not receive Kubernetes or SOPS credentials and do not mutate clusters.
- Published images and charts require independent remote read-back. Replays are idempotent, and conflicting immutable content fails closed.

## Validation contract

- The canonical central self-check validates the exact final pull-request head against the current base.
- Workflow/action parsing, action and tool pins, permissions, trust classes, source admission, runner profiles, call graphs, readability, public API compatibility, documentation, inventory, fixtures, discovered tests, cleanup, and artifact policy must remain green.
- A changed head invalidates older evidence. Queued, skipped, stale, cancelled, timed-out, missing, or partially successful checks are not passing evidence.
- Before integration, inspect the complete current-base diff, public contracts, permissions, trust boundaries, artifacts, cleanup behavior, generated drift, and every review thread.
- Merge only the unchanged validated head with expected-head protection, then verify `main`, record exact evidence, and remove the merged issue branch.
