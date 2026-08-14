# Python validation workflow

Public API: `validation.python` `1.0.0`  
Workflow: `.github/workflows/reusable-python.yml`  
Stable check: `CI / Python validation`

The reusable Python workflow validates one exact source SHA through a checked-in command profile. It does not accept arbitrary shell, callback, runner, container-engine, image, storage-driver, database, registry, secret-name, publication, deployment, Helm, Flux, or cluster inputs.

## Public inputs

| Input | Required | Meaning |
|---|---:|---|
| `admitted_sha` | yes | Exact lowercase commit admitted by `source.resolve`. |
| `validation_profile` | yes | One of `audit`, `host`, `podman`, or `podman-postgres`. |
| `command_profile` | yes | A reviewed command shape recorded in `contracts/python-validation.json`. |
| `working_directory` | no | Repository-relative directory that must exactly match the consumer contract; default `.`. |
| `version_file` | no | Repository-relative Python version file that must exactly match the consumer contract. |
| `script_path` | no | Repository-relative reviewed script path that must exactly match the consumer contract. |
| `artifact_exception_id` | no | Reserved central exception input. The initial Python API requires it to be empty and retains zero routine artifacts. |

## Public outputs

- `result`: `success` only when the bounded validation stage and terminal cleanup both succeed.
- `test_summary`: deterministic compact JSON containing only profile, command-profile, status, and stage count.
- `artifact_exception_used`: `false` for the initial zero-artifact API.

No command output, database URL, generated credential, host path, image pull details, or container identity is exposed through outputs.

## Profiles

### `audit`

`audit` uses the semantic `portable` runner. It copies exact source into workflow-scoped state and executes only source-only reviewed checks. It is the only profile permitted for untrusted fork source. The current Flux issue-ledger validation shape is represented by the checked-in `source-audit` fixture.

### `host`

`host` uses `portable`, verifies exact CPython `3.12.13`, creates an isolated home, temporary directory, XDG roots, and optional virtual environment, and runs only the selected checked-in profile. Current Agent State automation checks use this shape.

### `podman`

`podman` requires `trusted-exact` source and resolves centrally to `buildah-high`. The caller cannot select labels or engines. The workflow verifies Podman availability, rejects Docker/DinD and Docker sockets, uses the `vfs` storage driver in marker-bound state, mounts caller source read-only, copies it into disposable work state, and runs an exact digest-pinned Python image. Current Backend full validation uses this shape.

### `podman-postgres`

`podman-postgres` requires `trusted-exact` source and resolves centrally to `buildah-medium`. It adds one exact digest-pinned PostgreSQL 16.11 service, generated per-run credentials, one isolated network, one isolated data volume, bounded readiness, and no remote database fallback. Current Backend and Agent State PostgreSQL validation shapes are represented by checked-in command fixtures.

## Immutable private helper reuse

Private consumers do not clone `StreamScapeTV/ci-workflows` with the caller-scoped token. The planner and executor invoke reviewed central composite actions directly through immutable full-SHA references. `validate-python` is pinned to `e869906a0b192ab954dce3dbd1e90cccb649eb18`, recorded in the action lock as `issue #155 immutable Backend execution repair checkpoint`. `verify-toolchain` remains pinned to `70e08d4ddf8930046632a7135950e924b82e22bf`, recorded as `issue #125 immutable private-action checkpoint`. Exact checkout, workspace preparation, evidence rendering, and cleanup reuse the immutable foundation checkpoint established by #116.

The action archives resolve central scripts and libraries through `GITHUB_ACTION_PATH`. No `.ciw` checkout, central PAT, caller secret, `secrets: inherit`, mutable helper ref, or caller-selected central version is exposed. Exact caller source remains separate: it is admitted by `source.resolve`, checked out through the immutable exact-checkout action, and verified clean after terminal cleanup.

## Execution sequence

1. A portable planning job invokes the immutable private `validate-python` action directly; it does not clone the central repository.
2. The planner validates caller identity, source trust, profile, command profile, and contract-owned runner intent.
3. The dependent validation job is scheduled from the exact JSON selector emitted by the planner.
4. The job checks out only the exact admitted caller source, then composes immutable private central helper actions by full SHA.
5. Shared workspace and toolchain actions create marker-bound state, disable caching, and verify the semantic runtime.
6. `ciw python validate --phase execute` verifies exact source, version and dependency locks, repository policy, and then executes the reviewed plan in copied host state or Podman VFS state.
7. Deterministic redacted evidence is written beneath registered state.
8. `cleanup-workspace` runs under `if: always()`, removes all registered state, and fails on residue or unsafe targets.
9. The workflow rechecks exact source equality and complete source cleanliness after cleanup.

## Consumer examples

A consumer trigger remains responsible for exact source admission and passes only bounded intent:

```yaml
jobs:
  python:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-python.yml@main
    with:
      admitted_sha: ${{ needs.source.outputs.source_sha }}
      validation_profile: podman-postgres
      command_profile: postgres-test
      working_directory: .
```

Backend, Agent State, and Flux keep product-specific commands, dependency files, scripts, test selections, and assertions in their own repositories. This workflow does not branch on product names outside the checked-in compatibility contract and issue #9 does not edit consumer repositories.

## Failure and cleanup

Expected rejection exits with code `2` and a stable `PythonValidationError.code`. The public `failure_code` field is available only inside the thin action contract and never includes arbitrary exception text. Podman cleanup removes and verifies validation/service containers, network, data volume, pulled images, storage/runroot state, environment files, and processes. Marker-bound workspace cleanup then removes all remaining registered paths. Routine Actions artifacts remain zero.
