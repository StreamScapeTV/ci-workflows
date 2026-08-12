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

## Planner boundary

The typed planner validates:

1. lowercase exact source SHA;
2. bounded immutable release version and request ID;
3. operation in `plan`, `release`, or `verify-only`;
4. release publication only from an exact matching tag context;
5. policy path is relative, normalized, inside the product's reviewed roots,
   and not credential-bearing;
6. runner selector and workspace profile come only from the product contract;
7. dependency API list and expected outputs come only from the contract.

The planner emits no registry host, credentials, cluster identity, or mutable
selection state.

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

Dependency evidence is checked for exact required output names, success state,
digest syntax, immutable reference structure, and mutable `latest` references.
Missing or incomplete dependency evidence fails closed.

During parallel implementation, #33 keeps those outputs as typed adapters and
does not call unfinished branch-private dependency workflows. The final
integration patch can therefore wire the dependencies without changing the
inventory, evidence, replay, or handoff model.

## Evidence and replay

The release manifest is canonical JSON hashed with SHA-256. Immutable references
are accepted on replay only when the complete identity set and every digest are
identical. A partial replay or conflicting immutable version fails.

Runtime probe evidence for runner images must prove the exact platform and tool
versions and explicitly prove absence of Docker/Dockerd, forbidden sockets,
credential paths, service-account tokens, and KUBECONFIG. Chart upstream evidence
must include exact repository/version, immutable digest, license, preserved
attribution, and no unreviewed template mutation.

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
contract-selected execution job. The thin composite action only invokes the
Python adapter. Transient state is issue-owned beneath `RUNNER_TEMP`; cleanup
unlinks a symlink root rather than following it and residue verification fails
if any state remains. The workflow verifies the admitted source stays clean and
retains zero routine Actions artifacts.

The registered public component list also names `internal-flux-assets.yml`. That
leaf is intentionally not synthesized under another filename when its exact
resource is unavailable: current ownership must be transferred before it is
added. Likewise, shared public/CIW registration is integrated only after its
resource handoff.
