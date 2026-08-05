# AGENTS.md — StreamScapeTV/ci-workflows

## Repository identity

This private repository owns reusable GitHub Actions orchestration for supported StreamScapeTV repositories, including `agent-state` and `flux`. Consumer repositories retain thin event callers, minimum permissions, bounded product configuration, and product-owned scripts, contracts, policy, and credentials.

## Integration and ownership

- `main` is the integration branch and the initial bootstrap consumer channel.
- Except for initializing an empty repository's governance file, never implement directly on `main`.
- Every change must be linked to one bounded issue, branch, and pull request.
- Before editing, inspect open issues, pull requests, and branches for overlapping ownership.
- Cross-repository consumer edits require a linked issue and must follow that repository's `AGENTS.md` and coordination rules.
- Until #32 releases the central Agent State transport for this repository, explicit repository-owner instructions authorize bounded bootstrap work. After #32, accepted Agent State start and claim receipts are required for edits and scope expansion.

## Reusable workflow contract

- Public reusable workflows live directly under `.github/workflows/reusable-*.yml`.
- Optional internal multi-job leaf workflows use `.github/workflows/internal-*.yml` and may not call another reusable workflow.
- Consumers own event triggers; reusable workflows use `workflow_call` and must not silently add scheduled, branch, or manual publication paths.
- During the bootstrap phase, consumers may call `@main` so a central fix is immediately available. Tagged and full-SHA references remain supported. After the first stable tag, migration to immutable references is recommended for privileged or production callers but is not required unless the owner changes this policy.
- Keep workflow YAML readable. Put non-trivial algorithms in named, typed, tested functions under `src/ci_workflows/` and expose them through thin composite actions or CLI adapters.
- Public inputs must be bounded and validated. Do not accept an arbitrary registry host, runner label, container engine, cluster target, secret name, callback, or unrestricted shell command.

## Domain authority boundaries

- Agent State API/database remains the sole authority for project identity, sessions, claims, resources, collision decisions, retries, replay, receipts, projection, takeover, and pull-request ownership. This repository owns reusable transport and orchestration only.
- Flux remains the sole authority for desired state, target/product allowlists, SOPS data, Kubernetes credentials, reconciliation policy, canary selection, live health, and rollback acceptance. This repository owns reusable source validation, infrastructure-product, release, maintenance, and trusted reconciliation orchestration around exact Flux-owned policy source.
- Product repositories retain toolchain pins, product commands, schemas, assertions, test selection, and deployment-specific data.

## Release safety

- `ci-workflows` itself is released by creating a Git tag pointing to an exact compatible commit. No GitHub Release object, attached archive, container image, or Helm chart is required for this repository.
- Product publication is admitted only from an exact approved Git tag and exact tagged source SHA.
- Immutable image and chart versions use the exact approved tag. Never publish `latest`.
- Historical tags build the exact historical commit without rewriting branches.
- Publication and deployment are separate. Product release workflows do not receive Kubernetes/SOPS credentials or mutate a cluster.
- Published images and charts require independent remote read-back before success.
- Replays are idempotent and conflicting immutable content fails closed.

## Runners, credentials, artifacts, and cleanup

- Central workflows select semantic runner profiles and internal implementations; consumers do not choose Docker versus Buildah or concrete runner labels.
- Use explicit named secrets, workflow-scoped authentication files, minimum permissions, and no `secrets: inherit`.
- Never execute untrusted pull-request, issue-comment, or fork source in a privileged context.
- Routine workflows retain zero Actions artifacts. Any exception must be named, bounded, justified, redacted, and tested.
- Cleanup runs under `if: always()` and fails closed on credential, container, image, chart, cache, device, simulator, or temporary-state residue.

## Validation and merge

- Validate the exact final pull-request head and current base.
- Review the complete diff, public contracts, permissions, trust boundaries, artifacts, cleanup, and every review thread.
- Queued, skipped, stale, cancelled, or partially successful checks are not green evidence.
- Merge with expected-head protection, verify `main`, update the issue with exact evidence, remove the exact merged branch, and close only when acceptance criteria pass.
