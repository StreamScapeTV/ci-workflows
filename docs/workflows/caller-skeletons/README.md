# Consumer caller skeletons

These files are **copy/adapt scaffolding**, not reusable workflows and not a runtime
service. They live under `docs/` so GitHub does not execute them in this
repository. A consumer copies the relevant skeleton into its own
`.github/workflows/` directory and replaces every `TODO_...` token.

## Ownership checklist

The consumer repository owns and must review all of these fields:

1. Replace `TODO_PROTECTED_BRANCH` with each protected integration target used by
   that repository.
2. Keep, replace, or remove `TODO_OPTIONAL_FEATURE_BRANCH_GLOB`. A non-skipped
   feature-branch push may intentionally request CI; remove that branch entry when
   feature-push CI is not wanted.
3. Replace the `paths` list with product-owned build-affecting paths, or replace
   the entire key with `paths-ignore` when exclusions are safer. Do not use both
   on the same event. Do not assume dot-directories, documentation, specifications,
   assets, workflow files, or configuration are universally ignorable: include
   anything that can affect that product's validation.
4. Keep stable ref-scoped concurrency. The examples use
   `${{ github.workflow }}-${{ github.ref }}` with `cancel-in-progress: true`.
   Exact source SHA is evidence identity, not concurrency identity.
5. Confirm the minimum caller permissions. Validation skeletons start with
   `contents: read`; add only permissions required by the final reviewed reusable
   API.
6. After `ci-workflows` #283, #322, and #323 are integrated, replace the
   `TODO_REUSABLE_*_WORKFLOW`, `TODO_CENTRAL_REF`, and `TODO_BOUNDED_*_INPUT`
   placeholders with the final product-neutral API. Do not guess those names from
   these scaffolds.
7. Keep product paths, schemes, Gradle tasks, package-manager commands, checked-in
   scripts, and other application behavior in the consumer repository.

## Lifecycle boundary

These skeletons intentionally use GitHub-native branch/path/ref structure and do
not introduce a planner, queue, database, custom CI command language, or
organization-specific run protocol. The owner-approved lifecycle decision in
`StreamScapeTV/organization-rules#66` is complete; the merged shared
`organization-rules@main/RULES.md` policy is authoritative.

Native GitHub `[skip ci]` or `[ci skip]` markers may be used on coherent
intermediate checkpoints when CI is not wanted. A coherent non-skipped branch
commit intentionally requests any configured push CI. A skipped HEAD is
checkpoint-only whenever required pull-request validation applies, so the final
candidate must be a non-skipped exact head.

A finalized pull request should receive the repository-defined relevant
validation, and the protected integration branch should run the repository-defined
post-merge validation. Repository-specific merge strategy and required checks stay
with the consumer.

## Templates

- `apple.yml` — Apple validation caller shape.
- `android.yml` — Android / Gradle validation caller shape.
- `node.yml` — Node validation caller shape.
- `python.yml` — Python validation caller shape.
- `script.yml` — generic checked-in script validation caller shape.

Organization-wide maintenance such as issue-dependency synchronization remains
central. Do not copy those workflows into product repositories.
