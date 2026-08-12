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

The public workflow's `github` context remains caller-associated, so it passes
only event/ref/default-branch metadata into the typed guard. Central source is
checked out by `job.workflow_repository` and `job.workflow_sha`, which identify
the called reusable workflow itself. The planner emits no registry host,
credentials, cluster identity, or mutable selection state.

## Bootstrap boundary

A replacement runner image is self-hosting infrastructure. The contract therefore
requires a separate current-known-good builder identity. Both known-good builder
and candidate must be digest-pinned and different. A candidate cannot validate
itself simply because it built successfully.

The source contract additionally rejects any effective `FROM` line that is not
an explicit digest reference. This deliberately exposes mutable current Flux
base tags as an adoption blocker instead of hiding them behind the central
workflow.

## Dependency composition

Issue #33 does not copy the OCI or Helm publishers. It accepts the bounded public
outputs registered for:

- `oci.build` and `oci.publish` for the runner-image family;
- `helm.validate` and `helm.publish` for runner chart assets.

Dependency evidence must match the operation's dependency set exactly. The
composition layer checks required outputs, success state, immutable identities,
and mutable references while preserving nested OCI per-platform evidence.
`oci.publish` is additionally bound to the exact source SHA/version, two live
runner targets, target digest/reference parity, and contract-owned
canary/known-good/rollback identities. `helm.publish` is bound to the exact
immutable chart version, remote chart digest, and normalized package SHA-256.
Missing, extra, stale, or conflicting dependency evidence fails closed.

During parallel recovery, #33 keeps those outputs as typed adapters and does not
call unfinished branch-private dependency workflows. The final integration patch
can therefore wire the registered dependencies without changing the inventory,
evidence, replay, or handoff model.

## Runtime proof boundary

Runtime probe evidence for runner images must prove exact platform and tool
versions and explicitly prove absence of Docker/Dockerd, forbidden sockets,
credential paths, service-account tokens, and KUBECONFIG. The strict layer also
requires every checked-in OCI label to be present and non-empty. Where the
product contract defines them, subordinate-ID configuration and storage driver
must match exactly.

Chart upstream evidence must include exact repository/version, immutable digest,
license, preserved attribution, and no unreviewed template mutation.

## Evidence and replay

The release manifest is canonical JSON hashed with SHA-256. Immutable references
are accepted on replay only when the complete identity set and every digest are
identical. A partial replay or conflicting immutable version fails.

The guarded composer retains nested read-back evidence in the manifest instead
of forcing `platform_digests_json` into a flat map. This is required because the
OCI interfaces carry per-target/per-platform manifest, config, layer and label
proof rather than only one digest string.

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

The registered `.github/workflows/internal-flux-infrastructure-assets.yml` leaf
is a separate composition-only component on `[linux, amd64, general]`. It does
not accept runner labels, does not call another reusable workflow, and owns no
registry or cluster credential. It is staged for final dependency composition
but is not yet called by the public workflow because GitHub's reusable-call job
syntax excludes `timeout-minutes` while the current repository harness requires
a timeout on every job. Final shared integration must reconcile that rule rather
than committing GitHub-invalid YAML.

Transient state is issue-owned beneath `RUNNER_TEMP`; cleanup unlinks a symlink
root rather than following it and residue verification fails if any state
remains. Public, internal, and exact-head smoke workflows verify clean source and
query the run artifact inventory to prove zero routine Actions artifacts.

Shared public/CIW/bootstrap/action-lock registration remains serialized
integration work and is not modified by the issue-exclusive checkpoint.
