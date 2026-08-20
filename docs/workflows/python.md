# Python validation workflow

Public API: `validation.python` `1.0.0`  
Workflow: `.github/workflows/reusable-python.yml`  
Stable check: `CI / Python validation`

The reusable Python workflow validates one exact source SHA through a checked-in command profile. It does not accept arbitrary shell, callback, runner label, container-engine, image, storage-driver, database, registry, secret-name, publication, deployment, Helm, Flux, or cluster inputs.

## Execution backend

`execution_backend` is optional and defaults to `organization`, preserving the existing semantic runner resolution exactly. `github-hosted` is a bounded scheduling opt-in owned by Central; callers do not provide `ubuntu-latest` or any other concrete runner label.

The `audit` and `host` validation profiles both resolve to the existing `general-small` execution profile, so they may run their substantial validation job on the fixed standard `ubuntu-latest` hosted runner. Their source trust, commands, Python runtime contract, dependency restore, workspace isolation, cleanup, and outputs remain unchanged.

`podman` and `podman-postgres` retain their existing Buildah-backed Podman semantics. A request combining either profile with `github-hosted` fails closed as an unsupported backend/profile combination. Central does not substitute Docker, a GitHub service container, or another engine merely to make those profiles hosted.

The small trusted planning job remains on organization general capacity and emits the exact Central-owned `runs_on_json` consumed by the validation job. Repository visibility never chooses a backend automatically.

## Public inputs

| Input | Required | Meaning |
|---|---:|---|
| `execution_backend` | no | `organization` or `github-hosted`; default `organization`. |
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

`audit` uses semantic `portable`. Under the default organization backend that preserves the reviewed organization selector; under explicit `github-hosted` it maps to the fixed standard hosted Linux selector. It copies exact source into workflow-scoped state and executes only reviewed source checks. It is the only profile permitted for untrusted fork source. The current Flux issue-ledger validation shape is represented by the checked-in `source-audit` fixture.

### `host`

`host` also uses `portable`. It requires runner-provided CPython 3.12 on Linux/x64, accepts any actual `3.12.x` patch, and reports the resolved patch in the bounded result. A non-3.12 interpreter fails before dependency restore. The workflow never installs, downloads, or elevates a host interpreter.

Host validation creates isolated `HOME`, `TMPDIR`, XDG roots, and an optional virtual environment beneath registered workflow state. The shared Python primitives resolve the interpreter, create the venv, install the product-owned exact requirements with `--no-input --no-cache-dir`, and run reviewed Python modules or tests. Non-Python product commands, such as the Backend release gate, remain exact checked-in argv from the compatibility contract.

Current Agent State source checks and the Backend same-repository pull-request gate use this shape.

### `podman`

`podman` requires `trusted-exact` source and resolves centrally to `buildah-high`. The caller cannot select labels or engines. The existing path verifies Podman availability, rejects Docker/DinD and Docker sockets, uses the `vfs` storage driver in marker-bound state, mounts caller source read-only, copies it into disposable work state, and runs an exact digest-pinned Python image. Current Backend full validation uses this shape. `execution_backend: github-hosted` is rejected for this profile.

### `podman-postgres`

`podman-postgres` requires `trusted-exact` source and resolves centrally to `buildah-medium`. It adds one exact digest-pinned PostgreSQL 16.11 service, generated per-run credentials, one isolated network, one isolated data volume, bounded readiness, and no remote database fallback. Current Backend and Agent State PostgreSQL validation shapes are represented by checked-in command fixtures. `execution_backend: github-hosted` is rejected for this profile.

## Code distribution and runtime authority

Private consumers do not clone `StreamScapeTV/ci-workflows` with caller-scoped credentials. The planner and executor invoke `validate-python` directly at immutable #405 execution-backend checkpoint `34d736612462f7ab4e7be83443760c59027478db`. The final candidate records that identity in `contracts/action-tool-lock.json`.

That SHA fixes the reviewed Central code bundle; it is not a product-facing immutable-runtime or provenance acceptance gate. Host runtime authority comes from the checked-in `host-cpython-3.12` family contract and the selected runner's pre-provisioned interpreter. Container and PostgreSQL profiles retain their exact image identities.

Exact checkout, marker-bound workspace preparation, and cleanup use foundation checkpoint `70e08d4ddf8930046632a7135950e924b82e22bf`. The Python workflow does not run a separate toolchain-proof action or create a routine evidence manifest. It exposes no `.ciw` checkout, central PAT, caller secret, `secrets: inherit`, mutable helper ref, or caller-selected Central version.

## Execution sequence

1. A general-Linux planning job calls the immutable `validate-python` code checkpoint with bounded intent, including `execution_backend`.
2. The planner validates caller identity, source trust, validation profile, command profile, and the existing semantic runner intent.
3. Central backend resolution preserves the organization selector or maps a supported portable profile to the fixed hosted selector; unsupported combinations fail before execution.
4. The dependent validation job is scheduled from the exact JSON selector emitted by the planner.
5. The job checks out only the exact admitted caller source and creates marker-bound state with cache mode disabled.
6. `ciw python validate --phase execute` revalidates source, version and dependency contracts, and repository policy.
7. A copied-host plan uses runner-provided CPython 3.12 through shared runtime/language primitives; Podman plans retain their existing VFS and PostgreSQL isolation.
8. Product requirements, scripts, release gates, and test argv come from the product repository through the checked-in compatibility mapping, not from workflow-authored generic commands.
9. `cleanup-workspace` runs under `if: always()`, removes all registered state, and fails on residue or unsafe targets.
10. A final unconditional step proves exact source equality and a completely clean tree.

No Actions cache, workflow-owned persistent volume, routine artifact upload, runtime installation, privilege elevation, publication, or deployment operation is part of this path. Flux owns organization runner deployment, resources, and shared-volume caching.

## Consumer example

A consumer trigger remains responsible for exact source admission and passes only bounded intent. Existing consumers need no new input:

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

A public consumer may opt into hosted compute without supplying a runner label:

```yaml
    with:
      execution_backend: github-hosted
      admitted_sha: ${{ needs.source.outputs.source_sha }}
      validation_profile: host
      command_profile: locked-test
      working_directory: .
      script_path: scripts/run_release_gates.sh
```

Backend, Agent State, and Flux keep product-specific commands, dependency files, scripts, test selections, application configuration, and assertions in their own repositories. The compatibility contract records reviewed current shapes without moving that product authority into the reusable workflow.

## Failure and cleanup

Expected rejection exits with code `2` and a stable `PythonValidationError.code`. Invalid backend input projects to the existing `invalid_input` code; an unsupported hosted/profile combination projects to existing `unsupported_profile`. The internal `failure_code` projection never includes arbitrary exception text. Podman cleanup removes and verifies validation/service containers, network, data volume, pulled images, storage state, environment files, and processes. Marker-bound workspace cleanup then removes all remaining registered paths. Routine Actions artifacts remain zero.
