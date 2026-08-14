# Python validation architecture

`validation.python` is the first language-specific reusable workflow built on the merged source, runner, foundation, and `ciw` contracts. The architecture is deliberately narrow:

`consumer trigger → source.resolve → reusable-python → ciw python validate → checked-in command profile`

## Authority boundaries

`contracts/python-validation.json` is the Python validation authority. It owns:

- the four public profiles and their trust, runner, timeout, workspace, isolation, dependency, and PostgreSQL behavior;
- exact Python and PostgreSQL runtime identities;
- reviewed command-profile names and consumer compatibility fixtures;
- bounded environment values and paths;
- failure codes, cleanup duties, cache default, and artifact policy.

Consumer repositories remain authoritative for application source, requirement and constraint files, scripts, release gates, migrations, test selections, application configuration, and product-specific assertions. The central contract records current shapes but does not copy product code or provide arbitrary command dispatch.

## Typed command boundary

Issue #9 extends the checked-in command registry with exactly one entry:

```text
ciw python validate --phase plan|execute --source-root source
```

The action supplies the public inputs through validated environment fields. The caller cannot name a module, function, handler, command, shell, runner, engine, image, database, or deletion root. The `plan` phase performs no product execution. It resolves one `PythonValidationPlan` and asks the runner contract to translate the profile-owned runner intent into an internal JSON selector. The `execute` phase consumes the same checked-in contract and runs the selected plan.

The Python domain uses `PythonValidationError.code` without changing the shared `CIWResult` or `CIWError` design. Unexpected exceptions are projected to the existing fixed redacted `ciw_unexpected_failure` code.

## Exact source and trust

The workflow receives a full lowercase SHA that was already admitted by the source contract. It checks out only that exact commit with no persisted credentials and verifies exact HEAD before execution and again after cleanup.

Source trust is derived from GitHub event metadata rather than caller input:

- same-repository pull requests become `trusted-pr`;
- fork pull requests become `untrusted-fork`;
- push, workflow dispatch, and workflow-call exact tuples become `trusted-exact`.

Only `audit` accepts `untrusted-fork`. Podman and PostgreSQL profiles require `trusted-exact` before they can resolve to privileged Buildah capacity.

## Runner planning

The planning job itself always uses `portable`. The checked-in Python profile selects only semantic runner intent:

| Profile | Semantic runner |
|---|---|
| `audit` | `portable` |
| `host` | `portable` |
| `podman` | `buildah-high` |
| `podman-postgres` | `buildah-medium` |

The runner contract binds `validation.python` to these three runner profiles with `profile-contract` strategy. Buildah-medium and Buildah-high explicitly list the Python API as allowed. The canonical harness accepts only the exact planner expression `${{ fromJSON(needs.plan.outputs.runs_on_json) }}`; all other dynamic runner expressions remain forbidden.

## Runtime identities

The initial contract records reviewed immutable identities:

| ID | Identity |
|---|---|
| `host-cpython-3.12.13` | exact CPython `3.12.13`, verified before host execution |
| `python-3.12.8-slim-amd64` | `docker.io/library/python:3.12.8-slim@sha256:d0af71d7d6d1b7bb018395aca582e4d270d090ca41312ae5318341f122fec6b8` |
| `python-3.12.13-slim-amd64` | `docker.io/library/python:3.12.13-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de` |
| `postgres-16.11-alpine-amd64` | `docker.io/library/postgres:16.11-alpine@sha256:63cf43b1a12b5b9d1f7a576f8f5852d7ce0792618661969135ac6c276237eec9` |

Mutable tags without these exact digests are rejected. The caller cannot replace or override any runtime identity.

## Dependency restore

A command profile declares whether an exact dependency file is required. Dependency validation rejects editable installs, VCS dependencies, floating version ranges, and unbounded includes. It accepts exact pins and recursively validates bounded repository-relative `-r`/`--requirement` and `-c`/`--constraint` files. The normalized lock material produces deterministic evidence and installation uses `--no-input --no-cache-dir`. Cache transport remains disabled.

## Host isolation

Host profiles copy the complete exact source tree into registered temporary state after rejecting source symlinks. They isolate `HOME`, `TMPDIR`, XDG roots, and any virtual environment under the marker-bound root. Commands execute from the copied tree. The original source remains read-only by convention and is reverified as an exact clean Git tree after execution.

## Podman isolation

Podman profiles require the central Buildah classes and:

- reject Docker, `dockerd`, and Docker sockets;
- use Podman with explicit `vfs` and marker-bound private graph storage through `--root`; `--runroot` is deliberately left unset so Podman uses the semantic Buildah runner's job-isolated default runroot, because Podman 4.9 rejects custom runroot paths longer than 50 characters;
- pull exact digest-pinned linux/amd64 images;
- mount caller source read-only at `/src`;
- copy source into disposable `/work/source` before installing or running commands;
- use a mode-0600 environment file containing only bounded contract values;
- retain no cache and publish no artifact.

## PostgreSQL isolation

`podman-postgres` creates ephemeral per-execution PostgreSQL credentials: a random password with `secrets.token_urlsafe`, a fixed non-production username/database, one isolated Podman network, one isolated data volume, and one service container. The database URL is injected only into the contract-selected environment variable for the validation container. It is never an input, output, log summary, evidence field, or remote fallback.

Readiness is bounded to 30 attempts at two-second intervals. The service image and Python image are exact. A failure to become ready produces `postgres_readiness_timeout` and still enters unconditional cleanup.

## Policy, evidence, and cleanup

Before and after product execution, the shared repository policy checks exact clean source, tracked-secret/forbidden-file policy, generated-output agreement, and zero artifacts. Evidence records only the exact source SHA, workflow release, semantic runner, verified toolchain, command-profile ID, result, and cleanup state.

Podman cleanup removes and then verifies the absence of all validation and PostgreSQL containers, the isolated network, data volume, and pulled runtime images. Workspace cleanup runs under `if: always()` and uses the merged marker-bound, descriptor-anchored, no-follow implementation. Any unsafe target or residue fails the workflow. The final source equality and clean-tree check executes after cleanup.

## Deliberate exclusions

The Python API does not implement Node, Android, Flutter, Apple, devices, GitOps, OCI/Helm publication, release, deployment, Flux reconciliation, Agent State decisions, canonical Supabase, or consumer migrations. It does not modify `.github/workflows/self-check.yml`. Future language APIs must extend the same named-function, exact-source, runner-planning, deterministic-contract, and cleanup model without broadening this command surface.
