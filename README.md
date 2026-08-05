# StreamScapeTV CI Workflows

Reusable GitHub Actions orchestration for the StreamScapeTV organization.

## Scope

This repository centralizes exact-source handling, runner selection, shared validation, Agent State API transport, OCI and Helm pipelines, tag releases, Flux infrastructure assets, trusted Flux orchestration, maintenance, and conformance. It supports:

- `iptv-backend`
- `StreamScapeWeb`
- `iptv-android`
- `iptv-apple`
- `streamscape-media`
- `directus-front`
- `finance-hub`
- `agent-state`
- `flux`
- `ci-workflows`

Consumer repositories keep triggers, minimum permissions, concurrency and environments, bounded identifiers, and product-owned commands/contracts. Agent State remains the claim/session decision authority. Flux remains the desired-state, allowlist, credential, and live-rollout authority.

## Reuse layers

1. `.github/workflows/reusable-*.yml` — public reusable workflows.
2. `.github/workflows/internal-*.yml` — optional non-nesting internal leaf workflows.
3. `actions/<name>/action.yml` — thin bounded composite actions.
4. `src/ci_workflows/` — named typed functions for non-trivial behavior.

The bootstrap stage intentionally publishes no consumer-facing reusable workflow until the inventory, API contract, and security harness are complete.

## Consumer channel

During the first rollout, consumers may follow `main` so a central fix becomes available immediately:

```yaml
jobs:
  validate:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-example.yml@main
    with:
      profile: example
```

A Git tag can be used instead when a stable point is preferred:

```yaml
uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-example.yml@v1.0.0
```

`ci-workflows` releases are Git tags only. This repository does not need a GitHub Release object, ZIP attachment, image, chart, or other release artifact.

## Private repository access

Organization settings must allow supported private repositories to call workflows and actions from this private repository. See [`docs/consumers/access.md`](docs/consumers/access.md).

## Security and artifact defaults

- exact source and credential-free checkout;
- least-privilege permissions and explicit named secrets;
- no privileged execution of untrusted source;
- no consumer-selected runner label or container engine;
- zero routine Actions artifacts;
- unconditional, residue-aware cleanup;
- separate trust profiles for validation, Agent State transport, publication, Flux reconciliation, and maintenance.

See [`docs/architecture/ADR-0001-reuse-layers.md`](docs/architecture/ADR-0001-reuse-layers.md) and [`docs/architecture/security-and-artifacts.md`](docs/architecture/security-and-artifacts.md).
