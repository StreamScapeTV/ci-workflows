# Exact-tag release orchestration

`release.orchestrate` is the central release contract for current StreamScapeTV image-and-chart products. It turns one exact stable product tag into independently verified immutable publications, a deterministic release manifest, an exact GitHub Release, and a bounded review request for Flux.

The workflow is release orchestration only. It does not reconcile Flux, install Helm charts, run `kubectl`, write live GitOps state, or receive Kubernetes/SOPS credentials.

## Registered public API

The `release.orchestrate` `1.0.0` workflow accepts exactly these inputs:

- `admitted_sha` — exact lowercase source commit SHA;
- `release_contract` — checked-in release contract alias;
- `release_tag` — exact canonical stable SemVer product tag, for example `1.4.2`;
- `release_version` — the same exact stable SemVer release version;
- `request_id` — caller idempotency identity for the reviewed handoff; and
- optional `target_id` — confirmation only, never a destination selector.

It accepts only the named secrets `registry_username`, `registry_token`, and `flux_handoff_token`. Its public outputs are `result`, `immutable_references_json`, `release_manifest_sha256`, `handoff_state`, and `request_id`.

The workflow requires the registered `release-orchestration` permissions: `actions: read` and `contents: write`. `contents: write` supplies the workflow-scoped GitHub token used to create or exactly verify the GitHub Release; there is no caller-supplied GitHub Release token.

## Supported release contracts

`contracts/releases.json` is the exact release map. Public aliases resolve only to these checked-in releases:

| Public `release_contract` | Release ID | Repository | Image product | Chart product |
| --- | --- | --- | --- | --- |
| `backend` | `iptv-backend` | `StreamScapeTV/iptv-backend` | `iptv-backend-image` | `iptv-backend-chart` |
| `agent-state` | `agent-state` | `StreamScapeTV/agent-state` | `agent-state-image` | `agent-state-chart` |
| `flux` | `flux-runner-assets` | `StreamScapeTV/flux` | `flux-runner-images` | current checked-in Flux runner chart product |

Callers cannot redirect a release alias to another repository, registry destination, product, runner, or Flux target. When `target_id` is supplied it must equal the fixed resolved release ID.

The final Helm binding contract distinguishes image-bound charts from chart-only assets. Backend and Agent State charts consume exact #17 OCI publication evidence. The Flux GitHub Actions runner chart has no image binding: its chart release omits both `image_digest` and `immutable_references_json`, while the independently published runner-image family remains part of the same release manifest and Flux handoff evidence.

## Thin producer caller

Producer repositories keep a thin tag caller. The central workflow reference must be a reviewed immutable `ci-workflows` commit, never a mutable privileged reference. Current product tags are canonical stable SemVer without a `v` prefix.

```yaml
name: Release
on:
  push:
    tags:
      - '[0-9]*.[0-9]*.[0-9]*'
permissions:
  actions: read
  contents: write
jobs:
  release_products:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-release.yml@<PINNED_CI_WORKFLOWS_SHA>
    with:
      admitted_sha: ${{ github.sha }}
      release_contract: backend
      release_tag: ${{ github.ref_name }}
      release_version: ${{ github.ref_name }}
      request_id: release:${{ github.run_id }}:${{ github.run_attempt }}
    secrets:
      registry_username: ${{ secrets.REGISTRY_USERNAME }}
      registry_token: ${{ secrets.REGISTRY_TOKEN }}
      flux_handoff_token: ${{ secrets.FLUX_HANDOFF_TOKEN }}
```

Agent State uses `release_contract: agent-state`; Flux runner assets use `release_contract: flux`. The contract alias changes product selection only through the checked-in release map.

## Exact tag and source authority

Product versions and tags are stable SemVer without a prefix, for example `1.4.2`. `release_tag` and `release_version` must be identical. Pre-release versions, malformed tags, mutable aliases such as `latest`, tag/version mismatches, source mismatches, and repository mismatches fail closed.

The shared release-tag authority supports two modes without exposing a new public mode selector:

