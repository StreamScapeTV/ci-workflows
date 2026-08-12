# Helm validation and OCI chart publication

Issue #18 defines one product-contract Helm family for the chart producers that
exist today. Callers may select only a central `product_id`, an optional
checked-in values profile/policy path, an exact admitted source SHA, and (for a
release) an exact SemVer. They cannot select chart roots, dependency
repositories, registries, runner labels, commands, secret names, clusters,
namespaces, service accounts, or container engines.

## Current producer inventory

The contract is intentionally aligned to the live producer identities rather
than placeholders:

- `iptv-backend-chart` -> `StreamScapeTV/iptv-backend`,
  `charts/iptv-backend`, chart `iptv-backend`. The current chart has a locked
  Valkey dependency from `https://valkey.io/valkey-helm/`; therefore locked
  dependency repositories admit bounded credential-free HTTPS as well as OCI.
- `agent-state-chart` -> `StreamScapeTV/agent-state`, `charts/agent-state`,
  chart `agent-state`. Its checked-in development metadata is `0.0.0`; a
  release version must therefore be bound while packaging an isolated copy,
  not by editing the caller checkout.
- `flux-github-actions-runner-chart` -> `StreamScapeTV/flux`,
  `apps/github-actions-runner`, chart `github-actions-runner` (currently
  wrapper version `1.3.0`, ARC app version `0.14.2`).

Flux also mirrors the upstream ARC
`gha-runner-scale-set-controller:0.14.2` and
`gha-runner-scale-set:0.14.2` OCI charts. The current Flux repository records
their exact version and mirror destinations, but does not record the complete
upstream commit, chart digest, and license tuple required by issue #18. This
Helm slice does not invent those facts. Mirrored-upstream provenance remains a
producer/adoption gap to close before the central family can claim complete ARC
mirror admission.

## Source and chart admission

Each admitted product source supplies `.streamscape/helm-product.json`. The
central `contracts/helm-validation.json` binds that manifest to one known
repository, chart name, and fixed
`oci://git.faruqi.dev/mimranfaruqi/helm-charts` destination. The manifest
supplies an ordinary relative chart root, exact values-profile paths, optional
fixed policy path, locked dependency triples, and required rendered image
digests.

All paths are descriptor-checked below the exact checkout. Any symlink anywhere
inside the chart tree, path traversal, credential-bearing dependency URL, or
dependency/lock mismatch fails closed. HTTPS dependency URLs may not contain
userinfo, query strings, or fragments.

Source trust is explicit. `helm.validate` accepts `untrusted-fork`,
`trusted-pr`, or `trusted-exact` according to the protected caller-event
classification. `helm.publish` accepts only `trusted-exact`.

## Hermetic validation and deterministic packaging

Validation verifies Helm `v3.18.6`, `Chart.yaml`, optional
`values.schema.json`, templates, CRDs, values, and exact `Chart.lock`
dependency tuples. Before any operation that can write dependency archives, the
validated chart is copied under registered temporary state. `helm dependency
build`, `helm lint --strict`, `helm template --include-crds`, and `helm package`
operate only on that isolated copy. The exact caller checkout is verified clean
both before and after packaging.

Every rendered `image:` reference must end in an immutable
`@sha256:<64-hex>` digest even when the manifest does not enumerate a required
image. Required manifest digests must additionally appear in rendered output.

For a release, `helm package --version <release> --app-version <release>` binds
the immutable release version in the isolated copy. This supports producers
such as Agent State whose source chart intentionally retains `0.0.0` while
leaving source bytes untouched.

The package is normalized into deterministic gzip/tar bytes: sorted names,
epoch timestamps, normalized ownership/modes, bounded expansion, and ordinary
files/directories only. Source-control junk, credential-key suffixes, and
secret/token-like content are rejected across the entire package including
templates. The package exists only under registered temporary state and is
removed by terminal Helm and workspace cleanup. Routine workflow output retains
zero Actions artifacts.

## Publication and read-back

Publication accepts canonical SemVer only; `latest` is forbidden. It requires
only the named `registry_username` and `registry_token` secrets, supplied to
the publication action after planning. Before registry login it rejects a
non-empty `KUBECONFIG` and a mounted Kubernetes service-account token.

Publication performs pull-before-push. An existing byte-identical normalized
package is an idempotent success; a different package at the immutable version
is an `immutable_conflict`. Only a definitely missing version is pushed, once,
and then pulled into isolated state and normalized again for SHA-256
comparison. Lookup/network ambiguity never becomes a publication attempt.

This family never installs a chart, reconciles Flux, decrypts SOPS data, or
uses Kubernetes authority.

## Shared integration handoff

The Helm-exclusive implementation deliberately does not edit shared CIW/public
registration, generated workflow inventory, bootstrap, or runner-profile
files. The current shared runner contract advertises both `helm.validate` and
`helm.publish` on the `portable` semantic profile, so this slice preserves that
registered selector. The shared integration owner must reconcile that existing
registration with the organization preference for registry publication on the
smallest measured Buildah tier before final issue #18 integration, without
giving callers a runner selector.
