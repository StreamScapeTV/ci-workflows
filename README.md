# StreamScapeTV CI Workflows

Reusable GitHub Actions orchestration for the StreamScapeTV organization.

## Scope

This repository centralizes exact-source handling, runner selection, shared validation, OCI and Helm pipelines, tag releases, Flux infrastructure assets, trusted Flux orchestration, maintenance, and conformance. It supports:

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

Consumer repositories keep triggers, minimum permissions, concurrency and environments, bounded identifiers, and product-owned commands/contracts. `StreamScapeTV/agent-state-supabase` owns Agent State decisions through approved direct RPCs; this repository owns no Agent State transport. Flux remains the desired-state, allowlist, credential, and live-rollout authority.

## Reuse layers

1. `src/ci_workflows/` — named typed Python functions implement reusable behavior.
2. `actions/<name>/action.yml` — thin bounded adapters over those functions.
3. `.github/workflows/reusable-*.yml` — public reusable orchestration.
4. `.github/workflows/internal-*.yml` — optional non-nesting internal orchestration leaves.

Workflow YAML is orchestration, not the implementation layer. Central names describe technologies or capabilities; product paths, tasks, scripts, and options remain bounded inputs owned by the consumer. Shared functions take explicit working directories, environment/input data and named secret environment variables, and return structured results that adapters serialize for GitHub Actions.

The product-neutral primitives in `src/ci_workflows/runtime_primitives.py` provide the common runtime substrate: structured subprocess execution, non-secret input normalization, named secret lookup, bounded temporary workspaces and cleanup, exact repository/SHA checkout, deterministic JSON/GitHub-output serialization, and idempotent temporary/auth-state finalization. Domain modules compose these primitives; CLI/public-workflow registration remains a separate wiring concern.

Ordinary validation does not require a provenance ledger, canary/rollback framework, immutable-digest proof, or remote read-back layer merely to run a product-owned command. Publication workflows may retain stronger publication-specific checks where their reviewed contract requires them. Local-runner caching/storage stays Flux-owned; shared workflows do not add GitHub Actions cache or manage PV/shared-volume infrastructure.

`contracts/public-workflows.json` is the machine-readable authority for the current public API inventory and each API's implemented, planned, or compatibility status. Do not infer API availability from README prose alone. The exact-tag image/chart workflow introduced by #34 remains a `deprecated-bootstrap-exception` compatibility path while its replacement Helm/release APIs are still planned; it is not the sole public workflow and it does not block already implemented source, validation, or OCI APIs from being consumed.

## Consumer channel

During active Central development, **consumer repositories call shared `ci-workflows` workflows at `@main`**. This is ordinary shared-library consumption: there is no per-product Central bootstrap, registration, product-ID enrollment, initialization pipeline, request-ID exchange, synchronization handshake, or consumer-maintained Central SHA required before a repository can use a public reusable workflow.

A fix merged into Central `main` is therefore available to consumers without a mass repin. Using `@main` selects the current reviewed Central workflow implementation; it does **not** weaken the exact product/source authority enforced by the called workflow.

Full 40-character `ci-workflows` SHAs and published compatibility tags remain supported GitHub reference forms, but full SHAs are optional for ordinary consumers unless a later reviewed policy explicitly requires them. A future human-readable compatibility tag such as `@v1` can replace `@main` without redesigning caller workflows.

A thin validation caller resolves the exact product source and then invokes the technology API directly:

```yaml
jobs:
  source:
    permissions:
      contents: read
      pull-requests: read
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-resolve-source.yml@main
    with:
      source_mode: auto
      expected_branch: develop

  validate:
    needs: source
    permissions:
      contents: read
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-node.yml@main
    with:
      admitted_sha: ${{ needs.source.outputs.source_sha }}
      validation_profile: locked-node
      version_file: .node-version
      working_directory: .
      install_profile: npm-ci
      command_profile: project-quality
```

Consumer repositories own the pull-request/push triggers, path filters, stable ref-scoped concurrency, project paths and bounded product configuration around that call. Copy/adapt examples live under [`docs/workflows/caller-skeletons/`](docs/workflows/caller-skeletons/).

