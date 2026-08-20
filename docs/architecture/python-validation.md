# Python validation architecture

`validation.python` is the language-specific compatibility workflow built on the merged source, runner, workspace, runtime, language, and `ciw` contracts:

`consumer trigger → source.resolve → reusable-python → ciw python validate → checked-in product command profile`

## Authority boundaries

`contracts/python-validation.json` owns:

- four public profiles and their trust, runner, timeout, workspace, isolation, dependency, and PostgreSQL behavior;
- the runner-provided host Python family and exact container/service image identities;
- reviewed command-profile names and current consumer compatibility mappings;
- bounded environment values and paths;
- failure codes, cleanup duties, disabled cache default, and zero-artifact policy.

Consumer repositories remain authoritative for application source, requirements and constraints, scripts, release gates, migrations, test selections, application configuration, and product-specific assertions. Central records reviewed current shapes but does not copy product code or expose arbitrary command dispatch.

## Typed command boundary

The checked-in command registry exposes exactly:

```text
ciw python validate --phase plan|execute --source-root source
```

The action supplies public inputs through validated environment fields. The caller cannot name a module, function, handler, arbitrary command, shell, runner, engine, image, database, or deletion root. Planning performs no product execution. It resolves one `PythonValidationPlan` and asks the runner contract to translate profile-owned intent into an internal JSON selector. Execution consumes the same checked-in contract.

Expected failures use `PythonValidationError.code`; unexpected exceptions are projected to the fixed redacted `ciw_unexpected_failure` code.

## Exact source and trust

The workflow receives a full lowercase SHA already admitted by the source contract. It checks out only that commit with no persisted credentials and verifies exact HEAD before execution and again after cleanup.

Source trust is derived from GitHub event metadata:

- same-repository pull requests become `trusted-pr`;
- fork pull requests become `untrusted-fork`;
- push and workflow-dispatch exact tuples become `trusted-exact`.

Only `audit` accepts `untrusted-fork`. Podman and PostgreSQL profiles require `trusted-exact` before resolving to privileged Buildah capacity.

## Runner planning

The planning job uses `[linux, amd64, general, small]`. The checked-in Python profile selects semantic runner intent:

| Profile | Semantic runner |
|---|---|
| `audit` | `portable` |
| `host` | `portable` |
| `podman` | `buildah-high` |
| `podman-postgres` | `buildah-medium` |

The runner contract binds these intents to reviewed internal selectors. The validation job consumes only `${{ fromJSON(needs.plan.outputs.runs_on_json) }}`; callers cannot supply labels.

## Runtime identities

The current contract records:

| ID | Identity |
|---|---|
| `host-cpython-3.12` | runner-provided CPython `3.12.x` on Linux/x64; the exact patch is validated and reported at execution time |
| `python-3.12.8-slim-amd64` | `docker.io/library/python:3.12.8-slim@sha256:d0af71d7d6d1b7bb018395aca582e4d270d090ca41312ae5318341f122fec6b8` |
| `python-3.12.13-slim-amd64` | `docker.io/library/python:3.12.13-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de` |
| `postgres-16.11-alpine-amd64` | `docker.io/library/postgres:16.11-alpine@sha256:63cf43b1a12b5b9d1f7a576f8f5852d7ce0792618661969135ac6c276237eec9` |

Container and service profiles require those exact image identities. Host validation deliberately accepts the operational CPython 3.12 family rather than one stale patch: `3.12.x` passes, another major/minor fails before restore, and Central never installs or elevates a host runtime to repair drift.

The immutable `validate-python` action SHA distributes reviewed Central code. It is not an immutable host-runtime, provenance, or product acceptance proof.

## Shared Python primitives

Copied-host execution uses shared Python primitives introduced by the generic language/runtime foundation:

- `resolve_python_interpreter` selects an absolute pre-provisioned `python3.12` or `python3` executable;
- `create_python_venv` creates a registered venv without a workflow-authored setup step;
- `install_python_dependencies` installs the exact product-owned requirement file with `--no-input --no-cache-dir`;
- `run_python_tests`, `run_python_module`, and `run_python_script` execute reviewed Python argv;
- the shared runtime `run_process` boundary applies exact argv, bounded environment, working directory, and plan timeout.

A checked-in non-Python product command, such as `./scripts/run_release_gates.sh`, remains exact argv and runs through the same runtime boundary. The reusable workflow itself does not author product test commands.

## Dependency restore

A command profile declares whether an exact dependency file is required. Dependency validation rejects editable installs, VCS dependencies, floating version ranges, and unbounded includes. It accepts exact pins and recursively validates bounded repository-relative requirement includes. The normalized lock material remains deterministic. Cache transport is disabled, no Actions cache is used, and no workflow-owned persistent volume is created.

## Host isolation

Host profiles copy the exact source tree into registered temporary state after rejecting source symlinks. They isolate `HOME`, `TMPDIR`, XDG roots, and the virtual environment beneath the marker-bound root. Commands run from the copied tree. The original source is reverified as the exact clean Git tree after execution and cleanup.

## Podman isolation

Podman profiles retain the established behavior:

- reject Docker, `dockerd`, and Docker sockets;
- use Podman with explicit `vfs` and marker-bound private graph storage through `--root`;
- leave `--runroot` unset so Podman uses the semantic Buildah runner's job-isolated default runroot, because Podman 4.9 rejects custom runroot paths longer than 50 characters;
- pull exact digest-pinned linux/amd64 images;
- mount caller source read-only at `/src`;
- copy source into disposable `/work/source` before restore or commands;
- use a mode-0600 environment file containing bounded contract values;
- retain no cache and publish no artifact.

## PostgreSQL isolation

`podman-postgres` creates ephemeral per-execution PostgreSQL credentials: a random password, a fixed non-production username/database, one isolated network, one isolated data volume, and one service container. The database URL is injected only into the contract-selected environment variable for the validation container. It is never an input, output, log summary, evidence field, or remote fallback.

Readiness is bounded to 30 attempts at two-second intervals. Failure produces `postgres_readiness_timeout` and still enters unconditional cleanup.

## Policy and cleanup

Before and after product execution, repository policy checks exact clean source, tracked-secret/forbidden-file policy, generated-output agreement, and zero artifacts. The workflow does not create a separate routine evidence manifest.

Podman cleanup removes and verifies the absence of validation and PostgreSQL containers, isolated network, data volume, and pulled runtime images. Workspace cleanup runs under `if: always()` and uses the marker-bound, descriptor-anchored, no-follow implementation. A final unconditional source check verifies exact HEAD and complete cleanliness. Any unsafe target or residue fails the workflow.

## Deliberate exclusions

The Python API does not implement Node, Android, Flutter, Apple, devices, GitOps, OCI/Helm publication, release, deployment, Flux reconciliation, Agent State decisions, or consumer migrations. It adds no setup-python action, package-manager runtime installation, privilege elevation, Actions cache, workflow-owned PV, routine artifact, signing, publication, or deployment authority. Flux owns runner deployment, resources, and shared-volume caching.
