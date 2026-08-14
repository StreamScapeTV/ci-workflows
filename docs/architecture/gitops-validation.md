# GitOps validation architecture

## Authority split

The central repository owns the `validation.gitops` API, typed plan/execution
engine, exact tool identities, source admission, deterministic rendering,
evidence, cleanup, and stable failure projection. Consumer repositories own
which source roots, chart values, Kustomizations, schemas, required values, and
policy scripts are valid. Flux owns live GitOps desired state, SOPS decryption,
cluster credentials, reconciliation, health, and rollback acceptance.

The validator is deliberately source-only. It contains no Kubernetes client,
Flux client, registry writer, release publisher, production secret, or live
endpoint. It never decrypts SOPS data, invokes cluster mutation, or converts
portable validation into a privileged path.

## Execution layers

1. A protected planning job invokes the immutable private `validate-gitops`
   action, validates the exact request against `contracts/gitops-validation.json`,
   and resolves semantic `portable` through the central runner contract.
2. The execution job checks out only the exact admitted caller SHA through the
   immutable exact-checkout foundation action with bounded history depth `1000`.
   It does not clone the private central repository into `.ciw`.
3. A marker-bound workspace provides isolated HOME, cache, temporary, download,
   installation, render, log, and evidence roots.
4. The named Python engine downloads fixed tools, verifies digest and identity,
   selects checked-in targets, validates/renders twice, compares normalized
   fingerprints, detects duplicate object ownership, and runs at most one fixed
   checked-in policy script without a shell.
5. The engine verifies that the Git source and content digest are unchanged.
6. Always-run cleanup removes the issue-owned root without following symlinks,
   the shared workspace cleanup removes its registration, and an API query
   proves zero Actions artifacts.

Workflow YAML remains ordered orchestration. Algorithms and error handling live
under `src/ci_workflows/gitops_*.py`; the composite action and `scripts/ci`
adapter are thin entry points.

## Immutable private central identity

Private callers receive central implementation through exact full-SHA private
action references rather than a caller-token checkout of
`StreamScapeTV/ci-workflows`. `validate-gitops` is pinned to reviewed immutable
central revision `8445e63dd9fa9468b60b6d0c61e543da9681b47b`; the common exact
checkout, workspace, evidence, and cleanup actions reuse the immutable #116
foundation checkpoint. These identities are registered in
`contracts/action-tool-lock.json`.

Each action resolves its own scripts and typed libraries through
`GITHUB_ACTION_PATH`. The reusable workflow therefore needs no `.ciw` clone,
central PAT, secret inheritance, mutable helper reference, or caller-selected
central source. This changes only central helper distribution: exact product
source, semantic runner selection, public permissions, cleanup, and zero-artifact
policy remain unchanged.

The exact-checkout foundation allows history depth only from 1 through 1000.
GitOps changed-tree validation therefore requests the maximum bounded depth
`1000`; unlimited depth `0` is outside the source-admission contract and is
rejected rather than silently treated as another checkout mode.

## Determinism and ownership

YAML documents are normalized as sorted canonical JSON. Helm and Kustomize are
run twice with fixed arguments and isolated state; byte drift fails before
normalization. Optional checked-in expected renders are compared after
normalization. Every Kubernetes object contributes
`apiVersion/kind/namespace/name`; the same identity produced by different
contract targets is rejected as duplicate ownership. Changed-tree mode uses an
exact `base...head` Git comparison and can select only registered target roots.

## Security invariants

- Exact admitted SHA and a clean Git worktree are required before and after work.
- Regular source roots reject traversal and every symlink component.
- Tool URL, digest, version, archive member, redirect host, and output bounds are
  fixed in the contract; callers cannot override them.
- Policy execution uses a fixed `python3 <checked-in-path>` argv, exact file
  SHA-256, fixed timeout, bounded sanitized output, and no shell callback.
- Kustomize rejects remote resources, parent traversal, plugins, Helm charts,
  and executable generators.
- Helm rejects mutable versions and unlocked or non-vendored dependencies.
- The workflow exposes no secret, registry, cluster, deployment, decryption, or
  publication input and grants read-only source plus bounded Actions-read
  permission for zero-artifact verification.
- General Linux `portable` capacity is the only runner profile. The retired
  emergency Mac exception is not restored.
- Cleanup is registered-root-only, no-follow, fail-closed, and preserves both a
  primary and cleanup failure. Zero routine artifacts is invariant.

## Consumer progression

The inert synthetic consumer proves YAML/schema/SOPS, a locked vendored Helm
library, deterministic Kustomize output, duplicate ownership, policy execution,
and cleanup. `flux-source` provides a bounded YAML/changed-tree shape without
live authority. `agent-state` has a bounded Helm shape. The current
`iptv-backend` consumer is recorded as source-audit-only until its external
Valkey dependency has an immutable vendoring/content contract; product-owned
validation remains authoritative in the interim.
