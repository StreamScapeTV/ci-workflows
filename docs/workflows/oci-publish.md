# Trusted OCI publication (`oci.publish`)

`reusable-oci-publish.yml` is the privileged publication/read-back layer for
inventory-approved OCI products. It accepts only an exact admitted source SHA,
a checked-in product ID, an exact stable SemVer version, optional checked-in
platform confirmation, and two explicitly named registry credentials. Callers
cannot select a registry host/repository, builder, engine, runner label, storage
driver, shell command, callback, Flux target, or Kubernetes target.

## Event and release authority

The reusable workflow retains the original caller event. It derives release
authority rather than forcing one mode:

- a tag `push` uses `tag-push` authority. The resolved tag version and commit must
  equal the public `release_version` and `admitted_sha`; only this path may fill
  a missing immutable registry identity;
- `workflow_dispatch` uses the reviewed `existing-tag` authority, including the
  default-branch/current-caller/write-authority checks in the shared release-tag
  primitive. This path is **verify-only**: both immutable identities must already
  exist and match the exact locally rebuilt manifest. Missing identities fail
  with `remote_reference_missing`; no registry copy is attempted.

Both modes revalidate the exact tag object/commit after the potentially long
build and before registry credentials are exposed, then revalidate once more
immediately before any registry write. Pull-request, `pull_request_target`, and
`workflow_run` source cannot reach publication authority.

The reusable workflow does not clone the private central repository with the
caller token. It invokes reviewed immutable private composite-action SHAs for
release-tag authority, exact checkout, workspace state, OCI build validation,
publication/read-back, evidence, and cleanup. Caller repository/source still
comes only from the caller-associated GitHub context and the exact admitted SHA.

## Thin tag-push callers

Each supported consumer owns its tag event, minimum permissions, concurrency,
and mapping from consumer-owned secrets to the two named workflow-call secrets.
The tag name must be the exact stable SemVer release version (for example,
`1.2.3`, not `v1.2.3`), and `github.sha` must be the exact source selected by
that tag. The `"*"` filter below routes tag pushes only; the reusable workflow
still rejects any tag that is not an exact admitted stable SemVer release.

The central reference in each example is deliberately a non-runnable
placeholder. At adoption time, replace `<exact-compatible-tag-or-full-sha>`
with an exact compatible `ci-workflows` release tag or full 40-character commit
SHA. Do not make a privileged production caller depend on a moving branch
reference. GitHub does not expand expressions or variables in a reusable
workflow `uses` reference.

### IPTV Backend image

```yaml
name: Publish IPTV Backend OCI image

on:
  push:
    tags:
      - "*"

permissions:
  contents: read

concurrency:
  group: oci-publish-${{ github.repository }}-iptv-backend-image
  cancel-in-progress: false

jobs:
  publish:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-oci-publish.yml@<exact-compatible-tag-or-full-sha>
    with:
      admitted_sha: ${{ github.sha }}
      product_id: iptv-backend-image
      release_version: ${{ github.ref_name }}
      platform_set: linux-multi-arch
    secrets:
      registry_username: ${{ secrets.registry_username }}
      registry_token: ${{ secrets.registry_token }}
```

### Agent State image

```yaml
name: Publish Agent State OCI image

on:
  push:
    tags:
      - "*"

permissions:
  contents: read

concurrency:
  group: oci-publish-${{ github.repository }}-agent-state-image
  cancel-in-progress: false

jobs:
  publish:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-oci-publish.yml@<exact-compatible-tag-or-full-sha>
    with:
      admitted_sha: ${{ github.sha }}
      product_id: agent-state-image
      release_version: ${{ github.ref_name }}
      platform_set: linux-multi-arch
    secrets:
      registry_username: ${{ secrets.registry_username }}
      registry_token: ${{ secrets.registry_token }}
```

### Flux runner images

```yaml
name: Publish Flux runner OCI images

on:
  push:
    tags:
      - "*"

permissions:
  contents: read

concurrency:
  group: oci-publish-${{ github.repository }}-flux-runner-images
  cancel-in-progress: false

jobs:
  publish:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-oci-publish.yml@<exact-compatible-tag-or-full-sha>
    with:
      admitted_sha: ${{ github.sha }}
      product_id: flux-runner-images
      release_version: ${{ github.ref_name }}
      platform_set: linux-amd64
    secrets:
      registry_username: ${{ secrets.registry_username }}
      registry_token: ${{ secrets.registry_token }}
```

These callers provide no registry destination, runner label, container engine,
cluster identity, Flux credential, or deployment target. Those fields are not
part of `oci.publish`; the checked-in product contract selects build and
registry policy, and publication remains separate from deployment.

## Execution stages

1. **resolve-product** — resolve exact caller tag authority and checked-in OCI
   product/runner policy;
2. **verify-builder-toolchain** — require the runner's pre-provisioned Buildah
   1.33.7, Skopeo 1.13.3, and Podman 4.9.3 exactly before any build or registry
   authentication, and pass that verified version map into redacted evidence;
3. **build-exact-source** — check out the admitted SHA and rebuild/inspect it
   through merged `oci.build`; no prior image artifact is trusted;
4. **revalidate-before-authenticate** — confirm the tag object and commit are
   still exact after the build and before registry credentials are exposed;
5. **authenticate** — create one workflow-scoped auth file and pass the registry
   token only through stdin;
6. **publish-or-verify** — revalidate the tag again immediately before writes,
   then compare the local manifest digest with both immutable
   remote identities; tag-push may copy only missing matching identities while
   manual verification performs no writes;
