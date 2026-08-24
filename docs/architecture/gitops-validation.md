# GitOps validation architecture

## Authority split

The central repository owns the `validation.gitops` API, typed plan/execution
engine, exact tool identities, source admission, deterministic rendering,
evidence, cleanup, and stable failure projection. Consumer repositories own
which source roots, chart values, Kustomizations, schemas, required values,
SOPS source patterns, and policy scripts are valid. Flux owns live GitOps
desired state, SOPS decryption, cluster credentials, reconciliation, health,
and rollback acceptance.

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
4. The named Python plan layer resolves exact contract paths plus bounded SOPS
   globs. The composition layer selects changed-tree targets, scopes formatting,
   validates raw YAML/SOPS source, and composes nested render targets. Helm and
   Kustomize use their fixed deterministic renderers.
5. The engine verifies object ownership, runs at most one fixed checked-in
   policy script without a shell, and verifies that the Git source and content
   digest are unchanged.
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

## Changed-tree and formatting semantics

Git reports changed files repository-relative. A target root of `.` therefore
matches any non-empty repository-relative changed set directly; non-root targets
continue to match only their exact root or descendants. There is no synthetic
`./` prefix in the selection contract.

Target selection and YAML validation are separate. A selected YAML target still
parses every included document semantically with duplicate-key rejection and
schema/SOPS checks. Formatting is scoped by profile:

- `yaml` keeps strict formatting for every selected YAML file;
- `changed-tree` applies strict tab/final-newline/trailing-whitespace rules only
  to exact files changed by the admitted `base...head` comparison;
- `full` keeps repository-wide semantic validation but treats formatting as a
  non-blocking historical concern, so generated or pre-existing whitespace debt
  cannot false-red an otherwise safe full source validation.

Malformed YAML remains a failure in every profile, including an unchanged file
inside a selected target. Failure detail is limited to the bounded path/reason;
file contents are never copied into evidence.

## SOPS source structure

A YAML target may register exact `sops_files` paths or bounded repository-relative
glob patterns. Plan validation resolves those patterns inside the admitted
source tree and requires at least one match when a target declares SOPS source.
Execution additionally requires every matched file to belong to the same
reviewed target include surface before applying the existing encrypted MAC,
version, and encrypted `data`/`stringData` checks.

This is structure validation only. It requires no decryption key and performs no
decryption, live Secret lookup, cluster access, or plaintext output. Consumer
contracts choose the patterns; Central does not branch on product or Secret
names.

## Determinism and ownership

YAML documents are normalized as sorted canonical JSON. Helm and Kustomize are
run twice with fixed arguments and isolated state; byte drift fails before
normalization. Optional checked-in expected renders are compared after
normalization. Every Kubernetes object contributes
`apiVersion/kind/namespace/name`.

Duplicate identities inside one target always fail. Two source targets, two
render targets, or disjoint source/render objects also fail on duplicate
identity. One raw YAML document may overlap exactly one Helm or Kustomize render
only when that document's exact bounded source file is physically inside the
renderer root. The source path is preserved through composition solely for this
ownership decision. This lets semantic source validation compose with the
render of the same checked-in object without permitting a same-named object from
another directory to bypass duplicate ownership.

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
and cleanup. `flux-source` composes a repository-root YAML source audit with a
local `clusters/devops` Kustomize render, contract-owned encrypted Secret globs,
and changed-file-only formatting while retaining whole-target semantic parsing.
It receives no live Flux or Kubernetes authority. `agent-state` has a bounded
Helm shape. The current `iptv-backend` consumer is recorded as source-audit-only
until its external Valkey dependency has an immutable vendoring/content
contract; product-owned validation remains authoritative in the interim.
