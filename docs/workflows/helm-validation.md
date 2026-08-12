# Reusable Helm workflows

`.github/workflows/reusable-helm-validate.yml` is the read-only
`helm.validate` implementation. It accepts the reserved validation inputs and
derives `untrusted-fork`, `trusted-pr`, or `trusted-exact` from the caller
event; callers cannot select trust, a runner, registry, or command.

`.github/workflows/reusable-helm-publish.yml` is the exact-tag
`helm.publish` boundary. In addition to exact admitted source, product, version,
and checked-in values/policy selectors, it requires
`required_image_references_json`: a sorted duplicate-free JSON array of the
exact `repository@sha256:<64-hex>` identities already published and read back
by issue #17. `[]` is accepted only for a product whose central release-binding
contract has no image.

The publish workflow resolves the exact tag object/commit in its plan job,
requires the resolved source/version to match the requested tuple, checks out
that exact source, and revalidates the same tag object/commit immediately before
registry publication. Registry credentials are not passed until after that
revalidation.

Release image identities are applied only to an isolated chart copy according
to `contracts/helm-release-bindings.json`. Every rendered workload image must
be immutable and every required image reference must render. The source
checkout remains byte-for-byte clean. The current Agent State chart has no
digest values path and therefore intentionally fails closed until producer-side
adoption adds immutable digest rendering.

Publication does pull-compare-before-push, mandatory pull read-back, and a
second raw OCI manifest inspection. Public `chart_digest` is the SHA-256 of the
exact remote Helm OCI manifest bytes; package content remains separately
identified by `chart_package_sha256` and is also recorded inside
`immutable_references_json`. Replays succeed only on exact package parity;
conflicts fail closed.

Both workflows use isolated state and unconditional Helm-specific plus shared
workspace cleanup. No package, rendered output, registry state, cache, or
diagnostic Actions artifact is retained. Publication rejects Kubernetes
authority and never runs `helm install`, `helm upgrade`, `kubectl`, Flux
reconciliation, or SOPS decryption.

Shared registration, generated references, CIW/bootstrap wiring, and runner
policy remain a serialized integration lane. That lane must add the new public
image-reference input, update the Flux product ID, and select the smallest
measured Buildah-capable semantic profile for `helm.publish`; this Helm-only
slice intentionally does not edit those shared files.