7. **read-back** — independently copy the version identity from the registry to a
   fresh OCI layout;
8. **verify** — require exact manifest, platform, config/layer, metadata, runtime,
   source/version, and source-SHA-reference parity;
9. **clean** — remove auth, read-back layouts, publication state, OCI build state,
   and registered workspace state, then verify residue is absent;
10. **bind-terminal-supply-evidence** — after workspace cleanup, append one
    canonical redacted record binding the exact central workflow and publication
    helper SHAs, contract-owned builder and semantic runner profile, exact
    verified toolchain, publication and foundation evidence identities, release
    identity, execution result, and every terminal cleanup outcome. The public
    success projection fails closed unless this final record is written.

Routine Actions artifacts are forbidden and publication never mutates Flux or
Kubernetes state.

## Destination and immutable identities

The caller never supplies a destination. `oci.publish` preserves the reviewed
GHCR contract expected by release orchestration:

- single-target product: `ghcr.io/streamscapetv/<repository-name>`;
- multi-target product: `ghcr.io/streamscapetv/<repository-name>-<target-id>`.

Every target uses exactly two tags:

- `<release-version>`;
- `sha-<40-hex-source-sha>`.

`latest` is never produced. Existing references are replay-safe only when their
raw registry manifest digests equal the exact local manifest digest. A conflict
fails before any write. Tag publication repairs only a missing member of an
otherwise matching immutable pair; verify-only requires the complete pair.

Remote absence is accepted only when registry inspection returns an explicit
manifest/name-missing condition. Generic network/endpoint “not found” errors are
not treated as evidence that an immutable reference is absent.

## Independent OCI read-back

Both locally built and independently pulled layouts must contain the exact OCI
layout marker `{\"imageLayoutVersion\":\"1.0.0\"}` before any descriptor is
trusted. Descriptor digests/sizes/media types are verified recursively through
the bounded image-index shape. Read-back proves:

- expected platform set;
- per-platform manifest digest;
- config digest and layer digest list;
- exact OCI metadata labels including source SHA/version/product identity;
- configured user, entrypoint, command, and exposed ports;
- source-SHA identity resolving to the same top-level manifest digest.

The publication guard also reuses the merged `oci.build` target parser and layer
filesystem assertion logic on both the rebuilt layout and the independently
pulled layout. Contract-owned required files/tools must be present and forbidden
tools must be absent. #17 does not invent runtime-version or subordinate-ID
policy that is not encoded in `contracts/oci-products.json`; stronger Flux-owned
runtime assertions remain in the higher-level `flux.assets` contract.

## Registry credentials and cleanup

`registry_username` and `registry_token` are the only publication secrets. The
token is sent to `skopeo login` through stdin and is never placed in an argv,
output, evidence object, state JSON, or Actions artifact. The auth file is a
regular non-symlink file with owner-only permissions below the per-run
publication state root.

Cleanup removes all publication/auth/read-back state. Symlink substitution at
the deterministic state root is unlinked without following the target and is a
cleanup failure. The separate `oci.build` cleanup removes builders, manifests,
images, layouts, staged source, and caches; both layers run terminal residue
checks.

## Public outputs

The public API remains exactly:

- `result`;
- `manifest_digests_json` — canonical JSON target-to-manifest digest map;
- `platform_digests_json` — nested target/platform manifest, config, layer, and
  metadata proof;
- `immutable_references_json` — exact target repository/version/source refs plus
  exact base references, verified assertion evidence, release identity and, for
  Flux products, canary/known-good/rollback identities.

Each `immutable_references_json.targets` entry names its immutable `sha-<40hex>`
registry tag as `source_reference`. The separate
`immutable_references_json.release.source_sha` field remains the exact raw
40-character source commit.

Every immutable target carries `assertions.result=passed`, the exact verified
platform set, and a SHA-256 identity of the complete checked-in runtime,
filesystem, tool, forbidden-state, and healthcheck contract. Safe
contract-owned container paths and tool names remain visible for audit;
entrypoint, command, and healthcheck test vectors are represented only by their
item count and SHA-256 identity. This proves which assertions passed without
exposing host paths, credentials, or command text. The verify phase writes this
same canonical redacted proof and its deterministic publication `evidence_id`
to the GitHub step summary before credential/state cleanup.

The publication schema is closed recursively for these nested payloads. No host,
auth-file path, credential, mutable ref, or cluster identity is returned.

## CIW and compatibility wrappers

The public facade exposes `ci_workflows.oci.publish` and
`ci_workflows.oci.read_back`. `ciw_oci` contains the bounded `oci publish`
adapter for plan/authenticate/publish/readback/verify/cleanup/residue. Shared
command/public registry files are serialized organization surfaces and are
registered only by their current owner; the issue branch does not take them
over.

## Flux runner images

`flux-runner-images` uses the merged #16 inventory: exactly
`runner-buildah` and `runner-mobile`. Each target receives a deterministic GHCR
repository. `immutable_references_json.flux` carries only the checked-in canary,
previous-known-good, and rollback identities for later `flux.assets` review-only
handoff. Publication does not edit Flux manifests, select a live runner image,
reconcile a scale set, or receive Kubernetes/SOPS authority.

## Evidence boundary

Hermetic tests cover event trust, explicit absence handling, immutable replay and
conflict behavior, no-write verify-only, missing-reference repair on tag
publication, independent layout read-back, real #16 required/forbidden
filesystem assertions, token-stdin/auth-file permissions, private-helper
immutability, and cleanup. A live private-registry proof is claimed only when the
exact final candidate runs with authorized least-privilege credentials;
unit/mock evidence never substitutes for that proof.
