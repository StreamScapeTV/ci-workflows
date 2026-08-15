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

1. `.github/workflows/reusable-*.yml` — public reusable workflows.
2. `.github/workflows/internal-*.yml` — optional non-nesting internal leaf workflows.
3. `actions/<name>/action.yml` — thin bounded composite actions.
4. `src/ci_workflows/` — named typed functions for non-trivial behavior.

`contracts/public-workflows.json` is the machine-readable authority for the current public API inventory and each API's implemented, planned, or compatibility status. Do not infer API availability from README prose alone. The exact-tag image/chart workflow introduced by #34 remains a `deprecated-bootstrap-exception` compatibility path while its replacement Helm/release APIs are still planned; it is not the sole public workflow and it does not block already implemented source, validation, or OCI APIs from being consumed.

## Consumer channel

During the current active-development/bootstrap phase, **all consumer repositories should reference shared `ci-workflows` workflows at `@main`**, including trusted publication and release callers. This is deliberate: a fix merged into central `main` becomes available to every consumer without a separate mass-repin change across the organization.

Using `@main` selects the current reviewed central workflow implementation; it does **not** weaken the exact product/source authority enforced by that workflow. Trusted publication still requires the exact admitted caller source, exact release tag/source tuple, bounded checked-in product identity, explicit named credentials, independent remote read-back, cleanup, and the existing no-`latest`/no-deployment boundaries.

Full 40-character `ci-workflows` SHAs and published stable tags remain supported reference forms, but they are not the required/default consumer channel during this rapid-development phase. A later explicit stable-release/cutover decision may switch consumers to an immutable channel; until then, do not mass-repin consumers away from `@main` merely because a new central fix lands.

Existing genuine tag-push callers retain their product trigger and bounded inputs:

```yaml
name: Publish tagged Backend image and chart

on:
  push:
    tags:
      - "*"

permissions:
  actions: read
  contents: read

jobs:
  release:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-tag-image-chart.yml@main
    with:
      image_name: iptv-backend
      chart_name: iptv-backend
      chart_path: charts/iptv-backend
      dockerfile_path: Dockerfile
      build_context: .
    secrets:
      registry_username: ${{ secrets.FORGEJO_REGISTRY_USERNAME }}
      registry_token: ${{ secrets.FORGEJO_REGISTRY_TOKEN }}
```

When the exact tag already exists and no synthetic tag-push event can be relied on, a reviewed same-repository caller may expose a bounded manual tuple and select `existing-tag` explicitly:

```yaml
name: Publish an existing exact Backend tag

on:
  workflow_dispatch:
    inputs:
      release_version:
        description: Existing canonical SemVer tag, for example 1.0.4
        required: true
        type: string
      release_source_sha:
        description: Exact lowercase commit currently named by that tag
        required: true
        type: string

permissions:
  actions: read
  contents: read

jobs:
  release:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-tag-image-chart.yml@main
    with:
      release_mode: existing-tag
      release_version: ${{ inputs.release_version }}
      release_source_sha: ${{ inputs.release_source_sha }}
      image_name: iptv-backend
      chart_name: iptv-backend
      chart_path: charts/iptv-backend
      dockerfile_path: Dockerfile
      build_context: .
    secrets:
      registry_username: ${{ secrets.FORGEJO_REGISTRY_USERNAME }}
      registry_token: ${{ secrets.FORGEJO_REGISTRY_TOKEN }}
```

The explicit caller must execute from the current default branch of the same non-fork repository under a write-authorized actor. The central workflow resolves `refs/tags/<release_version>` through GitHub’s read-only API, supports lightweight and bounded annotated tags, requires the final commit to equal `release_source_sha`, checks out only that detached commit, and revalidates the tag immediately before publication. A branch or caller ref is never accepted as release authority.

One incident-specific recovery exception is available through the optional `image_recovery_authority` string. It is not a manual-dispatch field or a caller-selected set of hashes: the trusted Backend caller hard-codes the complete reviewed JSON authority from central issue #92. Central admission requires its exact schema, repository, version, source, historical publisher run and attempt, historical caller and central revisions, remote image digest, and exactly the `linux/amd64` and `linux/arm64` config digests. It then verifies the immutable historical run, jobs, steps, logs, zero artifacts, and current default-branch caller before publication credentials are used. Empty remains the default for every ordinary release.

After an approved stable `ci-workflows` release tag actually exists, a later explicit cutover may authorize consumers to follow that stable channel instead of `@main`. The following is illustrative only until that decision is made:

```yaml
uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-tag-image-chart.yml@v1.0.0
```

`ci-workflows` releases are Git tags only. This repository does not need a GitHub Release object, ZIP attachment, image, chart, or other release artifact.

## Exact-tag image and Helm publication

`.github/workflows/reusable-tag-image-chart.yml` is a `workflow_call`-only compatibility/bootstrap product release primitive. Its default `tag-push` mode preserves genuine tag-push behavior. Its explicit `existing-tag` mode accepts only the complete exact version/source tuple from the trusted caller class described above. Both ordinary modes produce the same immutable version and source outputs and enter identical daemonless image and chart publication stages. The fixed recovery authority is a narrower `existing-tag` sub-mode: it performs authenticated read-only verification of the already-published image before and after chart handling and cannot build, copy, push, delete, or retag an image.

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
