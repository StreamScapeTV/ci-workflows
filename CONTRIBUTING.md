# Contributing to CI Workflows

## Issue-led development

Every implementation starts from one bounded GitHub issue. The issue description is the live plan and records dependencies, scope, public API effects, security boundaries, exact base/head, validation, migration evidence, and completion. Split independently mergeable work before implementation.

Use one branch and one pull request per issue. Branch names use `issue/<number>-<slug>`. Do not force-push published work or combine unrelated issues.

## Implementation order

Foundation and contracts precede public workflows. A public workflow cannot merge until its API record, documentation, positive fixtures, negative security fixtures, cleanup tests, and compatibility checks exist.

Product commands remain in consumer repositories. Shared code owns reusable orchestration and generic functions; it must not grow repository-name conditionals to copy product policy.

## Readable architecture

The normal path is:

```text
consumer trigger -> public reusable workflow -> thin composite action or named function
```

Only a reviewed multi-job orchestration may use one internal reusable-workflow leaf layer. Internal leaves do not call another reusable workflow. Inline shell is glue, not an application runtime.

## Public compatibility

During bootstrap, consumers may reference `@main`. Tags and full commit SHAs are also supported. Public changes still require machine-readable compatibility updates so a later immutable-tag migration is reviewable.

A change is breaking when it removes or renames an input, output, secret, check name, trust mode, supported product, or permission; changes a default incompatibly; expands required permissions or secrets; changes runner trust; or changes behavior that callers rely on. Breaking changes require an explicit migration plan.

## Validation

Run focused tests while developing and the complete self-check on the exact final head. The canonical self-check validates repository structure, workflow YAML shape, policy contracts, clean-tree behavior, and zero routine artifacts without product credentials.

A changed head or base invalidates older evidence. Merge only the unchanged validated head, normally by squash.

## Releases

The repository release is a Git tag only. No GitHub Release object or attached artifact is required. A tag identifies an exact compatible shared-workflow commit and can be referenced as `@<tag>`; `@main` remains the initial rapid-update channel until the owner changes the policy.

Do not delete a tag that is still referenced by a supported consumer. Record rollback tags and compatibility notes in source-controlled manifests when release management is implemented.

## Consumer migration

A consumer migration needs a linked issue in both repositories, old/new validation on the same exact consumer SHA, an atomic required-check transition, documentation updates, and removal of duplicate orchestration only after parity. Agent State and Flux domain authority stays in their owning API/repository even when workflow implementation moves here.

## Security and artifacts

Use least privilege, explicit named secrets, exact source selection, and separate trust profiles for validation, Agent State transport, publication, Flux-authorized reconciliation, and maintenance.

Routine workflows upload no artifacts. Never print or retain credentials, environment files, private endpoints, SOPS material, kubeconfigs, signing data, image archives, chart packages, build outputs, logs, reports, or caches unless a reviewed bounded exception explicitly requires it.
