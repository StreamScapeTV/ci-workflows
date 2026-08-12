# Flux infrastructure assets

`flux.assets` centralizes orchestration for Flux-owned runner images and runner
chart assets while leaving desired state and cluster authority in the Flux
repository. It accepts only an exact admitted Flux SHA, a checked-in product ID,
an immutable release version, a bounded operation, a checked-in non-secret
policy path, and a request ID. Callers cannot choose a registry destination,
container engine, concrete runner labels, Kubernetes target, namespace, secret
name, upstream URL, or shell command.

## Current live inventory

Issue #33 follows the reviewed organization inventory and the live Flux tree.
The current custom runner image products are exactly:

- `images/github-actions-runner-buildah`;
- `images/github-actions-runner-mobile`.

The portable ARC class is not a third custom image product: it currently uses
the upstream Actions runner image. The confirmed runner chart is
`apps/github-actions-runner`, currently version `1.3.0` with ARC app version
`0.14.2`. Its two mirrored upstream chart assets are
`gha-runner-scale-set-controller` and `gha-runner-scale-set`, both at `0.14.2`.
These identities live in `contracts/flux-infrastructure-products.json`, not in
workflow YAML.

## Operations

`plan` is read-only. It validates the product, source identity, policy path, and
contract-owned runner/workspace selection, then returns a deterministic plan and
review-only handoff identities. It needs no registry credential and performs no
publication.

`release` is reserved for an exact tag context. It must consume successful,
independently verified outputs from the registered OCI or Helm dependency APIs.
The current issue-#33 parallel implementation intentionally fails closed until
those dependency outputs are wired after issues #16-#18 integrate; it never
substitutes branch-private implementation details or reports publication that
did not run.

`verify-only` reuses the immutable publication/read-back interfaces without
creating a new live selection. It still requires exact dependency evidence and
never receives Kubernetes or SOPS credentials.

## Bootstrap and self-hosting safety

Runner-image replacement cannot depend on the unverified candidate to build or
verify itself. The checked-in product contract selects the known-good builder
profile. Both the known-good builder reference and candidate reference must be
digest-pinned and distinct.

Every effective image base is required to be digest-pinned. Current Flux image
sources still contain mutable base tags; therefore a real #33 source proof will
correctly fail the source contract until the Flux adoption/update records exact
base digests. This is intentional fail-closed behavior rather than a reason to
weaken the central contract.

The Buildah product requires the reviewed daemonless toolchain and rejects
Docker/Dockerd, Docker sockets, Kubernetes tooling, service-account tokens,
KUBECONFIG, and credential residue. The Mobile product binds the exact reviewed
runner/JDK/Flutter/Dart/Node/Android/NDK versions and applies the same forbidden
credential/cluster boundary.

## Chart asset safety

The two ARC chart inputs must retain the reviewed upstream repository, exact
version, an immutable content digest, MIT license attribution, and evidence that
no unreviewed template mutation occurred. Chart validation/publication is
performed through the shared Helm dependency APIs and never installs a chart or
contacts Kubernetes.

## Immutable identity, replay, and read-back

Image and chart publication dependencies return only their registered outputs.
`flux.assets` validates the required output set, successful result, digest
shape, immutable reference payload, and absence of `latest`. Replay is accepted
only when every immutable identity and digest matches exactly; partial identity
sets or conflicting content fail closed.

The canonical infrastructure manifest is hashed with SHA-256. The public
outputs are exactly:

- `result`;
- `immutable_references_json`;
- `release_manifest_sha256`;
- `request_id`.

No host identity, registry credential, Kubernetes object, or decrypted value is
part of the output surface.

## Canary, selection, and rollback

Successful verification produces a bounded Flux handoff with contract-owned
canary, previous-known-good policy, and rollback identities. It explicitly sets
`review_required=true`, `canary_required=true`, and
`mutation_authorized=false`. Publication success never means the live ARC scale
set should change.

Flux remains responsible for the separate reviewed desired-state change,
canary/health acceptance, selection policy, and rollback decision. The previous
known-good image/chart remains the rollback authority until Flux policy replaces
it after review.

## Cleanup and artifacts

The reusable workflow uses issue-owned transient state under `RUNNER_TEMP`,
removes it through `if: always()`, verifies zero residue, asserts the exact Flux
checkout stayed clean, and retains zero routine GitHub Actions artifacts.
Registry credentials belong only to the shared publication dependencies and are
not accepted by the thin `actions/flux-assets` adapter itself.

## Integration boundary

Issue #33 owns its inventory contract, runtime, thin adapter, workflow, tests,
and documentation. Shared public/CIW/bootstrap/action-lock registration remains
a separate integration handoff. The public contract also names
`internal-flux-assets.yml`; if that exact shared/leaf resource is unavailable to
the issue owner, Central will keep that explicit finding until ownership is
transferred rather than accepting an unowned replacement.
