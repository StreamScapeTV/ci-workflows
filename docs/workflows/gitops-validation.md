# GitOps source validation

`validation.gitops` version `1.0.0` is the product-neutral, source-only API for
YAML, Helm, Kustomize, changed-tree, and checked-in policy validation. Its
stable check is **CI / GitOps validation**. The semantic execution profile is
`portable` / `general-small`. A caller never selects a host, concrete label,
tool URL, command, registry, cluster, namespace, service account, or deployment
target.

## Execution backend

The reusable accepts the shared bounded `execution_backend` input:

- `organization` is the backwards-compatible default and resolves semantic
  `general-small` to `[linux, amd64, general, small]`;
- `github-hosted` explicitly resolves the same portable validation contract to
  standard GitHub-hosted `ubuntu-latest`;
- an explicit hosted request never falls back to StreamScapeTV organization or
  ARC capacity;
- repository visibility does not select a backend automatically.

Planner capacity follows the selected backend rather than creating a hidden
cross-backend dependency. An explicit `github-hosted` call uses a small
`ubuntu-latest` planner; the default/explicit `organization` call uses the
existing `general-small` organization planner. Those planners are mutually
exclusive and the validation job consumes only the successful planner's
Central-owned selector. Product source behavior, tools, cleanup, evidence, and
zero-artifact semantics are identical after selection.

## Profiles

| Profile | Contract-owned work |
|---|---|
| `source-audit` | Exact source and optional checked-in bounded policy script; no renderer. |
| `yaml` | Strict UTF-8/style, duplicate-key, YAML syntax, bounded JSON-schema, Kubernetes identity, and SOPS ciphertext/reference structure for every selected YAML source file. |
| `helm-render` | Exact dependency lock, vendored dependency identity, values schema, required values, strict lint, deterministic template, and expected-render checks. |
| `kustomize-render` | Local root-restricted Kustomize graph inspection and deterministic build/render. |
| `changed-tree` | Select contract targets intersecting the exact Git diff base. Every selected YAML file is still parsed semantically, while strict formatting applies only to files changed by that diff. |
| `full` | Validate every contract target and optional policy. YAML semantic safety remains repository-wide, while historical formatting is not promoted into a whole-repository gate. |

A target rooted at `.` is the repository root: repository-relative changed
paths such as `apps/...`, `clusters/...`, and `.github/workflows/...` select it
without requiring a synthetic `./` prefix. Non-root targets retain their exact
bounded prefix semantics.

The reusable workflow accepts only the bounded backend, an exact admitted SHA,
one registered consumer/profile, an exact base SHA for `changed-tree`, the
profile's exact policy identifier, and the reserved empty artifact-exception
field. Roots, values, schemas, tasks, scripts, tool identities, and assertions
are checked-in contract data.

## Immutable private helper reuse

Private same-organization consumers do not clone `StreamScapeTV/ci-workflows`
with their caller-scoped token. The reusable planner and execution job invoke
`validate-gitops` through immutable central revision
`8445e63dd9fa9468b60b6d0c61e543da9681b47b`; exact checkout, workspace
preparation, evidence rendering, and cleanup reuse the immutable foundation
actions established by #116. Backend resolution uses the reviewed immutable
Central execution-backend helper and accepts no caller runner selector.

The private action archives resolve their central scripts and Python modules
relative to `GITHUB_ACTION_PATH`, so there is no `.ciw` checkout, central PAT,
`secrets: inherit`, mutable helper ref, or caller-selected central version.
Caller source remains independently admitted and is checked out with the exact
checkout primitive. GitOps uses the contract maximum `fetch_depth: 1000`, which
preserves bounded changed-tree history while complying with the source-admission
contract's allowed 1–1000 range. The previous value `0` was outside that
foundation contract and is not retained.

## Exact tools

The Linux contract installs and verifies:

- Helm 3.18.6 from the fixed official HTTPS archive and SHA-256;
- Kustomize 5.8.1 from the fixed upstream release archive and SHA-256;
- PyYAML 6.0.3 from the exact CPython 3.12 manylinux wheel and SHA-256.

Archives are bounded, digest-verified before extraction, and inspected for
absolute paths, traversal, duplicate destinations, links, devices, and FIFO
members. The installed binary or Python module must resolve beneath the
registered issue-owned state root and report the exact contract version.

## Helm dependencies

Network dependency updates are not part of source validation. A chart with
dependencies must have a matching `Chart.lock`, exact dependency versions, and
contract-identified vendored dependency trees. The synthetic fixture uses a
checked-in `file://` library chart and a deterministic tree digest. Producer
adoption that currently downloads a dependency, including `iptv-backend`, must
first establish a reviewed immutable vendoring or equivalent content-digest
contract; this workflow does not silently reproduce mutable repository access.
The current `agent-state` chart shape has no dependency and is represented by a
bounded chart consumer entry.

## SOPS boundary

SOPS validation inspects ciphertext and reference structure only. Checked-in
` sops_files ` entries may be exact repository-relative paths or bounded
repository-relative glob patterns. Every matching file must be part of the
same target's reviewed include surface and must carry an encrypted MAC, a SOPS
version, and encrypted `data` or `stringData` values. Matching plaintext or
malformed files fail closed with only the bounded path/reason in evidence.

The validator never decrypts a SOPS document, receives an age/PGP/KMS key,
reads a Kubernetes Secret, or emits plaintext secret contents. SOPS matching is
consumer-contract data rather than a product-name allowlist.

## Flux and chart consumers

Flux remains authoritative for desired state, target allowlists, policy,
decryption, credentials, and live reconciliation. The `flux-source` contract
uses one repository-root YAML source target plus one local Kustomize target at
`clusters/devops`. Its YAML target validates `apps/**`, `clusters/**`, and
workflow YAML semantically, applies strict style only to changed YAML under
`changed-tree`, and requires matching `*.secret.yaml` source to retain SOPS
encrypted structure. `full` keeps the same semantic and render coverage without
requiring unrelated historical/generated whitespace normalization.

The Kustomize target uses only local root-restricted references and deterministic
double rendering. A raw YAML source audit may overlap its own nested render
root; that intentional source/render composition does not suppress duplicates
within one target, across two source targets, across two render targets, or
across disjoint roots. Selecting GitHub-hosted validation grants no Flux or
Kubernetes authority.

`iptv-backend` and `agent-state` remain owners of their chart values and product
assertions; central validation supplies only the bounded source/render
machinery.

## Cleanup and artifacts

Every archive, installed tool, Python package, HOME, cache, temporary file, log,
render, and result lives under one marker-bound registered root. Cleanup uses
`lstat`, never follows symlinks, never accepts a caller deletion path, and
checks zero residue. A primary validation failure and a cleanup failure are
both retained as a bounded combined error. Source SHA, Git cleanliness, and a
deterministic source tree digest are reverified after policy and render work.
Routine execution retains **zero routine artifacts**.
