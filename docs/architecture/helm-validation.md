# Helm validation and OCI chart publication

Issue #18 defines one product-contract Helm family for the chart producers that
exist today. Callers may select only a central `product_id`, an optional
checked-in values profile/policy path, an exact admitted source SHA, and (for a
release) an exact SemVer. They cannot select chart roots, dependency
repositories, registries, runner labels, commands, secret names, clusters,
namespaces, service accounts, or container engines.

## Current producer inventory

The contract is intentionally aligned to live producer identities:

- `iptv-backend-chart` -> `StreamScapeTV/iptv-backend`,
  `charts/iptv-backend`, chart `iptv-backend`. The current chart has a locked
  Valkey dependency from `https://valkey.io/valkey-helm/`, so locked dependency
  repositories admit bounded credential-free HTTPS as well as OCI.
- `agent-state-chart` -> `StreamScapeTV/agent-state`, `charts/agent-state`,
  chart `agent-state`. Its checked-in development metadata is `0.0.0`; release
  version binding must therefore happen while packaging an isolated copy.
- `flux-github-actions-runner-chart` -> `StreamScapeTV/flux`,
  `apps/github-actions-runner`, chart `github-actions-runner` (wrapper version
  `1.3.0`, ARC app version `0.14.2` at the inventory checkpoint).

Flux also mirrors the upstream ARC
`gha-runner-scale-set-controller:0.14.2` and
`gha-runner-scale-set:0.14.2` OCI charts. Flux records the exact version and
mirror destinations, but does not yet record the complete upstream commit,
chart digest, and license tuple required by #18. The central workflow does not
invent those facts; complete mirrored-upstream provenance remains a Flux
product/adoption requirement.

## Source and chart admission

Each admitted product source supplies `.streamscape/helm-product.json`. The
central `contracts/helm-validation.json` binds the manifest to one known
repository, chart name, and fixed
`oci://git.faruqi.dev/mimranfaruqi/helm-charts` destination. The manifest
supplies ordinary relative chart paths, checked-in non-secret values profiles,
optional fixed policy paths, locked dependency triples, and baseline image
assertions.

All paths are descriptor-checked below the exact checkout. Symlinks anywhere
inside the chart tree, traversal, credential-bearing dependency URLs, or
dependency/lock mismatch fail closed. HTTPS dependency URLs may not contain
userinfo, query strings, or fragments.

Validation accepts explicit `untrusted-fork`, `trusted-pr`, or `trusted-exact`
source trust. Publication is additionally constrained by exact release-tag
authority and therefore cannot become a PR publication path.

## Exact image binding from OCI publication

The release workflow does not trust a mutable tag embedded in chart source and
does not infer the image from `Chart.appVersion`. Its required public input is
`required_image_references_json`: a deterministic, sorted, duplicate-free JSON
array of exact `repository@sha256:<64-hex>` identities returned by the trusted
OCI publication/read-back boundary in issue #17.

`contracts/helm-release-bindings.json` centrally maps each chart product to the
fixed values keys that may receive those identities. A product must supply
exactly the repository set named by that contract; no extra or missing image is
accepted. The workflow copies the chart under registered temporary state and
writes only the digest scalar in that isolated copy. The original checkout is
never rewritten.

This intentionally exposes a current adoption gap: the live Agent State chart
renders `image.repository:image.tag` and has no `image.digest` value. It will
fail closed at the binding contract until its producer-side chart supports an
immutable digest. The workflow must not downgrade to a mutable tag to make that
producer pass. The Flux wrapper chart currently has no workload image binding,
so its required image array is exactly `[]`.

After binding, every rendered `image:` reference must end in
`@sha256:<64-hex>` and every supplied exact image reference must appear in the
rendered result.

## Hermetic validation and deterministic packaging

Validation verifies Helm `v3.18.6`, `Chart.yaml`, optional
`values.schema.json`, templates, CRDs, values, and exact `Chart.lock`
dependency tuples. Before any operation that can write dependency archives, the
chart is copied under registered temporary state. `helm dependency build`,
`helm lint --strict`, `helm template --include-crds`, and `helm package` operate
only on that copy. The exact caller checkout is verified clean before and after.

For a release, `helm package --version <release> --app-version <release>` binds
the immutable version without editing source. This is required for producers
whose checked-in chart version intentionally remains a development placeholder.

The package is normalized into deterministic gzip/tar bytes with sorted names,
epoch timestamps, normalized ownership/modes, bounded expansion, and ordinary
files/directories only. Source-control junk, credential-key suffixes, and
secret/token-like content are rejected across the full package. The package
exists only under registered temporary state; routine workflow output retains
zero Actions artifacts.

## Exact tag authority and registry write boundary

`reusable-helm-publish.yml` reuses the reviewed
`actions/resolve-release-tag` authority. Planning resolves the exact tag object,
tag commit, release version, and source SHA in `tag-push` mode and requires the
resolved version/source to equal the requested publication tuple. After exact
source checkout and isolated-state preparation, the same tag object and commit
are revalidated immediately before the credentialed publication action. A
moved tag therefore fails before registry write.

Publication accepts canonical SemVer only; `latest` is forbidden. It requires
only the named `registry_username` and `registry_token` secrets. Before registry
login it rejects a non-empty `KUBECONFIG` and a mounted Kubernetes
service-account token.

## Publication, replay, and read-back

Publication performs pull-before-push. An existing byte-identical normalized
package is an idempotent success; different content at the immutable version is
an `immutable_conflict`. Only a definitely missing version is pushed. Lookup or
network ambiguity never becomes a write attempt.

The chart is pulled back and normalized again for package-content comparison.
The release-only read-back then inspects the same chart reference with
`skopeo inspect --raw`, verifies the Helm config media type, exactly one Helm
chart content layer, and that the remote layer digest equals
`chart_package_sha256`.

Public `chart_digest` is the SHA-256 of those exact raw remote OCI manifest
bytes. `chart_package_sha256` remains the package-content identity; the two are
not conflated. `immutable_references_json` records the exact source, product,
release, chart reference, remote OCI manifest digest, package checksum, and
bound exact image references for issue #19.

This family never installs a chart, reconciles Flux, decrypts SOPS data, or
uses Kubernetes authority.

## Shared integration handoff

The Helm-exclusive implementation does not edit shared public-registration,
generated inventory/reference, CIW command registration, bootstrap, or runner
profile files. The shared integration owner must:

1. add required `required_image_references_json` to the `helm.publish` public
   API record and generated references;
2. replace the old Flux product placeholder with
   `flux-github-actions-runner-chart`;
3. move `helm.publish` to the smallest measured Buildah-capable semantic tier
   because the final remote-manifest proof uses Skopeo, without exposing any
   concrete runner selector to callers.

Until those serialized shared edits land, the exclusive slice deliberately
reports the public-registry shape mismatch rather than overwriting another
owner's files.
