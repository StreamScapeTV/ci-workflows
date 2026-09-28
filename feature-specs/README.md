# ci-workflows feature specifications

## Authority

`feature-specs/**` is the canonical architecture and product-behavior authority for `StreamScapeTV/ci-workflows`.

Agents must read every feature specification applicable to their bounded change before planning, implementing, reviewing, or changing Central CI behavior. Current workflow YAML, source, tests, documentation, issues, pull requests, comments, historical behavior, and implementation convenience are evidence of current or historical behavior; they do not override an applicable feature specification.

If implementation or current work conflicts with an applicable feature specification, stop the conflicting change and surface the mismatch. Do not reinterpret the specification to match existing code and do not silently change the specification.

Changes to `feature-specs/**` require the owner's explicit permission. An agent must not infer permission from an issue, implementation need, review finding, failing test, migration, or cross-project request. When explicit owner permission authorizes a specification change, the durable issue carrying that change must state that authority and its bounded scope.

Workflow YAML remains the exact runtime/API source of truth only within the architecture and behavior allowed by these specifications. A runtime workflow cannot redefine or override the specifications.

## Specifications

- [Simple SemVer release model](release.md) — canonical release identity, publication, registry addressing, and downstream release-reference rules for Central tag-driven SemVer releases.

## Design principle

Prefer the smallest conventional mechanism that satisfies the feature specification. Do not introduce parallel authorities, identity registries, attestation layers, compatibility frameworks, or derived control planes when the canonical model can be expressed directly.
