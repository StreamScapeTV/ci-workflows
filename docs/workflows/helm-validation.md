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

## Product-owned policy hooks

`policy_path` is not a caller-selected command. `contracts/helm-policy-hooks.json`
centrally binds the one optional checked-in `.sh` hook path for each product;
all current products are explicitly `null`. A caller manifest that introduces
or changes a hook without a central contract update fails before validation.

For a future approved hook, the workflow first completes ordinary Helm
validation on the isolated chart copy, then runs exactly `bash --noprofile
--norc <approved-hook>` with that isolated chart as the working directory.
The hook receives only bounded `CIW_HELM_*` path/product/version variables plus
the normal scrubbed Helm runtime. Registry credentials, `KUBECONFIG`, SOPS or
other caller environment are not propagated. Exact source cleanliness is
verified before and after the hook. Validation, exact-tag release validation,
and the named Helm façade all traverse this same hook boundary.

## Deterministic package canonicalization

Helm first operates on an isolated chart copy. The resulting package is then
recursively canonicalized by `ci_workflows.helm_archive` before it becomes the
validation/publication identity. The canonicalizer normalizes gzip metadata,
tar order, timestamps, ownership, names, and modes for the outer chart and for
every packaged subchart under `charts/*.tgz`.

Packaged dependencies are not opaque bytes: nested chart members receive the
same path/type bounds and secret/token scanning as the outer package. Symlinks
and other non-file/non-directory entries fail closed. This prevents regenerated
dependency archives from carrying unstable gzip/tar metadata or hiding
credential-like content inside an otherwise deterministic outer archive.

The recursively canonical package SHA-256 is the local package identity used
for replay comparison and the expected Helm OCI content-layer digest.

## Flux wrapper provenance

The Flux wrapper has no application-image binding, but it references two
mirrored ARC charts. The product manifest therefore carries a separate
`upstream_assets` provenance list for `gha-runner-scale-set` and
`gha-runner-scale-set-controller`.

`contracts/helm-upstream-policy.json` centrally fixes the stable origin facts
that are independently verifiable for this release family: the official
`actions/actions-runner-controller` GitHub repository, version `0.14.2`, exact
upstream source commit `9bb16ae49d0ce585d8e682aa7e2668a6e832d5d8`,
Apache-2.0 license, and the two approved private mirror repositories. Caller
source cannot substitute a fork, different version/commit/license, or another
mirror destination.

Flux remains responsible for recording the remaining immutable producer facts:
the upstream chart-content digest, independently verified mirror chart-content
digest, and reviewed patch list. The current central fixture uses synthetic
chart digests only to exercise the validation path; those are not accepted as
live Flux evidence. For the currently supported unpatched case, upstream and
mirror digests must match exactly and the patch list must be empty.

Live Flux has not yet checked in the complete digest tuple or bounded Helm
product manifest; that producer adoption is tracked by `StreamScapeTV/flux#306`.
Missing live provenance is a hard validation failure, not a reason to infer or
invent registry content identities.

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
read-back, and a second independent raw manifest inspection with Skopeo. A
failed first pull is treated as proof of absence only when the registry returns
the standard `MANIFEST_UNKNOWN` (including Helm's `manifest unknown`
rendering) or `NAME_UNKNOWN` error code. Generic `404 Not Found`,
authentication, proxy, network, or other lookup failures are
`registry_lookup_failed` and never become a `helm push` attempt. A failed
absence lookup must also leave the destination directory empty before
publication is allowed.

For `chart_digest`, `skopeo inspect --raw` supplies the exact fetched manifest
bytes without converting the Helm artifact through image inspection. #18
hashes those exact bytes with SHA-256, parses the same bytes to require the Helm
OCI config media type, requires exactly one Helm chart content layer, and
requires that layer digest to equal the recursively canonical local package
SHA-256. The public `chart_digest` is this exact raw remote-manifest digest,
while `chart_package_sha256` remains the separate package-content identity.

Tag-push replays and manual verify-only replays succeed only on exact package
and manifest parity; conflicting immutable versions fail closed. Manual replay
never creates a missing version.

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
