# Architecture: Flux infrastructure asset orchestration

## Authority split

`ci-workflows` owns reusable orchestration and typed validation for the current
Flux CI infrastructure product inventory. `StreamScapeTV/flux` remains the sole
authority for product source, desired state, rollout targets, HelmRelease and
Kustomization data, runner scale-set selection, storage and quotas, RBAC,
Secrets/SOPS material, cluster credentials, canary acceptance, live health, and
rollback approval.

The `flux.assets` workflow is therefore a publication/read-back composition
boundary, not a reconciliation workflow. No Kubernetes or SOPS credential is
accepted anywhere in the issue-#33 contract.

## Product model

The inventory contract deliberately separates product family from workflow
logic. The live custom image members are Buildah and Mobile; Portable remains an
upstream image selected by Flux policy rather than a custom build product. The
runner chart bundle records the local wrapper chart plus the two reviewed ARC
upstream OCI charts.

All public decisions are derived from a checked-in `product_id`. No caller can
supply a container engine, runner labels, destination, upstream repository,
cluster target, or arbitrary command.

## Planner and caller-event boundary

The typed planner/guard validates:

1. lowercase exact source SHA;
2. bounded immutable release version and request ID;
3. operation in `plan`, `release`, or `verify-only`;
4. `release` only from a tag `push` matching the exact requested version;
5. `verify-only` only from `workflow_dispatch` on the caller default branch;
6. policy path is relative, normalized, inside the product's reviewed roots,
   and not credential-bearing;
7. runner selector and workspace profile come only from the product contract;
8. dependency API list and expected outputs come only from the contract.

The public and internal reusable workflows require `github.repository` to be
exactly `StreamScapeTV/flux` before source checkout or composition. The thin
runtime adds a second allowlist: plan/release may execute only from
`StreamScapeTV/flux` or from `StreamScapeTV/ci-workflows` for repository-owned
exact-head smoke. An unrelated repository cannot call the composite directly and
spoof event/ref inputs.

The public workflow's `github` context remains caller-associated, so it passes
only actual event/ref/default-branch metadata into the typed guard. The internal
leaf does the same: event name, ref type/name, and default branch are derived
from `${{ github.* }}` and are not `workflow_call` inputs. A Flux PR therefore
cannot directly call the leaf and claim to be a release tag push. Central source
is checked out by `job.workflow_repository` and `job.workflow_sha`, which
identify the called reusable workflow itself. The planner emits no registry
host, credentials, cluster identity, or mutable selection state.

## Bootstrap and source boundary

A replacement runner image is self-hosting infrastructure. The contract therefore
requires a separate current-known-good builder identity. Both known-good builder
and candidate must be digest-pinned and different. A candidate cannot validate
itself simply because it built successfully.

Every effective image `FROM` is required to use an explicit digest reference.
The strict source adapter resolves each exact runner Dockerfile as a normalized
relative POSIX path, rejects a symlink source root or Dockerfile, requires the
resolved ordinary file to stay below the admitted source, then parses pinned
bases. Chart publication receives the same fail-closed boundary: the checked-in
chart root and required `Chart.yaml`, `values.yaml`, and `values.schema.json`
must resolve as ordinary non-symlink files below the admitted source. This
exposes mutable or escaped current Flux source as an adoption blocker instead of
hiding it behind central orchestration.

## Dependency composition

Issue #33 does not copy the OCI or Helm publishers. It accepts the bounded public
outputs registered for:

- `oci.build` and `oci.publish` for the runner-image family;
- `helm.validate` and `helm.publish` for runner chart assets.

Every dependency `product_id` is checked against current merged
`contracts/products.json` entries owned by `StreamScapeTV/flux`. API families
are bound to product kinds: `oci.*` requires the current
`oci-runner-image-family`, while `helm.*` requires the current
`helm-oci-chart-assets` product. Thus a branch-private dependency identity or a
current-but-wrong product kind fails before planning. At this checkpoint the
Helm dependency remains `flux-runner-chart-assets`; the branch-private #18
`flux-github-actions-runner-chart` identity is not consumed before it actually
merges.

