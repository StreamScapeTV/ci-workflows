# Exact-tag release orchestration

`release.orchestrate` is the central release contract for the current StreamScapeTV image-and-chart products. It turns one already-existing stable product tag into independently verified immutable publications, a deterministic release manifest, an exact GitHub Release, and a bounded review request for Flux.

The workflow is release orchestration only. It does not reconcile Flux, install Helm charts, run `kubectl`, write live GitOps state, or choose deployment policy.

## Supported releases

The checked-in source of truth is `contracts/releases.json`. It maps the current release IDs to the existing product inventory in `contracts/products.json`:

| Release ID | Repository | Image product | Chart product |
| --- | --- | --- | --- |
| `agent-state` | `StreamScapeTV/agent-state` | `agent-state-image` | `agent-state-chart` |
| `flux-runner-assets` | `StreamScapeTV/flux` | `flux-runner-images` | `flux-runner-chart-assets` |
| `iptv-backend` | `StreamScapeTV/iptv-backend` | `iptv-backend-image` | `iptv-backend-chart` |

Callers cannot redirect one release ID to another repository or arbitrary product ID. The release map is cross-checked against the central product inventory and fails closed on drift.

## Thin producer callers

Producer repositories keep only a thin trusted tag caller. The central workflow reference must be a reviewed immutable `ci-workflows` commit, never `main` or another mutable ref. The tag glob is only an event prefilter; the central release authority still requires exact stable SemVer before any publication.

The backend caller differs only by its checked-in release ID:

```yaml
name: Release
on:
  push:
    tags: ['[0-9]*.[0-9]*.[0-9]*']
permissions:
  contents: write
jobs:
  release:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-release.yml@<PINNED_CI_WORKFLOWS_SHA>
    with:
      release_id: iptv-backend
      release_mode: tag-push
    secrets:
      registry_username: ${{ secrets.RELEASE_REGISTRY_USERNAME }}
      registry_token: ${{ secrets.RELEASE_REGISTRY_TOKEN }}
      github_release_token: ${{ secrets.GITHUB_TOKEN }}
```

The Agent State caller uses the same permission and secret boundary with `release_id: agent-state`:

```yaml
jobs:
  release:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-release.yml@<PINNED_CI_WORKFLOWS_SHA>
    with:
      release_id: agent-state
      release_mode: tag-push
    secrets:
      registry_username: ${{ secrets.RELEASE_REGISTRY_USERNAME }}
      registry_token: ${{ secrets.RELEASE_REGISTRY_TOKEN }}
      github_release_token: ${{ secrets.GITHUB_TOKEN }}
```

The Flux infrastructure caller publishes runner images and chart assets but does not reconcile the cluster. It uses `release_id: flux-runner-assets`; the resulting canary/known-good/rollback identities are carried only in the reviewed handoff:

```yaml
jobs:
  release:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-release.yml@<PINNED_CI_WORKFLOWS_SHA>
    with:
      release_id: flux-runner-assets
      release_mode: tag-push
    secrets:
      registry_username: ${{ secrets.RELEASE_REGISTRY_USERNAME }}
      registry_token: ${{ secrets.RELEASE_REGISTRY_TOKEN }}
      github_release_token: ${{ secrets.GITHUB_TOKEN }}
```

A trusted historical replay calls the same immutable workflow with `release_mode: existing-tag`, the stable `release_version`, and the exact `release_source_sha`. The resolver accepts it only when the existing tag still peels to that exact source. Ordinary `workflow_dispatch` is not a bypass for publication authority.

## Version and source authority

Product release versions are stable SemVer without a `v` prefix, for example `1.4.2`. Pre-release versions and mutable aliases such as `latest` are rejected by this orchestration layer because the current OCI publication contract accepts stable release versions only.

The default authority mode is a tag-push release. Explicit trusted replay may resolve an existing tag, but it is still the exact same immutable release identity. The tag resolver records and propagates:

- release version;
- tag object SHA;
- peeled tag commit SHA;
- exact admitted source SHA; and
- source timestamp.

Every privileged publication boundary must revalidate that the same tag still resolves to the same object and commit before it writes to a registry. A tag move, source mismatch, missing tag, or ambiguous authority fails the release.

## Publication order and immutable image binding

The release path is deliberately ordered:

1. resolve exact tag/source authority;
2. validate the checked-in release and product contracts;
3. run release gates on the exact source;
4. publish and independently read back OCI images;
5. derive digest-pinned image identities from the OCI read-back;
6. publish charts/assets using those digest-pinned image identities;
7. independently read back the chart publication;
8. normalize and verify the publication evidence;
9. create the deterministic release manifest;
10. create or exactly verify the GitHub Release;
11. emit the bounded Flux selection request; and
12. always run terminal cleanup/status projection.

The OCI publication API returns its exact target-to-digest map plus a bounded immutable reference description. `release.py image-bindings` verifies target parity, exact repository ownership, stable version references, source-SHA references, and remote manifest digests. It then derives chart-facing identities in the form:

