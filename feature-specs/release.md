# Simple SemVer release model

## Scope

This specification defines the canonical release-identity model for Central CI release flows that publish Docker/OCI images and Helm OCI charts using SemVer.

It defines shared Central behavior only. Product repositories continue to own their fixed registry coordinates, build commands, release gates, and any stricter product-specific publication checks that do not conflict with this specification.

## Core model

Keep the release path simple.

For a SemVer tag-driven release, the plain SemVer version is the sole StreamScapeTV release identity. The immutable Git tag has exactly that version and selects the source to publish.

For example, release `3.2.3` means:

- Git release tag: `3.2.3`;
- Docker/OCI image version: `:3.2.3`;
- Helm OCI chart version: `3.2.3`.

There is no second release identity made from a Git commit SHA, OCI manifest digest, chart/archive digest, environment hash, deployment hash, or any combination of those values.

## Tag-driven publication

A reviewed SemVer release follows this model:

1. the target repository establishes the next valid SemVer according to its product release policy;
2. an immutable Git tag whose name is that SemVer selects the exact source;
3. Central admits the tag and resolves/checks out the tagged source;
4. the repository-owned release entrypoint publishes every release artifact required by the target repository under the same SemVer;
5. publication reads the required artifacts back through the authenticated registry path before the release run may succeed;
6. downstream deployment and certification refer to the release by SemVer.

If the immutable tag-driven publication fails, do not retarget, mutate, or reconstruct that historical release identity. Follow the target repository's release policy for the next release attempt/version.

## Registry addressing

Docker/OCI images are addressed and fetched by their fixed repository coordinate plus SemVer tag.

Example form:

```text
<registry>/<namespace>/<image>:3.2.3
```

Helm OCI charts are addressed and fetched by their fixed repository coordinate plus SemVer chart version.

Example form:

```text
oci://<registry>/<namespace>/<chart> --version 3.2.3
```

Central must not require a caller or downstream project to provide an OCI digest merely to identify or fetch an artifact that is canonically addressed by SemVer.

## What a successful release means

A successful release run means the repository-owned release contract successfully published and read back every artifact required for that release at the selected SemVer.

The existence of a Git tag alone does not prove publication succeeded. The successful tag-driven release run is the publication evidence.

Once publication succeeds, downstream systems do not reconstruct the release by collecting additional SHA or digest authority. They use the same SemVer and the repository's fixed artifact coordinates.

## SHA and digest boundaries

Exact Git commit SHAs remain valid internal source/CI provenance. Central may record which exact commit an immutable tag resolved to, which PR head was validated, or which merge commit integrated a candidate. That does not make the SHA a release identity and does not make it a required downstream release/certification input.

Registry/build tooling may naturally compute or observe image manifest digests, chart/archive digests, checksums, or similar fingerprints while pushing, pulling, verifying transport, or producing diagnostics. Those values may be retained as bounded internal evidence when useful, but they must not become:

- a second release identity;
- a caller-selected release input;
- a required cross-project handoff value;
- a prerequisite for ordinary deployment or certification;
- a separate release-identity registry or attestation authority.

Environment hashes and deployment hashes are likewise not release identities.

## Deployment and certification

For a product whose deployment contract is SemVer-based, the intended application release is identified by SemVer.

Deployment/runtime evidence may verify that the deployed image/chart version is the requested SemVer and may perform the product's required health, functional, conformance, reset, cleanup, and private-input checks. It must not require digest equality, Git SHA equality, or environment-hash equality as a second way to identify the same release unless the owner explicitly authorizes a different product-specific architecture in the applicable canonical feature specification.

Protected targets, scenarios, credentials, private-network access, deterministic reset/readback, evidence redaction, and cleanup remain separate security/runtime concerns. Their existence does not justify inventing additional release identity.

## Forbidden overengineering

Without explicit owner permission in the applicable feature specification, do not add any of the following merely to identify an already SemVer-addressable release:

- release-manifest identity services;
- digest attestation services;
- Git-SHA release registries;
- chart/image digest handoff protocols;
- environment-hash release identity;
- duplicate release metadata stores;
- parallel release version authorities;
- a requirement that downstream projects recover internal push/readback digests before they can deploy or certify a release.

When a simple SemVer lookup against the fixed private registry coordinate answers the release-artifact question, use that mechanism.

## Compatibility with exact-source CI

This specification does not weaken exact-source CI, review, merge, or source-snapshot protections. Those protections may use commit/tree SHAs to prove what source was validated or integrated. They are source-evidence mechanisms, not release-identity mechanisms.
