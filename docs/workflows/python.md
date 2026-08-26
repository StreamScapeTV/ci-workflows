# Python validation workflow

Public API: `validation.python` `2.0.0`  
Workflow: `.github/workflows/reusable-python.yml`  
Stable check: `CI / Python validation`

`validation.python` is a product-neutral Python validation capability. Central owns exact source handling, bounded Python/runtime setup, optional dependency restoration, optional isolated PostgreSQL lifecycle, semantic runner/backend resolution, clean-tree enforcement, and terminal cleanup. The consumer owns its validation intent in one checked-in executable repository-relative script.

Central does **not** register repositories, products, command profiles, test lists, application environment, or release-gate argv. Callers cannot provide arbitrary inline shell, command arrays, environment maps, runner labels, container engines, secret names, database URLs, or runtime images.

## Execution backend

`execution_backend` is optional and defaults to `organization`. `github-hosted` is a bounded scheduling opt-in owned by Central; callers never provide a concrete hosted runner label.

`audit` and `host` use semantic `portable` capacity and may use the fixed hosted Linux backend when explicitly requested. `podman` and `podman-postgres` retain organization Buildah-backed Podman semantics and reject `github-hosted` because Central does not substitute Docker or another runtime.

The planner emits the Central-owned `runs_on_json`; repository visibility never selects a backend automatically.

## Public inputs

| Input | Required | Meaning |
|---|---:|---|
| `execution_backend` | no | `organization` or `github-hosted`; default `organization`. |
| `admitted_sha` | yes | Exact lowercase source SHA already admitted by `source.resolve`. |
| `validation_profile` | yes | `audit`, `host`, `podman`, or `podman-postgres`. |
| `python_version` | yes | `3.12` for host-family validation or an exact reviewed container version (`3.12.8` / `3.12.13`). |
| `working_directory` | no | Repository-relative working directory; default `.`. |
| `version_file` | no | Optional repository-relative Python version file used to verify runtime intent. |
| `dependency_file` | no | Optional repository-relative exact requirements file for profiles that allow restore. |
| `script_path` | yes | Repository-relative checked-in executable validation script owned by the consumer. |
| `artifact_exception_id` | no | Reserved compatibility field; callers leave it empty. |

No command-profile selector, inline argument/environment payload, caller-selected runner or engine, database endpoint, or secret-name field is part of this API.

## Public outputs

- `result`: `success` only after bounded validation and terminal cleanup/clean-source checks.
- `test_summary`: compact generic JSON containing only validation profile, script contract, status, and stage count.
- `artifact_exception_used`: compatibility output that remains `false` for this validation path.

Product output, command text, database credentials/URL, host paths, and container identities are never public outputs. Private command output and service credentials remain confined to the validation executor and bounded redacted diagnostics.

## Consumer-owned script contract

The script is the sole product execution hook. Central validates `script_path` as a normalized repository-relative path, rejects traversal and symlink escape, and requires a real executable file in the exact admitted source tree. It invokes the script with no caller-supplied arguments.

Only a small generic environment crosses the boundary (`CI`, locale/runtime isolation values, and the isolated runtime path state). Product-specific environment is not projected by Central.

Consumers therefore keep all product decisions in their repository: path filtering, test selection, release gates, migrations, feature flags, assertions, and any product-specific command composition.

## Dependency restore

`dependency_file` is optional for `host`, `podman`, and `podman-postgres`; `audit` rejects it. The path is repository-relative and bounded. Requirement validation rejects editable installs, VCS dependencies, floating ranges, parent/symlink escape, and cyclic/unbounded includes. Accepted entries are exact pins (or exact hash-bound URL material) and bounded requirement includes.

Host validation creates an isolated venv under registered workflow state and restores with `--no-input --no-cache-dir`. Container validation performs the same bounded restore inside disposable `/work` state. No Actions cache or workflow-owned persistent volume is introduced.

## Profiles

### `audit`

Uses semantic `portable` and copied-host-source isolation. It is the only profile admitted for untrusted fork source. It requires `python_version: 3.12`, no dependency file, and executes only the consumer-owned script in the isolated source copy.

### `host`