```text
ghcr.io/streamscapetv/<repository>@sha256:<digest>
```

Charts must consume the verified digest-pinned identities produced by the same release. Reading a mutable version tag from source is not an equivalent proof.

Flux runner images may contain several publication targets. The release manifest and handoff preserve every target digest. A multi-target family is never collapsed to an arbitrary single image digest.

## Remote read-back and replay

Publication success means the independently fetched remote object matches the expected immutable identity. Local build/package success by itself is not release success.

Replay is exact-match only:

- if a remote image/chart already exists and independently reads back to the expected immutable identity, the publication stage may return success without rewriting it;
- if the same release identity exists with different content, the release fails closed;
- if image publication completed but chart publication did not, the state is reported as `image-published-awaiting-chart` and a trusted retry verifies the already-published image before continuing;
- a release is `complete` only after both image and chart publication read-back succeed.

No stage repairs a conflict by retagging, overwriting a mismatched immutable identity, or switching to `latest`.

## Release manifest

The canonical JSON release manifest uses `contracts/release-manifest.schema.json`. Product releases extend the existing shared-workflow manifest without changing the legacy root required-field contract.

The product release record binds at least:

- release ID and repository;
- stable version;
- exact source SHA;
- tag object SHA and peeled tag commit SHA;
- source timestamp;
- exact central workflow SHA;
- hashes of the release workflow/function surface and checked-in schemas;
- action/tool/runner-profile hashes;
- image product ID, exact target digest map, immutable references, and bounded read-back evidence;
- chart product ID, digest, immutable references, and bounded read-back evidence;
- exact-match replay policy;
- GitHub Release requirement; and
- bounded Flux handoff intent.

The manifest is serialized as canonical sorted JSON and addressed by its SHA-256. Arbitrary credential-bearing runtime blobs are not accepted as publication evidence.

## GitHub Release behavior

The GitHub Release is keyed to the same stable version and admitted source. Its body contains the canonical release manifest and a manifest SHA-256 marker.

Creation is idempotent only for an exact match. An existing release must match the expected repository, tag, release name, canonical body, draft state, and prerelease state. A concurrent create race is re-read and accepted only if the winner is that exact release. Any conflicting release fails closed.

The GitHub release token is consumed only by the GitHub Release stage. It is not placed in the release manifest, Flux handoff, job outputs, or logs.

## Flux handoff

`contracts/flux-handoff.schema.json` defines the only producer-to-Flux payload generated by release orchestration. It is a `flux-selection-request` directed at `StreamScapeTV/flux` with `requested_action=review-selection`.

The handoff contains only bounded immutable release identities:

- producer and target repositories;
- release ID and stable version;
- exact source SHA;
- release-manifest SHA-256;
- exact GitHub Release URL;
- image/chart product IDs;
- exact digest maps and immutable references; and
- for `flux-runner-assets`, the checked-in canary, previous-known-good, and rollback selection identities returned by the OCI product contract.

The payload fixes both `mutation_authorized=false` and `secrets_included=false`. It cannot carry Kubernetes credentials, Flux credentials, registry credentials, reconciliation commands, or live deployment mutations.

For `flux-runner-assets`, all three selection identities are mandatory. They are omitted and rejected for non-Flux product releases.

## Credentials, runners, and cleanup

Reusable publication workflows receive only their explicit named registry credentials. Release orchestration does not use broad secret inheritance. Read-only planning/verification jobs do not receive registry credentials.

The GitHub release credential is a separate named secret and is consumed only by the release-metadata job. No Flux cluster or dispatch credential enters product publication jobs.

General Linux control-plane work runs on the semantic general profile from `RUNNERS.md`. Publication workflows retain their own product-specific runner selection.

Routine GitHub Actions artifact upload is forbidden. Release evidence is stored in the immutable remote publications, GitHub Release manifest, and bounded handoff instead of ordinary workflow artifacts.

Credential-bearing and publication workspaces must be removed on all terminal paths. Cleanup/residue verification runs even when a preceding publication step fails.

## Local contract tools

Issue-#19 release helpers intentionally use a standalone adapter (`scripts/ci/release.py`) so this parallel implementation does not modify shared `ciw` command registration.

Useful commands are:

```text
release.py plan
release.py image-bindings
release.py evidence
release.py verify-publications
release.py manifest
release.py github-release
release.py handoff
release.py progress
```

All GitHub Actions outputs emitted by this adapter are bounded single-line values. The helpers reject malformed release IDs, source identities, registry redirects, digest conflicts, mutable references, secret-shaped evidence, and unauthorized handoff fields rather than trying to infer or repair them.

## Integration invariant

The final `reusable-release.yml` must compose the registered publication interfaces rather than their private implementation details. In particular, the Helm publication boundary must accept the digest-pinned image identity derived from the OCI read-back and must preserve/revalidate the same exact tag authority immediately before its registry write. The release workflow is not complete until those dependency interfaces are available and its exact-head contract tests verify the composition.
