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

## Operations and caller trust

`plan` is read-only. It validates the product, source identity, policy path, and
contract-owned runner/workspace selection, then returns a deterministic plan and
review-only handoff identities. It needs no registry credential and performs no
publication.

`release` is tag-push only. The caller event must be `push`, the ref type must be
`tag`, and the ref name must be the exact requested version (with or without the
reviewed `v` prefix). It must consume successful, independently verified outputs
from the registered OCI or Helm dependency APIs. The current issue-#33 recovery
implementation intentionally fails closed until those dependency outputs are
wired after issues #16-#18 integrate; it never substitutes branch-private
implementation details or reports publication that did not run.

`verify-only` is manual default-branch only. It requires `workflow_dispatch`, a
branch ref, and the caller repository's exact default branch name. It consumes
immutable publication/read-back outputs but authorizes no publication repair,
new live selection, desired-state mutation, Kubernetes access, or SOPS access.

The reusable workflow checks out its own central implementation by the called
workflow identity (`job.workflow_repository` and `job.workflow_sha`), not by the
caller-associated `github.workflow` identity. Caller event/ref/default-branch
metadata is passed only to the internal typed guard and does not expand the
public input surface.

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
KUBECONFIG, and credential residue. Its strict runtime proof also requires the
reviewed OCI labels, subordinate-ID configuration, and `vfs` storage driver. The
Mobile product binds the exact reviewed runner/JDK/Flutter/Dart/Node/Android/NDK
versions and applies the same forbidden credential/cluster boundary plus its
required OCI labels.

## Chart asset safety

The two ARC chart inputs must retain the reviewed upstream repository, exact
version, an immutable content digest, MIT license attribution, and evidence that
no unreviewed template mutation occurred. Chart validation/publication is
performed through the shared Helm dependency APIs and never installs a chart or
contacts Kubernetes.

The Helm publication adapter preserves the exact immutable chart reference,
remote `chart_digest`, and normalized package SHA-256 returned by #18. The chart
reference must be an immutable OCI version matching the requested release; a
mismatched digest/version or `latest` fails closed.

## Immutable identity, replay, and read-back

Image and chart publication dependencies return only their registered outputs.
`flux.assets` validates the exact dependency set for the selected operation,
success state, digest shape, immutable reference payload, and absence of
`latest`. For OCI publication it also requires:

- the exact admitted source SHA and release version;
- exactly the two live runner-image targets;
- target repository/version/source/digest parity with `image_digest`;
- preserved nested per-platform manifest/config/layer read-back evidence;
- exact contract-owned canary, previous-known-good, and rollback identities.

Replay is accepted only when every immutable identity and digest matches
exactly; partial identity sets or conflicting content fail closed. Nested OCI
platform evidence is retained in the canonical manifest rather than flattened
to a lossy digest map.

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

## Internal composition leaf

`.github/workflows/internal-flux-assets.yml` is the bounded composition leaf
named by the public contract. It accepts only exact product and identity data
plus dependency evidence; it cannot call another reusable workflow and runs on
ordinary `[linux, amd64, general]` capacity because it performs only
source/evidence validation and composition. OCI/Helm publishers retain their
own contract-selected Buildah runners.

The public workflow does not call this leaf yet. GitHub permits only a restricted
set of keywords on a job that calls a reusable workflow, while the current
repository harness requires a timeout on every job, including reusable-call
jobs. Keeping the leaf staged but not yet called avoids committing a workflow
that GitHub rejects. Final shared integration must reconcile that harness/call
site rule before nesting the public workflow.

## Cleanup and artifacts

Issue-owned transient state lives directly under `RUNNER_TEMP`, is removed
through `if: always()`, and is followed by residue verification. Both public and
internal workflows assert the exact Flux checkout stayed clean and query the
current run artifact inventory to prove zero routine GitHub Actions artifacts.
Registry credentials belong only to shared publication dependencies and are not
accepted by the thin `actions/flux-assets` adapter itself.

## Integration boundary

Issue #33 owns its inventory contract, runtime/guards, thin adapter, public and
internal workflows, tests, and documentation. Shared public/CIW/bootstrap and
unrelated registration surfaces remain serialized integration work. The
issue-exclusive recovery checkpoint does not call unfinished #16/#17/#18
branch-private workflows; once those dependencies integrate, final #33 wiring
passes their registered outputs into the already-bounded composition layer.