Uses semantic `portable`. The selected runner must already provide CPython 3.12 on Linux/x64; Central accepts the reviewed `3.12.x` family and reports the resolved patch. A non-3.12 interpreter fails before dependency restore. Runtime installation and privilege elevation are forbidden.

The runtime contract ID is `host-cpython-3.12`. Central isolates `HOME`, `TMPDIR`, XDG roots, and any venv beneath registered workflow state, then runs only the checked-in consumer-owned script from the copied source.

### `podman`

Requires `trusted-exact` source and organization `buildah-high` capacity. The caller selects an exact reviewed Python version, which maps to a reviewed `linux/amd64` Python image. Central rejects Docker/DinD and Docker sockets, uses Podman VFS private graph storage, mounts source read-only, copies it into disposable work state, and invokes only the consumer-owned script.

### `podman-postgres`

Requires `trusted-exact` source and organization `buildah-medium` capacity. It adds the reviewed PostgreSQL 16.11 image, one isolated network, one isolated volume, generated per-run credentials, and bounded readiness.

The consumer gets exactly one stable generic connection handoff: `CIW_POSTGRES_URL`. The URL uses the `postgresql` scheme and never appears as an input, output, evidence field, or remote fallback. Product-specific database environment variable names are not supported.

## Code distribution and runtime authority

Planner and executor invoke `StreamScapeTV/ci-workflows/actions/validate-python@main`. Central is one workflow/action library, so internal first-party helpers follow the active library branch rather than maintaining separate action checkpoint SHAs. Repository tags may identify a whole Central library release when a consumer intentionally chooses a released snapshot.

Host runtime authority still comes from `host-cpython-3.12` plus the selected runner's pre-provisioned interpreter; container/PostgreSQL authority comes from the reviewed identities in `contracts/python-validation.json`.

Exact checkout, marker-bound workspace preparation, and cleanup use the same Central library channel. The workflow exposes no Central clone, caller credential, `secrets: inherit`, or runtime installer.

## Execution sequence

1. Planner validates source trust, profile, `python_version`, bounded paths, `dependency_file`, and required `script_path`.
2. Central resolves the existing semantic organization runner or the bounded hosted backend where supported.
3. The validation job checks out only the exact admitted source and creates marker-bound state with cache disabled.
4. `ciw python validate --phase execute` revalidates exact/clean source, script executable identity, Python version intent, optional dependency lock, and repository policy.
5. Host execution uses runner-provided CPython 3.12; Podman execution uses reviewed images and disposable VFS state.
6. If requested, dependencies restore from the consumer-owned exact requirements file.
7. `podman-postgres` creates ephemeral PostgreSQL and injects only `CIW_POSTGRES_URL` into the validation container.
8. Central invokes exactly the checked-in consumer-owned executable script with no caller-supplied argv/environment payload.
9. Runtime/workspace cleanup is unconditional and residue-checked.
10. A final exact HEAD + clean-tree check closes the boundary.

## Consumer example

```yaml
jobs:
  python:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-python.yml@main
    with:
      admitted_sha: ${{ needs.source.outputs.source_sha }}
      validation_profile: host
      python_version: "3.12"
      dependency_file: requirements.txt
      script_path: ci/validate.sh
```

A PostgreSQL-backed exact-source caller changes only the generic profile/runtime intent; its own script decides which application tests constitute the gate:

```yaml
    with:
      admitted_sha: ${{ needs.source.outputs.source_sha }}
      validation_profile: podman-postgres
      python_version: "3.12.13"
      dependency_file: requirements.txt
      script_path: ci/validate-postgres.sh
```

A portable public consumer may add `execution_backend: github-hosted`; it still cannot supply a runner label.

## Failure and cleanup

Expected rejection uses stable `PythonValidationError.code` values. Script failures project to `command_failed`; dependency/runtime/source/PostgreSQL failures retain their bounded generic codes. Product exception text is not promoted to public outputs.

Podman cleanup removes validation/service containers, network, volume, pulled images, storage state, and temporary environment files. Marker-bound workspace cleanup then removes remaining registered paths. The original admitted source must remain exact and clean. Private command output, service URLs, credentials, and run-owned state remain non-public and are removed at the terminal boundary.
