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

The publish workflow resolves the exact tag object and tag commit in its plan
job and requires the resolved release source/version to match the request. It
revalidates the same tag object and commit immediately before registry
credentials are passed to the publication action. A moved tag therefore fails
before registry write.

Publication uses pull-compare-before-push, mandatory Helm pull read-back, and a
second raw OCI manifest inspection with Skopeo. Public `chart_digest` is the
SHA-256 of the exact remote Helm OCI manifest bytes. Package content remains a
separate `chart_package_sha256`, also included in Helm's
`immutable_references_json`. Replays succeed only on exact package parity and
conflicting immutable versions fail closed.

Both Helm workflows use isolated temporary state and unconditional cleanup.
They retain zero routine Actions artifacts. Helm publication rejects Kubernetes
authority and never installs/upgrades charts, reconciles Flux, or decrypts SOPS
values.

Shared public registration, generated references, CIW/bootstrap registration,
and runner policy remain a serialized integration lane. That owner must
register optional `image_digest` and `immutable_references_json` inputs for
`helm.publish`, replace the stale Flux chart product placeholder with
`flux-github-actions-runner-chart`, and move publication to the smallest
measured Skopeo-capable Buildah semantic tier. This Helm-exclusive branch does
not edit those shared files.
