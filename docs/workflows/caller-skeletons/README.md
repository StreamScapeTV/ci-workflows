# Consumer caller skeletons

These files are **copy/adapt examples**, not reusable workflows and not a runtime
service. They live under `docs/` so GitHub does not execute them in this
repository. A consumer copies the relevant example into its own
`.github/workflows/` directory and replaces each `TODO_...` value with reviewed
repository-owned configuration.

## Central reference model

During active Central development, consumers call public `ci-workflows` reusable
workflows at `@main`. This is ordinary shared-library consumption, not a
per-product bootstrap or registration phase. A consumer does not register a
Central product ID, maintain a Central commit SHA, join an allowlist, run an
initialization workflow, exchange request IDs, or perform a synchronization
handshake before it can call these workflows.

A later human-readable compatibility tag such as `@v1` can replace `@main`
without redesigning these callers. Full 40-character Central SHAs remain normal
GitHub functionality and may be useful for explicit evidence or rollback, but
they are optional unless a later reviewed policy explicitly requires them.

## Validation ownership

Each validation example keeps the GitHub-native trigger and concurrency policy in
the consumer and composes two Central library calls:

1. `reusable-resolve-source.yml@main` admits the exact current PR head or protected
   integration-branch push.
2. The technology workflow consumes the admitted `source_sha` plus bounded
   product-owned values.

The examples intentionally run push CI only on `TODO_PROTECTED_BRANCH`. Central
push admission is integration-branch-bound; normal feature work is validated by
the pull-request trigger. Do not add a feature-branch push glob unless the final
reviewed source-admission model for that repository supports it.

The consumer repository owns and must review all of these fields:

1. Replace `TODO_PROTECTED_BRANCH` with the protected integration branch.
2. Replace the `paths` list with product-owned build-affecting paths, or replace
   the entire key with `paths-ignore` when exclusions are safer. Do not use both
   on the same event. Include workflow/configuration/specification paths whenever
   they can affect validation.
3. Keep stable ref-scoped concurrency. The examples use
   `${{ github.workflow }}-${{ github.ref }}` with `cancel-in-progress: true`.
   Exact source SHA is evidence identity, not concurrency identity.
4. Keep caller permissions at the minimum shown by each job. Source admission
   needs `contents: read` and `pull-requests: read`; ordinary validators need
   `contents: read`.
5. Replace bounded technology inputs with repository-owned values. Product paths,
   schemes, Gradle plans, package profiles, checked-in scripts, and similar
   application behavior remain in the consumer repository.
6. The Helm validation API's existing `product_id` input is a caller-owned chart
   identity required by that API; it is not a Central registration key and does
   not create a per-product bootstrap step.

## Release ownership

`release.yml` is the normal human release shape: a product owner pushes a SemVer
product tag, the caller invokes `reusable-native-image-chart.yml@main`, and
Central builds and publishes the image and Helm chart under that tag. The product
tag is release-version authority.

The normal release caller does **not** expose `workflow_dispatch`, an existing-tag
recovery mode, request IDs, tag-cut helpers, or a consumer-maintained Central SHA.
Exceptional recovery mechanisms, if ever needed, are separate from ordinary
consumer adoption and are not copied into this skeleton.

## Lifecycle boundary

These examples use GitHub-native branch/path/ref structure and do not introduce a
planner service, queue, database, custom CI command language, or organization
run protocol. Native GitHub `[skip ci]` or `[ci skip]` markers may be used for
coherent intermediate checkpoints when CI is intentionally not requested. When
required pull-request validation applies, the final candidate must still be a
non-skipped exact head.

## Templates

- `apple.yml` — Apple validation caller.
- `android.yml` — Android / Gradle validation caller.
- `node.yml` — Node validation caller.
- `python.yml` — Python validation caller.
- `script.yml` — generic checked-in script validation caller.
- `helm.yml` — Helm validation caller.
- `release.yml` — normal SemVer tag-push native image + Helm publication caller.

Organization-wide maintenance such as issue-dependency synchronization remains
central. Do not copy those workflows into product repositories.
