# Consumer versioning and upgrades

Consumer repositories use thin callers and adopt shared technology contracts through explicit, reviewable pull requests. Ordinary compatibility is defined by public API name/version, trust, permissions, technology inputs/outputs, and behavior—not by membership in a centralized repository or product list.

## Reference policy

During the initial migration, every public workflow family may be called through protected `StreamScapeTV/ci-workflows@main`. Two fixed forms remain supported from the beginning:

- an immutable full commit SHA;
- an immutable SemVer tag such as `v1.2.3`.

A caller may stay on `@main`, pin a known-good full SHA, or move to a tagged version. A migration pull request records the selected reference and a known-good rollback SHA or tag. Exact caller source, release tags, device commands, and Flux policy are validated independently by the called workflow.

## Upgrade pull request

A consumer update records:

1. the current and proposed shared reference;
2. the public API names and versions used;
3. any stable check-name, input, output, permission, secret, runner-intent, or technology-contract change;
4. exact same-source parity evidence where replacing local implementation;
5. the known-good rollback reference;
6. required organization/repository Actions access changes;
7. cleanup of replaced local implementation only after the central call is proven.

The consumer retains ownership of repository identity, triggers, path filters, concurrency, environments, product/project names, relative source/build/chart/output paths, schemes, tasks, scripts, and release manifests. Adding a new application repository that already satisfies a technology contract does not require editing `contracts/consumers.json`, `contracts/products.json`, or another central application allowlist.

## Compatibility classes

### Compatible

Examples include adding a public workflow, adding an optional input without changing default behavior, adding an output, widening a documented technology capability without privilege change, and clarifying documentation.

### Conditional

Examples include tightening validation inside documented bounds, changing a named function without public behavior change, changing timeout or matrix limits within the published maximum, or deprecating an API with a supported replacement window.

### Breaking

Examples include removing or renaming a workflow/input/output/secret/check, adding a required input or secret, changing default behavior, expanding permissions, changing trust/reference policy, narrowing a documented technology capability, or increasing reusable-workflow depth.

A breaking change fails compatibility validation until `contracts/public-workflow-types.json` contains an explicit acknowledgement with an ID, API name, change kind, reason, migration issue, and effective version. An acknowledgement records migration authority; it does not eliminate caller-side review or rollback planning.

A `migration-pending` record is a deliberate transition state. It means the reviewed next API version is published in the contract, while the existing reusable YAML still has the older interface. Callers remain on the old implemented interface until the wiring issue updates the YAML/CLI and flips the new version to `implemented`.

## Release manifest

Each stable `ci-workflows` tag has a machine-readable release manifest conforming to `contracts/release-manifest.schema.json`. The manifest binds shared release identity rather than application identity:

- shared tag and exact repository commit;
- workflow API versions, files, and digests;
- function-library version and digest;
- schema digests;
- action/tool locks;
- runner-profile version/digest.

The release manifest does not enumerate consumers or products. Application adoption is visible in each caller repository and may be navigated through organization tooling, but it is not part of the central compatibility contract.

## Rollback

A caller following `@main` records a known-good full SHA or tag before migration. During a central incident, rollback changes the thin caller reference to that fixed reference. Product state, database state, Flux desired state, credentials, and published immutable outputs are not silently rolled back by a workflow-reference change.

Existing immutable tags are never moved. A bad `main` commit is corrected by a reviewed follow-up commit rather than history rewriting.

## Private repository access

`ci-workflows` must be accessible to private callers that use it. Actions policies must permit the selected central reference and pinned third-party actions. Public workflow source/logs contain no credential, private endpoint, production data, or cluster secret.

When a technology operation needs a private dependency, the bounded workflow accepts only the reviewed named credential/path/repository inputs defined for that capability. Broad token inheritance remains forbidden; private dependency access does not create a general consumer allowlist.

## Revocation, rename, and recovery

Access revocation, repository rename/transfer, or Actions-policy changes can make an otherwise compatible reference unusable. The caller fails closed, selects its known-good rollback when needed, and opens a bounded compatibility issue. It does not copy central implementation into the product repository as an emergency fallback.

When a shared reference is bad, stop new migrations, identify affected callers from GitHub usage/navigation evidence, apply fixed-reference rollbacks where required, merge a reviewed correction to protected `main`, publish a corrected immutable tag when appropriate, and resume after parity and compatibility checks pass. Generic organization maintenance and discovery may help locate callers, but those inventories never become API admission policy.
