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
      registry_username: ${{ secrets.FORGEJO_REGISTRY_USERNAME }}
      registry_token: ${{ secrets.FORGEJO_REGISTRY_TOKEN }}
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
      registry_username: ${{ secrets.FORGEJO_REGISTRY_USERNAME }}
      registry_token: ${{ secrets.FORGEJO_REGISTRY_TOKEN }}
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
      registry_username: ${{ secrets.FORGEJO_REGISTRY_USERNAME }}
      registry_token: ${{ secrets.FORGEJO_REGISTRY_TOKEN }}
```

These callers provide no registry destination, runner label, container engine,
cluster identity, Flux credential, or deployment target. Those fields are not
part of `oci.publish`; the checked-in product contract selects build and
registry policy, and publication remains separate from deployment.

## Execution stages

1. **resolve-product** — resolve exact caller tag authority and checked-in OCI
   product/runner policy, and reject publication unless both product adoption
   and its independent registry-write policy are ready;
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
    identity, the exact registry-write policy authority SHA/evidence ID,
    execution result, and every terminal cleanup outcome. The public success
    projection fails closed unless this final record is written.

Routine Actions artifacts are forbidden and publication never mutates Flux or
Kubernetes state.

## Destination and immutable identities

The caller never supplies a destination. `oci.publish` preserves the reviewed
destination contract checked in with each product target:

- IPTV backend: `git.faruqi.dev/mimranfaruqi/iptv-backend`;
- Agent State: `git.faruqi.dev/mimranfaruqi/agent-state`;
- Flux Buildah runner: `git.faruqi.dev/mimranfaruqi/github-actions-runner-buildah`;
- Flux mobile runner: `git.faruqi.dev/mimranfaruqi/github-actions-runner-mobile`.

Every destination is a canonical lowercase repository identity without a tag,
digest, or `latest` component. All targets in one product must share exactly
one registry host. Authentication derives that host from these checked-in
repositories and fails closed if the product contract names multiple hosts.

### Server-side write-policy prerequisite

Client-side tag listing, second-look inspection, and replay checks cannot make a
registry tag immutable: a compromised or racing writer could still replace a
tag unless the registry itself enforces create-only tag writes. The current
checked-in Forgejo destinations do not yet have reviewed evidence of that
server-side guarantee, so every production and internal-smoke
`registry_write_policy` is deliberately `blocked`. Resolution fails with
`registry_write_policy_not_ready` before authentication or registry access;
changing `adoption_ready` alone cannot enable writes.

Enabling one product requires an exact `StreamScapeTV/flux` authority commit and
a `sha256` evidence identity proving
`server-side-create-only-tags-v1` on the exact `git.faruqi.dev` host for the
contract-owned destination. Only then may that closed policy record change to
`verified`. The policy ID, host, enforcement identity, Flux authority SHA, and
evidence ID are bound into authenticated plan state, publication evidence,
`immutable_references_json`, and final supply evidence. This contract records a
prerequisite; it does not claim that current Forgejo configuration satisfies it.

Every target uses exactly two tags:

- `<release-version>`;
- `sha-<40-hex-source-sha>`.

`latest` is never produced. Existing references are replay-safe only when their
raw registry manifest digests equal the exact local manifest digest. A conflict
fails before any write. Tag publication repairs only a missing member of an
otherwise matching immutable pair; verify-only requires the complete pair.

Before any target can be written, publication obtains one authenticated
`skopeo list-tags` response for every exact contract-owned repository. The
response must remain within the accepted response-size limit, name that
repository byte-for-byte, and contain valid, non-duplicate tags without a
case-ambiguous spelling of either requested identity. Each requested version
and source tag is therefore either exactly absent or present exactly once.
Malformed, authorization, transient, repository-alias, duplicate, and
case-ambiguous responses fail the complete multi-target preflight with zero
registry copies.

An existing listed tag is inspected and must return an exact raw manifest. A
listed tag that cannot be inspected fails closed. Before copying an absent tag,
publication immediately inspects that exact reference again; only an explicit
manifest/name-missing condition permits the write. Generic network/endpoint
“not found” errors are not treated as evidence that an immutable reference is
absent. After any copies, publication repeats authenticated exact tag listing
for every target repository as a second all-target barrier, requires both tags
to be present exactly once with exact case, and only then performs final digest
verification and records publication state.

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
workflow authenticates them only to the single contract-derived registry host.
The token is sent to `skopeo login` through stdin and is never placed in an argv,
output, evidence object, state JSON, or Actions artifact. The auth file is a
regular non-symlink file with owner-only permissions below the per-run
publication state root.

After login, authentication exclusively creates a closed `plan.json` snapshot
that binds the complete typed publication plan, exact product/source/version,
contract-owned registry host and repositories, publication capacity identity,
and a redacted identity of the exact auth-file bytes. It contains no credential
or auth-file/host filesystem path. Guarded and direct publish, independent
read-back, and final verification each read both files through bounded
owner-only, no-follow descriptors and require byte-for-byte canonical state
parity before any registry operation or publication-state consumption. Missing,
corrupt, permission-changed, substituted, or mismatched state fails closed.

Publication uses three marker-owned capacity leaves with one deterministic,
run-scoped token. The scratch leaf is fixed below
`/var/tmp/buildah/ciw-oci-publish-<token>`; graph and run leaves use the same
name below `/var/lib/containers/storage` and `/run/containers/storage`. These
parents are central runtime constants and must be the pre-provisioned runner
mounts. No workflow input, action input, CLI option, environment variable, or
caller path can select or replace them. Tests use an immutable internal root
object instead of weakening production path validation.

Every publication `skopeo login`, remote inspect, push, and independent
read-back copy runs in a fresh private mount namespace. The physical graph leaf
is bind-mounted into the publication-owned implicit-containers tree and that
tree is then bound over `/var/lib/containers`, confining containers/image's
implicit rootful cache to registered publication scratch. Skopeo receives a
closed child environment: publication-owned HOME, temp and XDG roots; the exact
0600 auth file; a 0600 registries configuration with no unqualified search and
disabled short names; and only bounded path, locale, and certificate variables
from the host. Ambient proxy, registry, auth, Docker, credential, and
caller-selected cache settings are not inherited.

Before either publication path reads a build layout or `result.json`, it
independently resolves the exact `oci-build`/`ciw-oci` allocation and validates
its fixed capacity parents plus all three ownership markers. Missing, corrupt,
or substituted build scratch, graph, or run state fails before local image
inspection, registry inspection, copy, or any remote write.

Cleanup independently removes all three publication leaves with descriptor-
relative, no-follow traversal. Marker mismatch or symlink/file substitution is
reported as a cleanup failure after the unsafe entry itself is removed when
possible; no substitution target is followed. Terminal residue requires the
fixed capacity parents to remain valid mounts and every scratch, graph, and run
leaf to be absent. The separate `oci.build` cleanup removes its own builders,
manifests, images, layouts, staged source, and caches; both domains must pass
their terminal cleanup and residue checks.

## Public outputs

The public API remains exactly:

- `result`;
- `manifest_digests_json` — canonical JSON target-to-manifest digest map;
- `platform_digests_json` — nested target/platform manifest, config, layer, and
  metadata proof;
- `immutable_references_json` — exact target repository/version/source refs plus
  exact base references, verified assertion evidence, registry-write policy,
  release identity and, for Flux products, canary/known-good/rollback
  identities.

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

The publication schema is closed recursively for these nested payloads. It
returns only the contract-owned registry-policy host; no caller-selected host,
auth-file path, credential, mutable ref, or cluster identity is returned.

## CIW and compatibility wrappers

The public facade exposes `ci_workflows.oci.publish` and
`ci_workflows.oci.read_back`. `ciw_oci` contains the bounded `oci publish`
adapter for plan/authenticate/publish/readback/verify/cleanup/residue/
final-evidence. The adapter and checked-in product/publication registries are
part of the reviewed contract surface and are validated together.

## Flux runner images

`flux-runner-images` uses the merged #16 inventory: exactly
`runner-buildah` and `runner-mobile`. They publish respectively to the exact
checked-in Forgejo destinations
`git.faruqi.dev/mimranfaruqi/github-actions-runner-buildah` and
`git.faruqi.dev/mimranfaruqi/github-actions-runner-mobile`.
`immutable_references_json.flux` carries only the checked-in canary,
previous-known-good, and rollback identities for later `flux.assets`
review-only handoff. Publication does not edit Flux manifests, select a live
runner image, reconcile a scale set, or receive Kubernetes/SOPS authority.

## Evidence boundary

Hermetic tests cover event trust, explicit absence handling, immutable replay and
conflict behavior, no-write verify-only, missing-reference repair on tag
publication, independent layout read-back, real #16 required/forbidden
filesystem assertions, token-stdin/auth-file permissions, private-helper
immutability, and cleanup. A live private-registry proof is claimed only when the
exact final candidate runs with authorized least-privilege credentials;
unit/mock evidence never substitutes for that proof.
