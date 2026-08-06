# AGENTS.md — StreamScapeTV/ci-workflows

## Shared organization-policy entry point

Before working in this repository, read `StreamScapeTV/organization-rules@main/AGENTS.md` after this file. It is the only shared organization-policy reference from this local entry point and owns routing to any additional central guidance. Do not add direct references here to organization-rules internal files, Agent State operating documents, Flux operating documents, or unrelated repositories for routine shared policy. Changes to shared organization policy belong in `StreamScapeTV/organization-rules` under a separate bounded change rather than being copied into this repository.

## Repository identity and integration

This private repository owns reusable GitHub Actions orchestration for supported StreamScapeTV repositories, including central source admission, runner resolution, validation, release, Flux orchestration, and temporary Agent State compatibility transport. Consumer repositories retain thin event callers, minimum permissions, bounded product configuration, and product-owned scripts, contracts, policy, credentials, and deployment data.

`main` is the integration branch and initial bootstrap consumer channel. This repository is released by an exact compatible Git tag; it does not require a GitHub Release object, attached archive, container image, or Helm chart for its own release.

## Agent State transition boundary

- Issue #32 is closed as superseded. Do not implement the reusable GitHub Actions lifecycle and ownership architecture formerly planned by #32.
- `StreamScapeTV/agent-state-supabase#1` is the canonical Supabase Agent State implementation tracker. In that repository, #2 owns repository and deployment bootstrap, #3 owns the transactional core and RPC, #4 owns structured multi-work, #5 owns exact-head review and administration, and #6 owns live proof, active-state reconciliation, final cutover, and legacy retirement.
- This repository's open #51–#55 issues are transition mirrors only. They preserve coordination and evidence while work is re-homed; they are not the eventual canonical Supabase migration repository or authoritative migration history.
- `ci-workflows` continues to own reusable workflow orchestration and the protected manual compatibility workflow completed by #37. It does not own the authoritative Supabase schema, migration history, or Agent State decision engine.
- Keep #37 available until `StreamScapeTV/agent-state-supabase#6` proves the live connector path, reconciles active state, completes separate reviewed organization-rules adoption, and confirms that no legacy consumer remains.
- Supabase is the selected target architecture, not the required organization operating path. `agent-state-supabase` is currently empty, its Supabase GitHub integration is intentionally not connected, and no Agent State schema is live. Do not instruct ordinary agents to use Supabase before the final cutover and subsequent organization-rules update.
- During this transition, follow the execution path routed by the current organization-rules entry point unless the repository owner gives explicit bounded bootstrap authorization. Never invent an Agent State receipt, ownership grant, schema state, deployment result, or fallback transport.
- This issue and repository do not modify `StreamScapeTV/organization-rules`; that adoption must remain a separate reviewed change after live proof.

## Reusable workflow architecture

- Public reusable workflows live directly under `.github/workflows/reusable-*.yml` and expose `workflow_call` only.
- Optional internal multi-job leaf workflows live under `.github/workflows/internal-*.yml`, may not call another reusable workflow, and must preserve the reviewed shallow call graph.
- Consumers own event triggers, concurrency, environments, minimum caller permissions, and bounded product configuration. Reusable workflows must not silently add scheduled, branch, manual publication, or trusted-dispatch paths.
- During bootstrap, consumers may call `@main`; tagged and full-SHA references remain supported. Privileged and production callers should migrate to immutable references after a stable tag when required by reviewed policy.
- Keep workflow YAML as short, ordered orchestration. Put non-trivial algorithms in named, typed, tested functions under `src/ci_workflows/` and expose them through thin composite actions or CLI adapters.
- Public inputs and outputs must match the checked-in contracts and generated reference documentation. Inputs must be bounded and may not accept arbitrary shell commands, callbacks, registry hosts, runner labels, container engines, cluster targets, namespaces, service accounts, secret names, or unrestricted matrices.
- Public/internal workflow calls and composite-action calls must remain acyclic, accessible, shallow, and compatible with the supported consumer and product inventory.

## Authority boundaries

- `StreamScapeTV/agent-state-supabase` owns the future canonical Supabase schema, versioned migrations, reviewed RPC contracts, fixtures, tests, deployment configuration, and transactional Agent State decisions. This repository must not duplicate that authority or maintain a competing canonical migration history.
- Flux remains the sole authority for desired state, target and product allowlists, SOPS data, Kubernetes credentials, reconciliation policy, canary selection, live health, and rollback acceptance. This repository owns only the reviewed orchestration around exact Flux-owned policy source.
- Product repositories retain toolchain pins, product commands, schemas, assertions, test selection, signing, release inputs, and deployment-specific data.

## Security, runners, artifacts, and cleanup

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