The normal image + Helm release caller is the product tag-push path. The product tag is release-version authority; no consumer-maintained Central SHA or existing-tag recovery ceremony is part of ordinary release adoption:

```yaml
name: Publish tagged image and chart

on:
  push:
    tags:
      - "*.*.*"

jobs:
  release:
    permissions:
      contents: read
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-native-image-chart.yml@main
    with:
      image_name: my-app
      chart_name: my-app
      chart_path: charts/my-app
      dockerfile_path: Dockerfile
      build_context: .
    secrets:
      registry_username: ${{ secrets.FORGEJO_REGISTRY_USERNAME }}
      registry_token: ${{ secrets.FORGEJO_REGISTRY_TOKEN }}
```

The older exact-tag compatibility workflow retains separately reviewed recovery capabilities for exceptional cases. Those are not the normal consumer skeleton and should not be copied into ordinary tag-push callers.

After an approved stable `ci-workflows` compatibility tag exists and a later policy selects that channel, a consumer can substitute it directly, for example:

```yaml
uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-node.yml@v1
```

`ci-workflows` releases are Git tags only. This repository does not need a GitHub Release object, ZIP attachment, image, chart, or other release artifact.

## Exact-tag image and Helm publication

`.github/workflows/reusable-tag-image-chart.yml` is a `workflow_call`-only legacy compatibility release primitive. Its default `tag-push` mode preserves genuine tag-push behavior. Its explicit `existing-tag` mode accepts only the complete exact version/source tuple from its reviewed trusted caller class. The fixed recovery authority is a narrower exceptional mode; none of these compatibility paths defines the normal consumer adoption skeleton above.

For reference only, a separately reviewed legacy compatibility caller can still select an already-existing exact tag through that older surface. This is **not** the normal release skeleton and is not an adoption/bootstrap requirement:

```yaml
jobs:
  compatibility_release:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-tag-image-chart.yml@<compatibility-ref>
    with:
      release_mode: existing-tag
      release_version: TODO_EXISTING_TAG
      release_source_sha: TODO_EXACT_TAGGED_SHA
      image_name: TODO_IMAGE_NAME
      chart_name: TODO_CHART_NAME
      chart_path: TODO_CHART_PATH
      dockerfile_path: TODO_DOCKERFILE_PATH
      build_context: TODO_BUILD_CONTEXT
    secrets:
      registry_username: ${{ secrets.FORGEJO_REGISTRY_USERNAME }}
      registry_token: ${{ secrets.FORGEJO_REGISTRY_TOKEN }}
```

The workflow uses the exact validated version for a multi-platform OCI image and Helm OCI chart, independently reads both products back, retains zero Actions artifacts, and performs no deployment. Authenticated image and chart tag listings fail closed. A present chart is pulled and compared with the exact local package; confirmed absence permits one push followed by tag, package checksum, metadata, dependency, and OCI layer verification. It does not publish `latest`, create a GitHub Release, accept a branch as release identity, update production values, restart workloads, or access a cluster. The caller passes only bounded product inputs and explicit named registry secrets; broad secret inheritance is prohibited.

Accepted product tags are `MAJOR.MINOR.PATCH` with an optional OCI-safe prerelease suffix such as `1.2.3-rc.1`. A tag can point to any approved historical commit:

```bash
git tag 1.2.3 <commit>
git push origin 1.2.3
```

Publication remains separate from Flux selection and deployment.

## Private repository access

Organization settings must allow supported private repositories to call workflows and actions from this private repository. See [`docs/consumers/access.md`](docs/consumers/access.md).

## Security and artifact defaults

- exact source and credential-free checkout;
- least-privilege permissions and explicit named secrets;
- no privileged execution of untrusted source;
- no consumer-selected runner label or container engine;
- zero routine Actions artifacts;
- unconditional, residue-aware cleanup;
- separate trust profiles for validation, publication, Flux reconciliation, and maintenance.

See [`docs/architecture/ADR-0001-reuse-layers.md`](docs/architecture/ADR-0001-reuse-layers.md) and [`docs/architecture/security-and-artifacts.md`](docs/architecture/security-and-artifacts.md).
