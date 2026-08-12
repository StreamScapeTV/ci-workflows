# OCI build validation

`validation.oci` is intentionally not the public name. The public API is
`oci.build` version `1.0.0`, exposed by
`.github/workflows/reusable-oci-build.yml` with stable check
`CI / OCI build validation`.

The caller supplies only an exact admitted source SHA, one checked-in product
identifier, an optional exact SemVer metadata value, and an optional
contract-owned platform-set confirmation. The caller cannot select Docker,
Buildah, BuildKit, Podman, a socket, storage driver, registry command,
concrete runner label, command, arguments, callback, secret name, publication,
or deployment behavior.

## Execution order

1. A general-Linux planning job checks out and verifies the exact called central
   workflow source through `job.workflow_repository` and `job.workflow_sha`,
   then loads `contracts/oci-products.json`, validates the repository/product
   relationship, confirms the measured runner tier, and emits the exact JSON
   runner selector from `generated/oci-engine-mapping.json`.
2. The build job consumes that selector with `fromJSON`; it never reconstructs
   or concatenates labels. It independently checks out and verifies the same
   exact called central workflow source before using local actions.
3. Exact caller source is checked out detached without persistent credentials.
4. Only tracked clean context files are copied into isolated state. Symlinks,
   untracked files, path escapes, and mutable `FROM` identities are rejected.
5. The internal `buildah-v1` adapter builds each contract target without
   publication, cache reuse, registry credentials, or `latest`.
6. Every result is exported to a temporary local OCI layout and independently
   rehashed. The platform set, manifests, configs, layers, runtime fields, and
   normalized OCI labels must match the contract.
7. A contract-owned checked-in smoke script may inspect only the bounded local
   layout and assertion lists. It receives no registry or Flux credential.
8. Images, containers, manifests, layouts, staged source, caches, and temporary
   state are removed under unconditional cleanup; residue and source cleanliness
   are checked separately. Routine Actions artifacts remain zero.

## Product examples

```yaml
jobs:
  oci:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-oci-build.yml@<immutable-ref>
    with:
      admitted_sha: ${{ needs.source.outputs.source_sha }}
      product_id: iptv-backend-image
      release_version: 1.2.3
```

Agent State uses `agent-state-image`. Flux uses `flux-runner-images`; its current
checked-in targets are exactly the Buildah and mobile runner image families at
`images/github-actions-runner-buildah/Dockerfile` and
`images/github-actions-runner-mobile/Dockerfile`. The Flux product record
requires an independent high-capacity builder tier and emits only bounded
canary, previous-known-good-policy, and rollback identifiers. Publication does
not select a runner image in Flux and does not mutate a cluster.

The internal `ciw-oci-smoke` fixture uses `FROM scratch`, one native platform,
and the smallest measured `buildah-tiny` tier. It performs a genuine
non-publishing Buildah build with no network base or registry dependency.

## Base and secret rules

Every Dockerfile stage must use `scratch` or an exact `@sha256:` base. Channels,
tags without digests, `ARG`-selected bases, caller download URLs, and mutable
builder authority fail closed. The adapter supports only contract-declared
secret mount IDs and requires private regular files; secret values and their
SHA-256 strings are scanned out of configs, history, labels, and layer blobs.
The initial public workflow exposes no build-secret input.

## Outputs

Outputs are bounded JSON identities for the local OCI indexes and each exact
platform manifest/config/layer set, plus deterministic evidence, source, clean
state, and Flux handoff identifiers where applicable. They are not publication
receipts and must not be interpreted as registry or deployment success.