- on a real tag-push context, `release.orchestrate` invokes the shared authority in its normal `tag-push` mode and takes the tag/version/source tuple only from the GitHub event and immutable tag resolution;
- outside tag context, the workflow invokes `existing-tag` replay with the exact public version/source tuple, and the shared authority independently requires the trusted same-repository default-branch `workflow_dispatch` caller with write permission.

A normalization step then requires the resolved tag/version/source to equal the public `release_tag`, `release_version`, and `admitted_sha`. The workflow therefore cannot turn a replay request into an arbitrary publication or make a normal tag push depend on replay authority.

The shared authority records the tag object SHA, peeled tag commit SHA, and exact admitted source SHA. This supports both lightweight and bounded annotated tags, including historical releases whose source commit is older than the current default branch.

The resolved authority mode and exact tag object/commit tuple are revalidated before the release gate, before image publication, before chart publication, and immediately before the GitHub Release write. Moving, deleting, or retargeting the tag after admission therefore fails the release.

## Bounded six-job graph

The public workflow stays within the repository readability limit of seven jobs and reusable depth one. It performs the conceptual release stages in six jobs:

1. `plan` resolves the checked-in release plan, admits either normal tag-push authority or trusted existing-tag replay, proves that authority matches the public request, resolves the OCI product plan, and selects one contract-owned publication runner;
2. `run_release_gates` revalidates the same authority and proves an exact clean detached source checkout;
3. `publish_images` executes the reviewed #17 OCI build/publication/read-back primitives and their unconditional cleanup/residue checks;
4. `publish_charts_or_assets` verifies #17 image evidence for release provenance, conditionally passes it to image-bound #18 Helm publication, and runs unconditional cleanup/residue checks;
5. `verify_and_record` normalizes evidence, verifies immutable identities, renders the deterministic manifest, revalidates the same tag authority, creates or exactly verifies the GitHub Release, and constructs the sanitized handoff; and
6. `request_handoff_and_finalize` submits only the reviewed handoff request and exposes the stable `Release / Verified products` terminal check.

The workflow deliberately composes #17/#18 named actions/scripts directly instead of nesting their public reusable workflows. That preserves their publication engines while respecting the repository-wide reusable-depth limit.

## Publication and remote read-back

Image publication reuses the #17 sequence: exact source rebuild, workflow-scoped registry authentication, immutable version/source publication, independent Skopeo read-back, manifest/platform/config/layer verification, and unconditional publication/build/workspace cleanup.

For image-bound charts, Helm publication consumes the exact #17 public `image_digest` and `immutable_references_json` pair. #18 validates source/version/product parity and derives digest-pinned `repository@sha256:<digest>` bindings internally. Supplying only one evidence input fails closed.

For a chart whose checked-in Helm binding contract has no image binding, both OCI evidence inputs are omitted from Helm publication. Supplying either one fails closed. This is the intended Flux GitHub Actions runner chart behavior; it does not remove the separately verified Flux runner-image family from release evidence.

Flux runner images may contain multiple targets. Every target digest is preserved; the release never collapses a multi-target family to one arbitrary digest.

Publication success is not deployment success. No #19 step performs Flux reconciliation or Kubernetes mutation.

## Replay and partial publication

Immutable publication replay is exact-match only:

- a remotely existing image/chart that independently reads back to the expected identity is accepted without rewriting it;
- a conflicting immutable identity fails closed;
- a trusted retry after image success/chart failure must reverify the already-published image before continuing; and
- release completion requires successful independent image and chart read-back.

Trusted existing-tag replay is not a second publication authority. It is limited by the shared default-branch/write-permission checks and must resolve the same immutable tag/source tuple as the original release.

No stage repairs a conflict by retagging, overwriting a mismatched immutable identity, or switching to `latest`.

## Deterministic release manifest

`contracts/release-manifest.schema.json` preserves the existing shared-workflow manifest contract while adding a strict product-release record. The canonical product manifest binds:

