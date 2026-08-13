# OCI build architecture

## Authority boundaries

`contracts/oci-products.json` owns the product, repository, context,
Dockerfile, target stage, platforms, fixed arguments, assertion profile,
measured Buildah tier, input-policy identifier, fixed source-lock path, allowed
registry and download hosts, fixed lowercase publication repository, and Flux
handoff fields. Consumer input cannot override those decisions. Multi-target
products must use one common publication host and it must equal the product's
closed `registry_write_policy`. Publication remains blocked until an exact Flux
authority commit and sha256 evidence verify
`server-side-create-only-tags-v1`; client preflight is not treated as registry
immutability. The registry host is derived from target repositories only when
publication authenticates. Product repositories retain
their Dockerfiles, the exact input lock at that central-fixed per-target path, source pins, smoke
scripts when they exist, and product assertions. Flux retains runner inventory,
desired state, canary selection, previous-known-good acceptance, rollback
policy, credentials, and live reconciliation.

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
- `oci_input_contract.py` validates the exact consumer-source lock against the
  selected central policy and Dockerfile stages.
- `oci_input_download.py` performs bounded HTTPS acquisition with digest and
  size verification. `oci_registry_download.py` owns anonymous exact-digest
  Distribution/API/token/blob acquisition with role-separated hosts and bounded
  descriptor graphs. `oci_base_inspection.py` verifies local OCI descriptor
  bytes independently of registry-tool output.
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

Before any Dockerfile instruction runs, the builder loads the product-owned
lock from the central-fixed target path. The lock must account for every
Dockerfile stage in order, preserve each declared exact `@sha256:` reference,
and enumerate the selected platforms. Central `input_policy_id` data remains
authoritative for distinct registry reference, Distribution API, anonymous
token, blob-redirect, and external-download host allowlists, HTTPS-only
behavior, redirect limits, no ambient authentication, and maximum input size.
The roles are not collapsed into one host union: the declared reference host
cannot become an authorization realm or blob redirect merely because it is an
approved image name authority.

Each non-scratch base is copied by exact digest under an empty authentication
file into temporary local OCI state. The implementation hashes the root
descriptor bytes, selects the exact locked platform child, and hashes its
manifest and config before assigning a deterministic local-only base identity.
For the Docker Hub smoke profile, `docker.io` is the reference authority,
`registry-1.docker.io` is the Distribution API, `auth.docker.io` is the only
anonymous-token authority, and `production.cloudfront.docker.com` is the only
blob-redirect authority. Every challenged realm, API request, and redirect hop
must retain its assigned role and remain HTTPS.
Each declared external input is fetched before the build, checked on every
allowed redirect hop, bounded by its maximum size, verified against its exact
SHA-256, and materialized only beneath the reserved `.ciw-build-inputs`
directory in the staged context.

Buildah then executes `bud --pull=never --network none` from the fully
materialized state. Dockerfile instructions, consumer scripts, and package
managers receive no egress; an undeclared or mutable input fails closed rather
than falling back to the network. Final publication and remote
normalization/read-back belong to issue #17.

`resolved_inputs_json` is redacted build evidence keyed by target and projected
through the action and reusable workflow without retaining an artifact. It
contains the lock and policy identities, verified base descriptor identities,
external input IDs, digests, sizes, and deterministic evidence IDs. It excludes
source URLs, credentials, authentication-file paths, temporary filesystem
paths, and registry implementation details.

## Reusable-workflow source identity

The reusable planner and build job do not clone the private central repository
with a caller-scoped token. They compose the reviewed `validate-oci` private
action at immutable central revision
`3b401078d1167d7048281e3c3269556ce586dada` and the immutable shared foundation
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
trusted exact-source capacity. Central code, never caller or ambient path
input, allocates exactly three marker-bound `0700` leaves: scratch below
`/var/tmp/buildah/ciw-oci-<token>`, physical graph storage at
`/var/lib/containers/storage/ciw-oci-<token>`, and physical run state at
`/run/containers/storage/ciw-oci-<token>`. No public input, `RUNNER_TEMP`,
`TMPDIR`, or other ambient path selects these roots. Production requires Linux,
effective UID 0, and all
three fixed parents to be pre-provisioned non-symlink directories and exact
mount points in `/proc/self/mountinfo`.

Every Buildah, Skopeo, and Podman subprocess runs inside a fresh private mount
namespace. The namespace first bind-mounts the physical graph leaf onto the
scratch tree's `implicit-containers/storage`, then recursively bind-mounts that
private containers tree over `/var/lib/containers`. Buildah retains the fixed
logical graph root `/var/lib/containers/storage`, while its bytes and Skopeo's
rootful blob-info cache remain in the allocation. The Buildah run root is the
physical run leaf. Staged source, downloads, layouts, HOME, XDG directories,
and temporary files remain under scratch. Cleanup engine calls use the same
namespace before removing the allocation. The capacity contains no Docker
daemon, Docker socket, registry credential, Kubernetes token, or Agent State
credential.

Flux replacement images use the separate high-capacity independent bootstrap
record. Its current build targets are exactly
`images/github-actions-runner-buildah/Dockerfile` and
`images/github-actions-runner-mobile/Dockerfile`; there is no synthetic portable
image target. The workflow emits canary and rollback data only; it cannot select
or reconcile a live image.

## Failure and cleanup

Stable failures cover invalid input, unsupported products, mutable bases,
missing or mismatched source locks, unapproved hosts or redirects, digest or
size mismatch, source/context dirtiness, symlink/path escape,
platform/config/layer/metadata mismatch, secret leakage, forbidden
engines/sockets, smoke failure, cleanup, and residue. Build failure is captured
before always-run cleanup. Cleanup unconditionally removes the reserved input
tree, downloaded bytes, authentication files, base layouts and local tags, all
other issue-owned state, and every recorded manifest, container, and local
image. A separate residue phase and exact clean-source check make any remaining
state terminal.

Each leaf's private `0600` marker binds the operation domain, token, and all
three recomputed paths. Exact directory/file type and modes are revalidated
before engine execution, and each fixed parent is revalidated as an exact
mountpoint. Cleanup never follows a substituted entry, never
traverses a marker-mismatched directory, attempts all three leaves, and reports
substitution or incomplete removal as `cleanup_failed`. Residue requires every
leaf to be absent while preserving sibling allocations.
