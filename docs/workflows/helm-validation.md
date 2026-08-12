# Reusable Helm workflows

`.github/workflows/reusable-helm-validate.yml` is the read-only
`helm.validate` implementation. It derives `untrusted-fork`, `trusted-pr`, or
`trusted-exact` from the caller event and gives callers no registry, runner,
engine, cluster, or command-selection surface.

`.github/workflows/reusable-helm-publish.yml` is the exact-tag `helm.publish`
boundary. For application charts it accepts issue #17's public
`image_digest` and `immutable_references_json` outputs **verbatim**. Release
orchestration does not construct an intermediate image-reference array or add
an adapter job. Both inputs are optional at the workflow syntax boundary only
because the current Flux wrapper chart has no workload-image binding; a chart
with an image binding requires both, while a no-image chart requires both to be
omitted.

`contracts/helm-release-bindings.json` binds each application chart to its
issue #16/#17 OCI product target, canonical published repository, historical
source-chart repository value, and fixed values keys. Before packaging, #18
checks that the OCI evidence has the exact admitted source SHA and release
version, exactly the expected target set, the expected canonical repository,
exact version and `sha-<source>` identities, and the same independently
read-back manifest digest in both #17 outputs. It then derives the exact
`repository@sha256:<64-hex>` reference internally.

The derived repository and digest are written only into an isolated chart
copy. Every rendered workload image must be digest-pinned and every required
reference must appear in the rendered output. The caller checkout remains
clean. The current Agent State chart still lacks an `image.digest` value path,
so it intentionally fails closed until producer-side adoption adds immutable
digest rendering; #18 never falls back to a mutable tag.

## Central dependency policy

A caller manifest may describe its locked dependency tuple, but it does not own
the network destination used by `helm dependency build`.
`contracts/helm-dependency-policy.json` centrally fixes the exact dependency
set for each admitted chart product. The current backend tuple is exactly
`valkey` `0.11.0` from `https://valkey.io/valkey-helm/`; Agent State and the
Flux wrapper currently admit no Helm dependencies through this mechanism.

The production Helm planners compare the caller manifest's parsed dependency
tuples to that central policy before any dependency build. A fork therefore
cannot redirect validation to another HTTPS or OCI repository by editing its
manifest, `Chart.yaml`, and lock data together. Credential-bearing or malformed
central repository entries also fail closed.

## Flux wrapper provenance

The Flux wrapper has no application-image binding, but it references two
mirrored ARC charts. The product manifest therefore carries a separate
`upstream_assets` provenance list for `gha-runner-scale-set` and
`gha-runner-scale-set-controller`.

Each row must contain the exact upstream GitHub repository, stable tag, full
upstream commit SHA, upstream chart digest, SPDX-style license, exact approved
private mirror repository, mirror chart digest, and reviewed patch list. The
current contract admits only unpatched mirrors: patch lists must be empty,
upstream and mirror digests must match, and both tags must equal the wrapper
`Chart.yaml` `appVersion`.

The checked-in test fixture uses deliberately synthetic upstream hashes and
source identity. Live Flux has not yet recorded the full immutable tuple; that
producer adoption is tracked by `StreamScapeTV/flux#306`. Missing provenance is
a hard validation failure, not a reason to infer facts from the mutable tag.

## Publication trust and runner selection

For an actual tag-push release, the publish workflow resolves exact `tag-push`
authority and may write only the missing immutable chart version. For a trusted
same-repository default-branch `workflow_dispatch`, it instead resolves
`existing-tag` authority from the explicit source/version tuple. That manual
path is verification-only: it can authenticate and read back an existing
immutable chart, but a confirmed missing version fails with
`remote_version_missing` and can never become a `helm push`.

Both modes bind the requested source/version to the resolved tag object and tag
commit, then revalidate that same object/commit immediately before registry
access. A moved tag therefore fails before either publication or replay
verification reaches the registry action. The resolved release mode is passed
internally through the composite action and is not a caller-selectable public
input.

Validation remains on semantic `portable`. Publication needs Skopeo for the
independent registry manifest proof, so the central runner resolver maps
`helm.publish` to trusted-exact `buildah-tiny` as the smallest Skopeo-capable
measurement candidate. Callers cannot select that profile or its concrete ARC
labels.

The candidate tier is not final evidence by itself. Before #18 becomes a final
candidate, exact-head publication evidence must record real peak memory and
local-storage bytes for the workflow/product/source and prove they fit the
selected tier with the reviewed headroom in `contracts/helm-publication.json`.
If not, the central Buildah escalation policy selects the next sufficient tier.

## Publication and read-back

Tag-push publication uses pull-compare-before-push, mandatory Helm pull
read-back, and a second raw OCI manifest inspection with Skopeo. A failed first
pull is treated as proof of absence only when the registry returns the standard
`MANIFEST_UNKNOWN` (including Helm's `manifest unknown` rendering) or
`NAME_UNKNOWN` error code. Generic `404 Not Found`, authentication, proxy,
network, or other lookup failures are `registry_lookup_failed` and never become
a `helm push` attempt. A failed absence lookup must also leave the destination
directory empty before publication is allowed.

Public `chart_digest` is the SHA-256 of the exact remote Helm OCI manifest
bytes. Package content remains a separate `chart_package_sha256`, also included
in Helm's `immutable_references_json`. Tag-push replays and manual verify-only
replays succeed only on exact package parity; conflicting immutable versions
fail closed. Manual replay never creates a missing version.

Privileged publication execute has exactly one entry point: the release-only
adapter that binds tag authority and OCI evidence. The generic `ciw helm
publish` surface may plan and perform cleanup/residue checks, but its `execute`
phase fails with `release_adapter_required` before caller source or packaging is
reached. This prevents direct CIW invocation from bypassing release mode or OCI
evidence admission.

Both Helm workflows use isolated temporary state and unconditional cleanup.
They retain zero routine Actions artifacts. Helm publication rejects Kubernetes
authority and never installs/upgrades charts, reconciles Flux, or decrypts SOPS
values.

## Final shared registration

The final integrated head must register optional `image_digest` and
`immutable_references_json` inputs for `helm.publish`, replace the stale Flux
chart product placeholder with `flux-github-actions-runner-chart`, keep CIW and
generated public references synchronized, and keep the runner contract,
generated mapping, and compatibility report aligned with the measured
Skopeo-capable publication tier. These shared surfaces are serialized by Agent
State ownership and must not be overwritten merely because the issue branch
contains older historical edits.