- release ID, repository, exact stable tag, stable version, source SHA, tag object SHA, and peeled commit SHA;
- source timestamp and exact central workflow SHA;
- hashes of the release workflow/function/schema/tool/runner surfaces;
- image product ID, complete digest map, immutable references, and bounded read-back evidence;
- chart product ID, remote chart digest, immutable references, and bounded read-back evidence;
- exact-match replay policy; and
- review-only Flux handoff intent.

The chart identity requires the separately supplied remote `chart_digest` to equal the digest embedded in Helm's immutable read-back record. When normalized chart evidence contains a `read_back` object, that object must exactly equal the immutable read-back payload before manifest or handoff construction.

The manifest is canonical sorted JSON addressed by SHA-256. Credential-bearing or arbitrary caller JSON is rejected as evidence.

## GitHub Release identity

The GitHub Release is keyed to the same exact canonical product tag, for example `1.4.2`, and the exact admitted source. Its body contains only the canonical release manifest plus its SHA-256 marker.

Creation is idempotent only for an exact match. Repository, tag, release name, body, draft state, and prerelease state must all agree. A concurrent create race is re-read and accepted only when the winner is that exact release. Any mismatch fails closed.

The automatic workflow token is used only in the release-recording job and is never placed in the manifest, public outputs, or Flux handoff.

## Review-only Flux handoff

`contracts/flux-handoff.schema.json` defines the producer-to-Flux payload. It is fixed to:

- `target_repository=StreamScapeTV/flux`;
- `requested_action=review-selection`;
- `mutation_authorized=false`; and
- `secrets_included=false`.

The handoff contains exactly the two immutable publication product identities for the release. The runtime revalidates the complete bounded handoff shape, exact product/digest/reference types, exact canonical GitHub Release URL, and secret-free content again immediately before cross-repository dispatch. A digest-valid payload with an added or unauthorized field is rejected rather than transmitted.

For `flux-runner-assets`, the payload also contains the checked-in canary, previous-known-good, and rollback identities returned by the OCI publication contract; those selection fields are forbidden for non-Flux releases.

The final job uses `flux_handoff_token` only to submit a fixed `release-selection-review` repository-dispatch request to `StreamScapeTV/flux`. The request includes the caller `request_id`, the handoff SHA-256, and the sanitized handoff JSON. It does not carry cluster credentials or authorize reconciliation; Flux-owned review/policy remains a separate boundary.

## Credentials, artifacts, and cleanup

Registry credentials appear only in the two publication jobs. The Flux handoff token appears only in the final handoff job. Product publication never receives Flux/Kubernetes/SOPS authority, and the handoff job never receives registry credentials.

Routine Actions artifact upload/download is forbidden. Evidence is represented through independently verified remote identities, the GitHub Release manifest, and the bounded handoff.

OCI publication cleanup removes and verifies publication auth/layout state, OCI build state, and the registered workspace under `if: always()`. Helm publication similarly removes and verifies package/credential/workspace state on every terminal path.

## Release helper commands

`scripts/ci/release.py` is the thin issue-#19 command adapter. Its relevant commands are:

```text
release.py plan
release.py runner-plan
release.py image-bindings
release.py evidence
release.py verify-publications
release.py manifest
release.py github-release
release.py handoff
release.py dispatch-handoff
release.py progress
```

All GitHub outputs are bounded single-line values. Malformed release identities, tag/source mismatches, runner-selector injection, registry redirects, digest conflicts, mutable references, secret-shaped evidence, unauthorized handoff fields, handoff digest mismatch, and missing handoff credentials fail closed.

## Integration boundary

The #19-owned workflow and release core do not modify #17/#18 resources or shared public-registration files. Final integration requires the #17/#18 publication primitives and their shared registrations to be present on the candidate `main` graph. `release.orchestrate` itself remains non-mergeable until the canonical public contract is switched from planned to implemented, its registered implementation-component references agree with the depth-one design, and exact-head validation is green.

After the serialized shared merge that introduces `flux-github-actions-runner-chart`, #19 updates only its own `contracts/releases.json` entry to that final product ID and sets `chart_requires_image_identity=false`; before that shared product exists, the current placeholder remains intentionally unchanged to keep the branch fail-closed against the present inventory.