The implemented `oci.build` interface receives an additional exact adapter based
on merged `contracts/oci-products.json`. For the Flux runner family, target sets
must be exactly the merged OCI target IDs and each target must return exactly its
contract-owned platform set. `platform_digests_json` rows must use the merged
shape `{platform, manifest_digest, config_digest, layer_digests}` with immutable
SHA-256 identities; duplicate, missing, unexpected, or stale pre-integration
nested platform evidence fails closed.

Dependency evidence must otherwise match the operation's dependency set exactly.
The composition layer checks required outputs, success state, immutable
identities, and mutable references. Detailed `oci.publish` and Helm publication
payload adapters remain dormant until those APIs actually merge; #33 does not
promote branch-private synthetic fixtures into authoritative interfaces.

`oci.publish`, `helm.validate`, and `helm.publish` remain planned/unmerged on the
current central branch, so the issue-exclusive public workflow passes empty
dependency evidence and fails closed for release/verify-only rather than calling
branch-private implementations. Final dependency wiring happens only after those
registered interfaces are authoritative on `main`.

## Runtime proof boundary

Runtime probe evidence for runner images must prove exact platform and tool
versions and explicitly prove absence of Docker/Dockerd, forbidden sockets,
credential paths, service-account tokens, and KUBECONFIG. The strict layer
consumes the canonical image-member shape directly: it derives OS/architecture
from the nested `platform` object, requires every checked-in OCI label to equal
its exact reviewed value, independently rejects forbidden tools present in the
tool map, and verifies subordinate-ID configuration and storage driver where the
contract defines them.

Chart upstream evidence must include exact repository/version, immutable digest,
Apache-2.0 license identity, preserved attribution, and no unreviewed template
mutation.

## Evidence and replay

The release manifest is canonical JSON hashed with SHA-256. Immutable references
are accepted on replay only when the complete identity set and every digest are
identical. A partial replay or conflicting immutable version fails.

The guarded composer retains nested read-back evidence in the manifest rather
than collapsing dependency outputs to a single digest. Final publication output
shape is reconciled only from merged dependency contracts.

## Handoff boundary

The generated Flux handoff is intentionally inert. It contains source/version,
verified asset references, manifest digest, canary ID, previous-known-good
policy identity, and rollback identity. It always requires review and canary
evidence and always marks mutation, desired-state change, cluster credentials,
and SOPS credentials as false.

A later Flux-owned reviewed change may consume that handoff. Publication itself
cannot select or reconcile an ARC scale set.

## Workflow and cleanup boundary

The public reusable workflow has a small general-Linux planning job and a
contract-selected execution job. Both check out the central implementation by
called-workflow identity and the admitted Flux source by exact SHA. The thin
composite action invokes only the Python adapter.

The registered `.github/workflows/internal-flux-assets.yml` leaf is a separate
composition-only component on `[linux, amd64, general]`. It has no caller-supplied
trust metadata, accepts no runner labels, calls no other reusable workflow, and
owns no registry or cluster credential. It remains staged because GitHub
reusable-call jobs do not accept `timeout-minutes`, while the current central
validation policy still requires a positive timeout on every job. #33 does not
work around that shared policy with GitHub-invalid YAML; the shared
harness/call-site rule must be reconciled before the leaf can be nested.

Transient state is issue-owned beneath `RUNNER_TEMP`; cleanup unlinks a symlink
root rather than following it and residue verification fails if any state
remains. Public, internal, and exact-head smoke workflows verify clean source and
query the run artifact inventory to prove zero routine Actions artifacts.

Shared public/CIW/bootstrap/validation-harness registration remains serialized
integration work and is not modified by the issue-exclusive checkpoint.
