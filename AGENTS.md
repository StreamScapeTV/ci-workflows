# AGENTS.md — StreamScapeTV/ci-workflows

## Shared organization-policy entry point

Before working in this repository, read `StreamScapeTV/organization-rules@main/AGENTS.md` after this file. It is the only shared organization-policy reference from this local entry point and owns routing to any additional central guidance. Do not add direct references here to organization-rules internal files, Agent State operating documents, Flux operating documents, or unrelated repositories for routine shared policy.

## Repository identity and integration

This private repository owns reusable GitHub Actions orchestration for supported StreamScapeTV repositories, including central source admission, runner resolution, validation, release, Flux orchestration, and temporary Agent State transport. Consumer repositories retain thin event callers, minimum permissions, bounded product configuration, and product-owned scripts, contracts, policy, credentials, and deployment data.

`main` is the integration branch and initial bootstrap consumer channel. This repository is released by an exact compatible Git tag; it does not require a GitHub Release object, attached archive, container image, or Helm chart for its own release.

## Agent State transition boundary

- Issue #32 is closed as superseded by the Supabase replacement program in #51–#55. Do not implement the reusable GitHub Actions lifecycle and ownership architecture formerly planned by #32.
- The protected manual command workflow completed by #37 is explicitly temporary. Keep it available only until #55 live-proves the direct Supabase path, completes the separate organization-rules cutover, and confirms that no active consumer depends on the legacy transport.
- Supabase is the selected target architecture, but it is not yet the organization operating path. Do not instruct agents to use Supabase for ordinary coordination before #52–#55 are live-proven and `StreamScapeTV/organization-rules` is updated through its own reviewed change.
- During this transition, follow the execution path routed by the current organization-rules entry point unless the repository owner gives explicit bounded bootstrap authorization. Never invent an Agent State receipt, ownership grant, or fallback transport.
- This repository owns orchestration and transport surfaces only. It does not make project identity, session, claim, collision, replay, receipt, review, takeover, or ownership decisions locally.

## Reusable workflow architecture

- Public reusable workflows live directly under `.github/workflows/reusable-*.yml` and expose `workflow_call` only.
- Optional internal multi-job leaf workflows live under `.github/workflows/internal-*.yml`, may not call another reusable workflow, and must preserve the reviewed shallow call graph.
- Consumers own event triggers, concurrency, environments, minimum caller permissions, and bounded product configuration. Reusable workflows must not silently add scheduled, branch, manual publication, or trusted-dispatch paths.
- During bootstrap, consumers may call `@main`; tagged and full-SHA references remain supported. Privileged and production callers should migrate to immutable references after a stable tag when required by reviewed policy.
- Keep workflow YAML as short, ordered orchestration. Put non-trivial algorithms in named, typed, tested functions under `src/ci_workflows/` and expose them through thin composite actions or CLI adapters.
- Public inputs and outputs must match the checked-in contracts and generated reference documentation. Inputs must be bounded and may not accept arbitrary shell commands, callbacks, registry hosts, runner labels, container engines, cluster targets, namespaces, service accounts, secret names, or unrestricted matrices.
- Public/internal workflow calls and composite-action calls must remain acyclic, accessible, shallow, and compatible with the supported consumer and product inventory.

## Authority boundaries

- Agent State coordination authority remains external to workflow YAML and local helper code. The #51–#55 program defines the replacement decision engine and cutover; this repository must not duplicate those decisions.
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
