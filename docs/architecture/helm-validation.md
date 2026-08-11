# Helm validation and OCI chart publication

Issue #18 introduces a product-contract Helm family.  The public caller may
select only `product_id`, an optional checked-in values profile, and an exact
release version.  It cannot select a chart root, registry, upstream, command,
runner, secret name, cluster, namespace, or container engine.

Each admitted product source supplies `.streamscape/helm-product.json`.  The
central `contracts/helm-validation.json` binds that manifest to one known
repository, chart name, and fixed `oci://git.faruqi.dev/mimranfaruqi/helm-charts`
destination.  The manifest supplies an ordinary relative chart root, exact
values-profile paths, optional fixed policy path, locked dependency triples,
and any image digests that rendered output must contain.  Every path is
descriptor-checked below the exact checkout; symlinks and traversal fail.

Validation checks Helm `v3.18.6`, `Chart.yaml`, optional `values.schema.json`,
templates, CRDs, values, and exact `Chart.lock` dependency tuples.  It runs only
`helm dependency build`, `helm lint --strict`, and `helm template --include-crds`
with isolated Helm home/cache/data state.  If image references are specified,
each rendered `image:` must be immutable and each required digest must appear.

`helm package` output is rewritten into a deterministic gzip/tar archive with
sorted names, epoch timestamps, normalized ownership and modes.  It rejects
links, absolute or traversal members, source-control/junk files, credential
suffixes, and token-like non-template content.  The archive lives only under
registered temporary state and is removed by both Helm-specific and shared
terminal cleanup.  Routine workflow output retains zero Actions artifacts.

Publication accepts only canonical SemVer—not `latest`—and named registry
credentials.  It attempts an OCI pull first: an existing byte-identical package
is idempotent, while different immutable content fails.  Only a missing version
is pushed, followed by an authenticated OCI pull and normalized SHA-256
comparison.  This workflow never installs or reconciles a chart, accesses
Kubernetes, or decrypts SOPS data.
