# OCI build architecture

## Authority boundaries

`contracts/oci-products.json` owns the product, repository, context,
Dockerfile, target stage, platforms, fixed arguments, assertion profile,
measured Buildah tier, and Flux handoff fields. Consumer input cannot override
those decisions. Product repositories retain their Dockerfiles, source pins,
smoke scripts when they exist, and product assertions. Flux retains runner
inventory, desired state, canary selection, previous-known-good acceptance,
rollback policy, credentials, and live reconciliation.

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
  detects secret leakage, and owns cleanup/residue checks.
- `oci_execution_safe.py` adds bounded local-filesystem verification and the
  hardened non-publishing execution sequence used by the public façade.
- `oci.py` exposes the public `build` and `inspect` implementation components and
  delegates to the hardened executor and strict inspector.
- `ciw_oci.py` implements the bounded `ciw oci validate` phases while
  `scripts/ci/oci.py` remains a compatibility adapter for existing validation
  and generated-mapping checks.
- `actions/validate-oci` and the reusable workflow are intentionally thin and
  dispatch through the stable `ciw` registry.

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

## Reusable-workflow source identity

The reusable planner and build job do not clone the private central repository
with a caller-scoped token. They compose the reviewed `validate-oci` private
action at immutable central revision
`29cb88e406a0490834bd556bb825d0e227c862ac` and the immutable shared foundation
actions for exact checkout, workspace state, evidence, and cleanup.

Each private action archive resolves central scripts and typed Python libraries
relative to `GITHUB_ACTION_PATH`, so no `.ciw` checkout, mutable central ref,
caller credential, or generic secret is needed to reach the implementation.
Those exact remote action identities must be represented in the action lock
before the final candidate is merged. Caller source remains independently bound
to the admitted product SHA, checked out detached, and reverified clean after
terminal cleanup. Immutable central helper distribution therefore changes only
how reviewed central code reaches a private caller; it does not weaken source,
runner, build, or cleanup authority.

## Runner separation

Planning runs directly on the approved general-Linux capability selector
`[linux, amd64, general]`. Build execution uses exactly the trusted planner's
JSON selector. Product measurements plus reviewed headroom select one of
`buildah-tiny`, `buildah-small`, `buildah-medium`, or `buildah-high` in
contract data. A caller cannot request labels. Buildah capacity is privileged,
trusted exact-source capacity and contains no Docker daemon, Docker socket,
registry credential, Kubernetes token, or Agent State credential.

Flux replacement images use the separate high-capacity independent bootstrap
record. Its current build targets are exactly
`images/github-actions-runner-buildah/Dockerfile` and
`images/github-actions-runner-mobile/Dockerfile`; there is no synthetic portable
image target. The workflow emits canary and rollback data only; it cannot select
or reconcile a live image.

## Failure and cleanup

Stable failures cover invalid input, unsupported products, mutable bases,
source/context dirtiness, symlink/path escape, platform/config/layer/metadata
mismatch, secret leakage, forbidden engines/sockets, smoke failure, cleanup,
and residue. Build failure is captured before always-run cleanup. Cleanup
removes all issue-owned state and best-effort removes every recorded manifest,
container, and local image. A separate residue phase and exact clean-source
check make cleanup failure terminal.
