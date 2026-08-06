# Consumer versioning and upgrades

Consumer repositories use thin callers and update shared workflow configuration through explicit, reviewable pull requests. The initial shared reference is deliberately uniform so a correction in this repository does not require a coordinated edit in every consumer.

## Reference policy

During the first organization migration, every public workflow family may be called through protected `StreamScapeTV/ci-workflows@main`. This includes validation, Agent State transport, physical-device validation, OCI and Helm work, release orchestration, Flux-authorized wrappers, and trusted maintenance. The user-selected purpose is rapid central iteration while the shared function and workflow library is still being established.

Two fixed reference forms remain supported from the beginning:

- an immutable full commit SHA;
- an immutable SemVer tag such as `v1.2.3`.

A consumer may therefore stay on `@main`, pin a known-good full SHA, or move to a tagged version without changing the workflow API. A migration pull request records the selected reference and a known-good rollback SHA or tag. Protected `main` is a workflow implementation channel; exact product source, release tags, pull-request heads, Agent State requests, device commands, and Flux policy are still validated independently by the called workflow.

## Upgrade pull request

A consumer update records:

1. the current and proposed shared reference;
2. the public API names and versions used;
3. any stable check-name, input, output, permission, secret, runner-intent, or product change;
4. exact same-source parity evidence where replacing local implementation;
5. the known-good rollback reference;
6. required organization/repository Actions access and allowlist changes;
7. cleanup of the replaced local implementation only after the central call is proven.

The migration must preserve the consumer's trigger, path filters, concurrency, environment protection, minimum permissions, product-owned commands, and stable branch-protection check surface unless the same pull request intentionally changes and documents them.

A normal central implementation fix does not require a consumer pull request while that consumer follows `@main`. A public API contract change remains a reviewed change in `ci-workflows`; any required consumer input, permission, secret, trigger, or check-name change still requires an explicit consumer pull request.

## Compatibility classes

### Compatible

Examples include adding a public workflow, adding an optional input without changing default behavior, adding an output, widening documented consumer support without changing privilege, and clarifying documentation. Consumers may update normally after exact-source validation.

### Conditional

Examples include tightening validation inside documented bounds, changing a named function without public behavior change, changing timeout or matrix limits within the published maximum, or deprecating an API with a supported replacement window. The pull request records the affected conditions and proves them for that consumer.

### Breaking

Examples include removing or renaming a workflow, input, output, secret, or stable check; adding a required input or secret; changing default behavior; expanding caller permissions; changing a trust class or reference policy; changing semantic runner intent incompatibly; removing a supported product/consumer; or increasing reusable-workflow depth.

A breaking change fails the compatibility command until `contracts/public-workflow-types.json` contains an explicit acknowledgement with:

- a unique ID;
- API name and change kind;
- reason;
- migration issue;
- effective version.

Acknowledgement does not make the change automatically safe. Every affected consumer still requires its own reviewable pull request and rollback plan.

## Release manifest

Each stable `ci-workflows` tag has a machine-readable release manifest conforming to `contracts/release-manifest.schema.json`. The manifest binds:

- shared tag and exact repository commit;
- every workflow API version, file, and digest;
- function-library version and digest;
- schema digests;
- action and tool locks;
- runner-profile version;
- known consumer references and products.

A tag is the `ci-workflows` repository release. No GitHub Release object or attached archive is required. Do not delete or rewrite a release still referenced by a supported consumer.

## Rollback

A consumer following `@main` records a known-good full SHA or tag before migration. During a central incident, rollback changes only the thin caller's shared reference from `@main` to that recorded fixed reference. Product state, database state, Flux desired state, credentials, and published immutable products are not silently rolled back by a workflow-reference change. Any product or deployment rollback follows the owning repository's separate policy.

A bad tagged release remains available for forensic comparison while consumers roll back. Fixes are published under a new immutable reference; existing tags are never moved. A bad `main` commit is corrected through a reviewed follow-up commit rather than rewriting branch history.

## Private repository access

`ci-workflows` must remain accessible to every supported private repository in `StreamScapeTV`. Consumer Actions policies must allow the approved shared workflow reference and pinned third-party actions. GitHub may expose shared workflow implementation details in caller logs; therefore public workflow source and logs contain no private endpoint, credential, production data, or cluster secret.

Private dependency access uses an explicit short-lived read token only for the inventory-approved repository and exact SHA. Broad token inheritance is forbidden.

## Revocation, rename, and recovery

Access revocation, repository transfer/rename, or organization Actions-policy changes can make an otherwise compatible reference unusable. The consumer fails closed, retains or selects its known-good fixed reference, and opens a bounded compatibility issue. It does not copy the central implementation into the product repository as an emergency workaround.

When a shared reference is bad, stop new migrations, identify affected consumers from the inventory or release manifest, apply fixed-reference rollbacks where required, merge a reviewed correction to protected `main`, publish a corrected immutable tag when appropriate, and resume migrations only after exact parity and compatibility checks pass.
