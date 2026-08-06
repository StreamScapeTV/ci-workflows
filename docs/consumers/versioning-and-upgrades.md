# Consumer versioning and upgrades

Consumer repositories use thin callers and update shared workflow references through an explicit, reviewable pull request. A shared workflow update is never a silent organization-wide mutation.

## Reference policy

Two immutable reference forms are supported:

- an immutable full commit SHA;
- an immutable SemVer tag such as `v1.2.3`.

During the initial bootstrap, `@main` is allowed only for source-admission and ordinary read-only validation APIs so central fixes can be adopted quickly. `@main` is forbidden for Agent State mutation, physical-device credentials, registry publication, release orchestration, Flux-authorized reconciliation, and trusted maintenance.

Production and privileged callers use an immutable full commit SHA or immutable SemVer tag recorded in the migration pull request. A consumer must retain a known-good rollback reference before switching.

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

## Compatibility classes

### Compatible

Examples include adding a public workflow, adding an optional input without changing default behavior, adding an output, widening documented consumer support without changing privilege, and clarifying documentation. Consumers may update normally after exact-source validation.

### Conditional

Examples include tightening validation inside documented bounds, changing a named function without public behavior change, changing timeout or matrix limits within the published maximum, or deprecating an API with a supported replacement window. The pull request records the affected conditions and proves them for that consumer.

### Breaking

Examples include removing or renaming a workflow, input, output, secret, or stable check; adding a required input or secret; changing default behavior; expanding caller permissions; changing trust class or immutable-reference policy; changing semantic runner intent incompatibly; removing a supported product/consumer; or increasing reusable-workflow depth.

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

Rollback changes only the thin caller's shared reference to its recorded known-good rollback reference. Product state, database state, Flux desired state, credentials, and published immutable products are not silently rolled back by a workflow-reference change. Any product or deployment rollback follows the owning repository's separate policy.

A bad shared release remains available for forensic comparison while consumers roll back. Fixes are published under a new immutable reference; existing tags are never moved.

## Private repository access

`ci-workflows` must remain accessible to every supported private repository in `StreamScapeTV`. Consumer Actions policies must allow the approved shared workflow reference and pinned third-party actions. GitHub may expose shared workflow implementation details in caller logs; therefore public workflow source and logs contain no private endpoint, credential, production data, or cluster secret.

Private dependency access uses an explicit short-lived read token only for the inventory-approved repository and exact SHA. Broad token inheritance is forbidden.

## Revocation, rename, and recovery

Access revocation, repository transfer/rename, or organization Actions-policy changes can make an otherwise compatible reference unusable. The consumer fails closed, retains its previous reference, and opens a bounded compatibility issue. It does not copy the central implementation into the product repository as an emergency workaround.

When a shared release is bad, stop new migrations, identify affected consumers from the release manifest, roll them back through reviewable pull requests, publish a corrected immutable release, and resume migrations only after exact parity and compatibility checks pass.
