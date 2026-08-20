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

- `result`: `success` only when bounded validation, registered-state cleanup, and the final exact clean-source check all succeed.
- `test_summary`: deterministic compact JSON containing only profile, command-profile, status, and stage count.
- `artifact_exception_used`: `false` for the initial zero-artifact API.

No command output, database URL, generated credential, host path, image pull detail, or container identity is exposed through outputs.

## Profiles

### `audit`

`audit` uses the semantic `portable` runner. It copies exact source into workflow-scoped state and executes only reviewed source checks. It is the only profile permitted for untrusted fork source. The current Flux issue-ledger validation shape is represented by the checked-in `source-audit` fixture.

### `host`

`host` also uses `portable`, which resolves to the reviewed general Linux capacity. It requires runner-provided CPython 3.12 on Linux/x64, accepts any actual `3.12.x` patch, and reports the resolved patch in the bounded result. A non-3.12 interpreter fails before dependency restore. The workflow never installs, downloads, or elevates a host interpreter.

Host validation creates isolated `HOME`, `TMPDIR`, XDG roots, and an optional virtual environment beneath registered workflow state. The shared Python primitives resolve the interpreter, create the venv, install the product-owned exact requirements with `--no-input --no-cache-dir`, and run reviewed Python modules or tests. Non-Python product commands, such as the Backend release gate, remain exact checked-in argv from the compatibility contract.

Current Agent State source checks and the Backend same-repository pull-request gate use this shape.

### `podman`

`podman` requires `trusted-exact` source and resolves centrally to `buildah-high`. The caller cannot select labels or engines. The existing path verifies Podman availability, rejects Docker/DinD and Docker sockets, uses the `vfs` storage driver in marker-bound state, mounts caller source read-only, copies it into disposable work state, and runs an exact digest-pinned Python image. Current Backend full validation uses this shape.

### `podman-postgres`

`podman-postgres` requires `trusted-exact` source and resolves centrally to `buildah-medium`. It adds one exact digest-pinned PostgreSQL 16.11 service, generated per-run credentials, one isolated network, one isolated data volume, bounded readiness, and no remote database fallback. Current Backend and Agent State PostgreSQL validation shapes are represented by checked-in command fixtures.

## Code distribution and runtime authority

Private consumers do not clone `StreamScapeTV/ci-workflows` with caller-scoped credentials. The planner and executor invoke `validate-python` directly at immutable checkpoint `aece8d01efdd5482a1c3d42db357aed87a7917e9`, recorded in `contracts/action-tool-lock.json` as `issue #235 general-runner Python primitives checkpoint`.

That SHA fixes the reviewed Central code bundle; it is not a product-facing immutable-runtime or provenance acceptance gate. Host runtime authority comes from the checked-in `host-cpython-3.12` family contract and the pre-provisioned general runner. Container and PostgreSQL profiles retain their exact image identities.

Exact checkout, marker-bound workspace preparation, and cleanup use the foundation checkpoint `70e08d4ddf8930046632a7135950e924b82e22bf`. The Python workflow does not run a separate toolchain-proof action or create a routine evidence manifest. It exposes no `.ciw` checkout, central PAT, caller secret, `secrets: inherit`, mutable helper ref, or caller-selected Central version.

## Execution sequence

1. A general-Linux planning job calls the immutable `validate-python` code checkpoint with bounded intent.
2. The planner validates caller identity, source trust, profile, command profile, and contract-owned runner intent.
3. The dependent validation job is scheduled from the exact JSON selector emitted by the planner.
4. The job checks out only the exact admitted caller source and creates marker-bound state with cache mode disabled.
5. `ciw python validate --phase execute` revalidates source, version and dependency contracts, and repository policy.
6. A copied-host plan uses the runner-provided CPython 3.12 family through shared runtime/language primitives; Podman plans retain their existing VFS and PostgreSQL isolation.
7. Product requirements, scripts, release gates, and test argv come from the product repository through the checked-in compatibility mapping, not from workflow-authored generic commands.
8. `cleanup-workspace` runs under `if: always()`, removes all registered state, and fails on residue or unsafe targets.
9. A final unconditional step proves exact source equality and a completely clean tree.

No Actions cache, workflow-owned persistent volume, routine artifact upload, runtime installation, privilege elevation, publication, or deployment operation is part of this path. Flux owns runner deployment, resources, and shared-volume caching.

## Consumer example

A consumer trigger remains responsible for exact source admission and passes only bounded intent:

```yaml
jobs:
  python:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-python.yml@main
    with:
      admitted_sha: ${{ needs.source.outputs.source_sha }}
      validation_profile: host
      command_profile: locked-test
      working_directory: .
      script_path: scripts/run_release_gates.sh
```

Backend, Agent State, and Flux keep product-specific commands, dependency files, scripts, test selections, application configuration, and assertions in their own repositories. The compatibility contract records reviewed current shapes without moving that product authority into the reusable workflow.

## Failure and cleanup

Expected rejection exits with code `2` and a stable `PythonValidationError.code`. The internal `failure_code` projection never includes arbitrary exception text. Podman cleanup removes and verifies validation/service containers, network, data volume, pulled images, storage state, environment files, and processes. Marker-bound workspace cleanup then removes all remaining registered paths. Routine Actions artifacts remain zero.
