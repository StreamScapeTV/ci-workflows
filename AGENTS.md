# AGENTS.md — StreamScapeTV/ci-workflows

## Shared organization-policy entry point

Before working in this repository, read `StreamScapeTV/organization-rules@main/AGENTS.md` after this file. It is the only shared organization-policy reference from this local entry point and owns routing to any additional central guidance. Do not add direct references here to organization-rules internal files, Agent State operating documents, Flux operating documents, or unrelated repositories for routine shared policy. Changes to shared organization policy belong in `StreamScapeTV/organization-rules` under a separate bounded change rather than being copied into this repository.

## Repository identity and integration

This private repository owns reusable GitHub Actions orchestration for supported StreamScapeTV repositories, including central source admission, runner resolution, validation, release, Flux orchestration, and temporary Agent State compatibility transport. Consumer repositories retain thin event callers, minimum permissions, bounded product configuration, and product-owned scripts, contracts, policy, credentials, and deployment data.

`main` is the integration branch and initial bootstrap consumer channel. This repository is released by an exact compatible Git tag; it does not require a GitHub Release object, attached archive, container image, or Helm chart for its own release.

## Agent State transition boundary

- Issue #32 is closed as superseded. Do not implement the reusable GitHub Actions lifecycle and ownership architecture formerly planned by #32.
- `StreamScapeTV/agent-state-supabase#1` is the canonical Supabase Agent State implementation tracker. In that repository, #2 owns repository and deployment bootstrap, #3 owns the transactional core and canonical migration/RPC reconciliation, #4 owns structured multi-work, #5 owns exact-head review and administration, and #6 owns canonical live proof, active-state reconciliation, final cutover, and legacy retirement.
- This repository's open #51–#55 issues are transition mirrors only. They preserve coordination and evidence while work is re-homed; they are not the canonical Supabase migration repository or authoritative migration history.
- `ci-workflows` owns reusable workflow orchestration and the protected manual compatibility workflow completed by #37. It does not own the authoritative Supabase schema, canonical migration/deployment history, or Agent State decision engine.
- Keep #37 available until `StreamScapeTV/agent-state-supabase#6` proves the canonical live connector path, reconciles active state, completes separate reviewed organization-rules adoption, and confirms that no legacy consumer remains.
- The canonical `agent-state-supabase` repository may contain bootstrap work, but it is not yet the accepted canonical core migration/deployment source. Repository existence or bootstrap progress alone does not establish schema or operating-path readiness.
- Provisional directly applied `agent_private` / `agent_api` schema, RPC, or migration-ledger state from transition work is not accepted canonical deployment evidence until reconciled to the reviewed `agent-state-supabase` migration history and proven by that repository.
- Direct Supabase Agent State operation is forbidden for ordinary agents until `agent-state-supabase#6` completes canonical live proof and the separate organization-rules cutover makes that path authoritative. Provisional RPCs are not an ordinary coordination surface.
- During this transition, follow the execution path routed by the current organization-rules entry point unless the repository owner gives explicit bounded bootstrap authorization. Never invent an Agent State receipt, ownership grant, schema state, deployment result, or fallback transport.
- `ci-workflows` changes must not bundle organization-rules adoption. That remains a separate reviewed change after canonical live proof.

## Reusable workflow architecture

- Public reusable workflows live directly under `.github/workflows/reusable-*.yml` and expose `workflow_call` only.
- Optional internal multi-job leaf workflows live under `.github/workflows/internal-*.yml`, may not call another reusable workflow, and must preserve the reviewed shallow call graph.
- Consumers own event triggers, concurrency, environments, minimum caller permissions, and bounded product configuration. Reusable workflows must not silently add scheduled, branch, manual publication, or trusted-dispatch paths.
- During bootstrap, consumers may call `@main`; tagged and full-SHA references remain supported. Privileged and production callers should migrate to immutable references after a stable tag when required by reviewed policy.
- Keep workflow YAML as short, ordered orchestration. Put non-trivial algorithms in named, typed, tested functions under `src/ci_workflows/` and expose them through thin composite actions or CLI adapters.
- Public inputs and outputs must match the checked-in contracts and generated reference documentation. Inputs must be bounded and may not accept arbitrary shell commands, callbacks, registry hosts, runner labels, container engines, cluster targets, namespaces, service accounts, secret names, or unrestricted matrices.
- Public/internal workflow calls and composite-action calls must remain acyclic, accessible, shallow, and compatible with the supported consumer and product inventory.

## Authority boundaries

- `StreamScapeTV/agent-state-supabase` owns the canonical Supabase schema, versioned migrations, reviewed RPC contracts, fixtures, tests, deployment configuration, and transactional Agent State decisions once those changes are accepted there. This repository must not duplicate that authority or maintain a competing canonical migration history.
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
