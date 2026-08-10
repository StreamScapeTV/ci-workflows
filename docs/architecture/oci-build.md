# OCI build architecture

## Authority boundaries

`contracts/oci-products.json` owns the product, repository, context,
Dockerfile, target stage, platforms, fixed arguments, assertion profile,
measured Buildah tier, and Flux handoff fields. Consumer input cannot override
those decisions. Product repositories retain their Dockerfiles, source pins,
smoke scripts, and product assertions. Flux retains runner inventory, desired
state, canary selection, previous-known-good acceptance, rollback policy,
credentials, and live reconciliation.

`oci.build` has read-only repository permission, no registry secret contract,
no Kubernetes/SOPS/Flux authority, no image publication, and no retained image
archive. `oci.publish` in issue #17 is a separate privileged API and must rebuild
exact source rather than trust this workflow's temporary result.

## Typed implementation

- `oci_types.py` defines requests, product targets, plans, results, and stable
  errors.
- `oci_contract.py` validates the closed contract, source trust, public request,
  product compatibility, runner measurement, platform confirmation, and
  deterministic engine/profile mapping.
- `oci_execution.py` stages tracked context, checks immutable bases, invokes the
  reviewed daemonless Buildah adapter, independently verifies the OCI layout,
  runs bounded smoke, detects secret leakage, and owns cleanup/residue checks.
- `ciw_oci.py` is the issue-owned plan/execute/cleanup/residue adapter. Shared
  CIW and public-workflow registrations remain deferred until issue #15 merges.
- `actions/validate-oci` and the reusable workflow are intentionally thin.

The public API is engine-neutral even though the first required internal
implementation is `buildah-v1`. A future engine may be added only as a reviewed
contract implementation; callers still select only product intent.

## Reproducibility

The build context is reconstructed from tracked source at the exact admitted
SHA. The source date is the exact Git commit timestamp. Build cache and layers
are disabled for the validation run. OCI source, revision, version, creation,
title, description, license, and product labels are injected identically into
every platform. Temporary OCI layouts are verified by digest from bytes rather
than by trusting tool output.

This does not claim that every external package download is reproducible. The
product source must pin every base and dependency input under its own contract;
mutable bases are rejected before Buildah runs. Final publication and remote
normalization/read-back belong to issue #17.

## Runner separation

Planning runs on semantic `portable`. Build execution uses exactly the trusted
planner's JSON selector. Product measurements plus reviewed headroom select one
of `buildah-tiny`, `buildah-small`, `buildah-medium`, or `buildah-high` in
contract data. A caller cannot request labels. Buildah capacity is privileged,
trusted exact-source capacity and contains no Docker daemon, Docker socket,
registry credential, Kubernetes token, or Agent State credential.

Flux replacement images use the separate high-capacity independent bootstrap
record. The workflow emits canary and rollback data only; it cannot select or
reconcile a live image.

## Failure and cleanup

Stable failures cover invalid input, unsupported products, mutable bases,
source/context dirtiness, symlink/path escape, platform/config/layer/metadata
mismatch, secret leakage, forbidden engines/sockets, smoke failure, cleanup,
and residue. Build failure is captured before always-run cleanup. Cleanup
removes all issue-owned state and best-effort removes every recorded manifest,
container, and local image. A separate residue phase and exact clean-source
check make cleanup failure terminal.
