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

## Immutable private helper reuse

Private same-organization consumers do not clone `StreamScapeTV/ci-workflows`
with their caller-scoped token. The planner and build job invoke the reviewed
`validate-oci` composite action through immutable central revision
`676aa6b1b4d5fb8d4c26531e1a181e37b33e3433`; exact caller checkout, workspace
preparation, deterministic evidence, and terminal workspace cleanup reuse the
reviewed immutable foundation helpers.

The private action archives resolve their central scripts and Python modules
through `GITHUB_ACTION_PATH`. The reusable workflow therefore has no `.ciw`
central checkout, central PAT input, `secrets: inherit`, mutable helper ref, or
caller-selected helper version. Exact caller source remains separately admitted,
checked out detached, and reverified clean after cleanup. Buildah tiering,
trusted-source requirements, publication denial, cleanup/residue checks, and the
zero-artifact contract are unchanged by this helper-distribution mechanism.

## Execution order

1. A general-Linux planning job invokes the immutable private `validate-oci`
   action, loads `contracts/oci-products.json`, validates the repository/product
   relationship, confirms the measured runner tier, and emits the exact JSON
   runner selector from `generated/oci-engine-mapping.json`.
2. The build job consumes that selector with `fromJSON`; it never reconstructs
   or concatenates labels. It composes the same immutable central helper set
   directly instead of cloning the private central repository.
3. Exact caller source is checked out detached without persistent credentials.
4. Only tracked clean context files are copied into isolated state. Symlinks,
   untracked files, path escapes, and mutable `FROM` identities are rejected.
5. The target's exact product-owned build-input lock is loaded from the path
   fixed by the central product contract. Its ordered stages, exact bases,
   selected platforms, and external inputs must match the Dockerfile and the
   central `input_policy_id`.
6. In a bounded pre-build acquisition phase, exact base descriptors are copied
   under an empty authentication file and independently hashed at both the OCI
   root and selected platform-manifest/config levels. Bounded external HTTPS
   inputs are host-, redirect-, size-, and digest-verified before being placed
   only beneath `.ciw-build-inputs` in staged context.
7. The internal `buildah-v1` adapter builds each contract target with
   `bud --pull=never --network none`, without publication, cache reuse,
   registry credentials, `latest`, or build-instruction egress.
8. Every result is exported to a temporary local OCI layout and independently
   rehashed. The platform set, manifests, configs, layers, runtime fields, and
   normalized OCI labels must match the contract.
9. A contract-owned checked-in smoke script may inspect only the bounded local
   layout and assertion lists. It receives no registry or Flux credential.
10. Images, containers, manifests, base layouts, local base tags, authentication
    files, downloaded inputs, `.ciw-build-inputs`, staged source, caches, and
    temporary state are removed under unconditional cleanup; residue and source
    cleanliness are checked separately. Routine Actions artifacts remain zero.

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

The internal `ciw-oci-smoke` fixture retains the `FROM scratch` no-input case.
The `ciw-oci-input-smoke` fixture uses an exact BusyBox digest and one
digest-locked public source input on one native platform. It proves the bounded
pre-build acquisition path and then performs a genuine non-publishing Buildah
build on the smallest measured `buildah-tiny` tier with build-time networking
disabled.

## Base, external-input, and secret rules

Every Dockerfile stage must use `scratch` or an exact `@sha256:` base. Channels,
tags without digests, `ARG`-selected bases, caller-selected download URLs,
caller-selected hosts, and mutable builder authority fail closed. The product
source supplies only the exact lock at the central-fixed target path; the
central product contract supplies its `input_policy_id` and host authority.
External inputs are accepted only as exact HTTPS URLs with declared SHA-256,
maximum byte count, and reserved relative destination below
`.ciw-build-inputs`. Redirects must remain within the same central profile.

Registry host authority is typed by protocol role rather than treated as one
interchangeable allowlist. The current Docker Hub profile admits references at
`docker.io`, Distribution API requests at `registry-1.docker.io`, anonymous
Bearer-token requests at `auth.docker.io`, and blob redirects at
`production.cloudfront.docker.com`. A host admitted for one role is not
automatically admitted for another, and none of these fields is caller input.

Acquisition has no ambient registry or HTTP authentication. Network access ends
before `buildah bud`, so consumer package managers and scripts cannot use this
mechanism for undeclared downloads. The adapter supports only contract-declared
secret mount IDs and requires private regular files; secret values and their
SHA-256 strings are scanned out of configs, history, labels, and layer blobs.
The initial public workflow exposes no build-secret input.

Every daemonless engine subprocess runs inside a fresh private mount namespace
inside the privileged Buildah capacity. The registered run's private implicit
containers directory is bind-mounted over `/var/lib/containers`, so rootful
containers/image blob-info caches remain inside cleanup ownership. HOME and all
XDG data, cache, configuration, and runtime directories are also created below
that registered per-run OCI state. Cleanup uses the same confinement and then
removes the complete state root.

## Outputs

Outputs are bounded JSON identities for the local OCI indexes and each exact
platform manifest/config/layer set, plus deterministic evidence, source, clean
state, and Flux handoff identifiers where applicable. They are not publication
receipts and must not be interpreted as registry or deployment success.

The thin `validate-oci` action and public `oci.build` workflow additionally
export `resolved_inputs_json` for downstream composition without retaining an
Actions artifact. The evidence is keyed by target and contains only lock/policy
IDs, verified base descriptor identities, external input IDs, digests, sizes,
and deterministic evidence IDs. URLs, credentials, authentication-file paths,
temporary paths, and mutable registry state are not reported.
