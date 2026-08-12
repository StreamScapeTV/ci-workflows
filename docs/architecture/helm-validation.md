# Helm validation and OCI chart publication

Issue #18 defines one product-contract Helm family for the chart producers that
exist today. Callers select only a central product identity, exact admitted
source, release version where applicable, and bounded checked-in values/policy
selectors. They cannot choose chart roots, dependency repositories, registry
hosts, runner labels, commands, secret names, clusters, namespaces, service
accounts, or container engines.

## Current producer inventory

The Helm contract covers:

- `iptv-backend-chart` in `StreamScapeTV/iptv-backend`, chart
  `charts/iptv-backend`; its Valkey dependency is locked and uses bounded
  credential-free HTTPS.
- `agent-state-chart` in `StreamScapeTV/agent-state`, chart
  `charts/agent-state`; release metadata is bound on an isolated copy because
  checked-in development metadata may remain `0.0.0`.
- `flux-github-actions-runner-chart` in `StreamScapeTV/flux`, chart
  `apps/github-actions-runner`. It is currently a wrapper chart with no
  application-image binding.

Flux also mirrors upstream ARC charts. Complete upstream commit, original chart
digest, license, and reviewed-patch provenance remain Flux-owned adoption
requirements; #18 never invents missing provenance.

## Source admission and deterministic packaging

Each source supplies `.streamscape/helm-product.json`; central Helm contracts
bind the repository, chart root/name, values profiles, locked dependencies, and
approved OCI chart destination. Chart paths and dependencies are validated
without following symlinks or accepting credential-bearing/mutable repository
locations.

All operations that can write run against isolated temporary chart state.
`helm dependency build`, `helm lint --strict`, deterministic rendering, and
`helm package` never mutate caller source. A release uses `helm package
--version <release> --app-version <release>` rather than rewriting source chart
metadata. The resulting archive is normalized for deterministic path order,
ownership, modes, and timestamps and is checked for unsafe entries and secret
content. Routine Actions artifacts remain zero.

## Direct immutable image evidence from issue #17

`helm.publish` consumes issue #17's registered reusable-workflow outputs
`image_digest` and `immutable_references_json` directly. There is no release
adapter that converts them into a second public image-reference format.

The binding authority is `contracts/helm-release-bindings.json`. For every
image-bearing chart it records:

- the corresponding #16/#17 OCI product ID;
- the exact OCI target ID;
- the canonical published GHCR repository;
- the repository value expected in checked-in chart source before binding;
- the only values-file repository and digest keys that may be changed.

Current bindings are:

- `iptv-backend-chart` -> OCI product `iptv-backend-image`, target
  `iptv-backend`, published repository
  `ghcr.io/streamscapetv/iptv-backend`;
- `agent-state-chart` -> OCI product `agent-state-image`, target
  `agent-state-api`, published repository
  `ghcr.io/streamscapetv/agent-state`;
- `flux-github-actions-runner-chart` -> no OCI application-image binding.

For a bound chart both OCI inputs are mandatory. For the no-binding Flux
wrapper both must be omitted. The parser rejects malformed JSON, extra or
missing targets, source/version disagreement, repository disagreement, mutable
or mismatched version/source identities, invalid digests, and disagreement
between `image_digest` and each target's read-back `manifest_digest`.

After validation, #18 derives exact `repository@sha256:<digest>` identities
internally. The isolated chart copy has both repository and digest rewritten to
the verified published identity. The original checkout remains byte-for-byte
unchanged. Rendered workloads must contain only immutable image references and
must contain every required bound reference.

The Agent State producer currently has no `image.digest` scalar in its chart,
so release binding intentionally fails closed until the producer adopts digest
rendering. Mutable-tag fallback is forbidden.

## Exact tag authority at the registry write boundary

`reusable-helm-publish.yml` reuses `actions/resolve-release-tag`. Its plan job
resolves the exact tag object, tag commit, source SHA, and release version and
checks the source/version tuple against the requested publication. In the
publication job, the same tag object and commit are revalidated immediately
before named registry credentials are handed to the Helm publisher. A moved
or retargeted tag therefore cannot race the registry write.

Publication is trusted-exact only, stable SemVer only, and never publishes
`latest`. It rejects Kubernetes authority before registry login; release jobs
receive no cluster, SOPS, Flux-reconciliation, or deployment credential.

## Publication, replay, and independent read-back

Publication first probes the immutable version by pulling it. Exact normalized
package parity is an idempotent replay. Different content is an immutable
conflict. Only a definitely missing version may be pushed; ambiguous lookup or
network failures never become write attempts.

The chart is pulled again into isolated state and normalized for package
comparison. A second independent Skopeo raw-manifest read verifies Helm config
media type, exactly one Helm chart content layer, and that the layer digest
matches the deterministic package SHA-256.

`chart_package_sha256` is the package-content identity. Public `chart_digest`
is separately the SHA-256 of the exact raw remote OCI manifest bytes. Helm's
`immutable_references_json` records admitted source, chart product, release
version, chart reference, remote manifest digest, package checksum, and the
internally derived image references for release orchestration.

This family never installs a chart, mutates Kubernetes, reconciles Flux, or
decrypts SOPS data. Helm/package/auth/cache/read-back state is removed on all
terminal paths and residue verification fails closed.

## Shared integration handoff

This Helm-exclusive slice deliberately does not modify serialized shared
public-API, CIW/bootstrap, generated-reference, or runner-profile files. The
shared integration owner must:

1. register optional `image_digest` and `immutable_references_json` inputs on
   `helm.publish`, preserving their direct #17 pass-through meaning;
2. replace the old Flux chart placeholder with
   `flux-github-actions-runner-chart`;
3. select the smallest measured Buildah semantic tier that supplies Skopeo for
   Helm publication, without exposing concrete runner selection to callers;
4. regenerate/validate the shared public reference surfaces on the final
   integrated head.

Until that serialized work lands, the branch's Helm workflow test recognizes
only this precise expected registration delta; the repository-wide public API
validator remains the integration gate.
