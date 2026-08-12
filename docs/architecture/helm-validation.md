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
  `apps/github-actions-runner`. It is a wrapper chart with no application-image
  binding and references two mirrored Actions Runner Controller charts through
  Flux-owned `OCIRepository` objects.

## Flux upstream and mirror provenance

The Flux wrapper is not treated as provenance-free merely because its
`Chart.yaml` has no Helm dependencies. `contracts/helm-validation.json` fixes
the two expected ARC asset names and their checked-in private mirror
repositories:

- `gha-runner-scale-set`;
- `gha-runner-scale-set-controller`.

For each asset, `.streamscape/helm-product.json` must record an exact upstream
GitHub repository, stable SemVer tag, full upstream commit SHA, upstream chart
SHA-256 digest, SPDX-style license identity, exact private mirror repository,
mirror chart SHA-256 digest, and reviewed patch list. The current central
contract admits only unpatched mirrors, so `patches` must be empty and upstream
and mirror chart digests must match. Both asset tags must agree and must equal
the wrapper chart `appVersion`.

Missing, malformed, reordered, extra, mutable, or mismatched provenance fails
before Helm execution. The Helm test fixture uses deliberately synthetic source
identities and repeated hashes only to exercise the contract; those values are
not real ARC evidence. Live Flux currently records the private mirror
repositories and tag `0.14.2` but not the complete commit/digest/license tuple.
Producer adoption is tracked in `StreamScapeTV/flux#306`; #18 never invents
those missing facts.

## Source admission and deterministic packaging

Each source supplies `.streamscape/helm-product.json`; central Helm contracts
bind the repository, chart root/name, values profiles, locked dependencies,
upstream provenance requirements, and approved OCI chart destination. Chart
paths and dependencies are validated without following symlinks or accepting
credential-bearing or mutable repository locations.

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

## Runner and trust boundary

`helm.validate` remains on semantic profile `portable`, resolved internally to
the general Linux ARC capability. `helm.publish` requires Skopeo for an
independent raw-manifest read-back and therefore cannot run on `portable`.

Publication is mapped to the smallest Skopeo-capable candidate profile,
`buildah-tiny`, through the central semantic runner resolver. Callers never see
or select concrete ARC labels. The candidate is trusted-exact only and carries
no Kubernetes or Agent State authority.

The selection is not final merely because the semantic mapping resolves.
`contracts/helm-publication.json` requires real `peak_memory_bytes` and
`peak_local_storage_bytes` evidence for the exact source/workflow/product plus
reviewed headroom before the issue reaches final-candidate readiness. If the
measured envelope does not fit `buildah-tiny`, the central Buildah escalation
policy selects the next sufficient tier rather than silently overcommitting the
runner.

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

## Shared registration and final integration

The final integrated head must keep all shared projections synchronized with
the implementation:

1. public `helm.publish` registration exposes optional `image_digest` and
   `immutable_references_json` with direct #17 pass-through semantics;
2. supported chart products use `flux-github-actions-runner-chart`, not the
   stale `flux-runner-chart-assets` placeholder;
3. CIW/public/generated references match the reusable workflows and actions;
4. runner contract, deterministic runner mapping, and compatibility report
   agree that validation is portable and publication uses the measured
   Skopeo-capable tier;
5. the repository-wide generated/public API/canonical validation gates are
   green on the unchanged final candidate.

Shared files are integrated serially under Agent State resource ownership; a
textual branch diff is not permission to overwrite another active shared lane.
