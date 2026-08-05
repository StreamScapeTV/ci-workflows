# AGENTS.md — StreamScapeTV/ci-workflows

## Repository identity

This private repository owns reusable GitHub Actions orchestration for supported StreamScapeTV repositories. Consumer repositories retain thin event callers, minimum permissions, bounded product configuration, and product-owned scripts or policy.

## Integration and ownership

- `main` is the integration branch.
- Except for initializing an empty repository's governance file, never implement directly on `main`.
- Every change must be linked to an issue, use one bounded branch and pull request, and preserve a clean exact diff.
- Before editing, inspect open issues, pull requests, and branches for overlapping ownership.
- Cross-repository consumer edits require a linked issue in the consumer and must follow that repository's `AGENTS.md` and ownership rules.

## Reusable workflow contract

- Public reusable workflows live directly under `.github/workflows/reusable-*.yml`.
- Consumers own triggers; reusable workflows use `workflow_call` and must not silently add scheduled, branch, or manual publication paths.
- Privileged callers must pin the shared workflow to an immutable full commit SHA.
- Keep workflow YAML readable. Put non-trivial reusable algorithms in named scripts or actions when the repository has that foundation.
- Public inputs must be bounded and validated. Do not accept an arbitrary registry host, runner label, container engine, cluster target, secret name, or unrestricted shell command.

## Release safety

- Publication is admitted only from an exact Git tag and exact tagged source SHA.
- Immutable image and chart versions must use the exact approved tag. Never publish `latest`.
- Historical tags must build the exact historical commit without rewriting branches.
- Publication and deployment are separate. Release workflows must not receive Kubernetes/SOPS credentials or mutate a cluster.
- Published images and charts require independent remote read-back before success.
- Replays must be idempotent and conflicting immutable content must fail closed.

## Runners, credentials, artifacts, and cleanup

- Central workflows select the internal runner profile and implementation; consumer callers do not choose Docker versus Buildah or concrete runner labels.
- Preserve the daemonless product invariant: no Docker daemon, Buildx, DinD, Docker socket, Docker Desktop, or hosted fallback for OCI publication.
- Use explicit named secrets, workflow-scoped authentication files, minimum permissions, and no `secrets: inherit`.
- Routine workflows retain zero Actions artifacts.
- Cleanup runs under `if: always()` and fails closed on credential, container, image, chart, cache, or temporary-state residue.

## Validation and merge

- Validate the exact final PR head and current base.
- Review the complete diff and resolve every review thread.
- Queued, skipped, stale, cancelled, or partially successful checks are not green evidence.
- Merge with expected-head protection, verify the protected-main commit, then remove the exact merged branch and close the issue with durable evidence.
