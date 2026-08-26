# Consumer versioning and upgrades

Consumer repositories use thin callers and adopt shared technology contracts through explicit, reviewable pull requests. Ordinary compatibility is defined by public API name/version, trust, permissions, technology inputs/outputs, and behavior—not by membership in a centralized repository or product list and not by a per-component checkpoint registry.

## Reference policy

`StreamScapeTV/ci-workflows@main` is the normal active-development reference for the shared Central library. A caller may also use a full repository commit SHA or a repository SemVer tag when it functionally needs a fixed **whole-repository** snapshot. Those references identify the repository as a whole: functions, first-party actions, and reusable workflows do not receive independent versions or checkpoint identities.

Repository tags therefore describe one complete Central snapshot. There is no action/tool lock, function release registry, helper-release comment contract, or requirement to propagate a new component SHA when an internal implementation changes.

Exact caller source, release tags, device commands, and other product-owned identities are validated independently when they are functional inputs to the called workflow.

## Upgrade pull request

A consumer update records only what is relevant to the technology contract:

1. the public API names and versions used;
2. any stable check-name, input, output, permission, secret, runner-intent, or behavior change;
3. exact same-source parity evidence where replacing local implementation;
4. required organization/repository Actions access changes;
5. cleanup of replaced local implementation only after the central call is proven.

Changing an ordinary caller from one Central snapshot to another does not require publishing or recording per-action/function versions. A caller following `@main` follows the current protected library channel by design.

The consumer retains ownership of repository identity, triggers, path filters, concurrency, environments, product/project names, relative source/build/chart/output paths, schemes, tasks, scripts, and release manifests. Adding a new application repository that already satisfies a technology contract does not require editing a central application allowlist.

## Compatibility classes

### Compatible

Examples include adding a public workflow, adding an optional input without changing default behavior, adding an output, widening a documented technology capability without privilege change, and clarifying documentation.

### Conditional

Examples include tightening validation inside documented bounds, changing a named function without public behavior change, changing timeout or matrix limits within the published maximum, or deprecating an API with a supported replacement window.

### Breaking

Examples include removing or renaming a workflow/input/output/secret/check, adding a required input or secret, changing default behavior, expanding permissions, changing trust/reference policy, narrowing a documented technology capability, or increasing reusable-workflow depth.

A breaking change fails compatibility validation until `contracts/public-workflow-types.json` contains an explicit acknowledgement with an ID, API name, change kind, reason, migration issue, and effective version. An acknowledgement records migration authority; it does not create a per-component release ceremony.

## Whole-repository releases

When `ci-workflows` publishes a stable repository tag, that tag identifies the complete repository snapshot. Any release manifest retained for repository-level release tooling describes the shared repository/API state; it must not become a per-function, per-action, or per-workflow version registry or restore the retired action/tool lock.

Functional product release identities remain separate. For example, an exact product Git tag, published image version, chart version, package digest, or remote read-back may be mandatory because it proves what was actually released. Those checks do not version Central helper components independently.

## Recovery and revocation

A bad `main` change is corrected by a reviewed follow-up commit on protected `main`; published history is not rewritten. A full repository SHA or repository tag may be selected as a temporary fixed snapshot when operationally useful, but maintaining a mandatory known-good rollback reference for every Central component is not part of the public API policy.

Access revocation, repository rename/transfer, or Actions-policy changes can make an otherwise compatible reference unusable. The caller fails closed and opens a bounded compatibility issue rather than copying Central implementation into the product repository as an emergency fallback.

## Private repository access

`ci-workflows` must be accessible to private callers that use it. Actions policy must permit the selected Central reference. Third-party actions use the ordinary upstream reference required by their syntax/functionality; there is no organization-wide immutable third-party pin registry.

When a technology operation needs a private dependency, the bounded workflow accepts only the reviewed named credential/path/repository inputs defined for that capability. Broad token inheritance remains forbidden. Private source, private configuration, credentials, and private CI logs/output stay protected regardless of whether the Central caller uses `@main` or a whole-repository snapshot.
