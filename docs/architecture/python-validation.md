# Python validation architecture

`validation.python` is a product-neutral language capability built on the shared source, runner, workspace, runtime, language, policy, and `ciw` contracts:

`consumer trigger → source.resolve → reusable-python → ciw python validate → consumer-owned executable script`

## Authority boundaries

`contracts/python-validation.json` owns only generic mechanics:

- four validation profiles and their trust, semantic runner, timeout, workspace, isolation, dependency, and PostgreSQL behavior;
- the runner-provided host CPython family and exact container/service image identities;
- the bounded script contract (repository-relative, executable, no caller arguments, generic-only environment);
- allowed Python versions and optional exact dependency-file policy;
- the single generic PostgreSQL connection handoff `CIW_POSTGRES_URL`;
- failure codes, cleanup duties, disabled cache default, and zero-artifact policy.

It deliberately contains no repository-keyed `consumers` map, product command profiles, application test lists, product environment, dependency paths, feature policy, or release gates.

Consumer repositories remain authoritative for triggers/path filtering, application source, requirements, scripts, test selection, migrations, release gates, application configuration, and assertions. A consumer does not register with Central before using the API.

## Typed script boundary

The checked-in command library exposes exactly:

```text
ciw python validate --phase plan|execute --source-root source
```

Public intent reaches the action through bounded environment fields: admitted SHA, validation profile, Python version, optional working/version/dependency paths, required script path, execution backend, and the reserved empty artifact-exception field.

The caller cannot supply a module/function/handler, inline command, argument array, environment map, shell, runner label, container engine, runtime image, database URL/name, secret name, callback, or deletion root. Planning performs no product execution.

At execute time Central requires `script_path` to resolve inside the exact admitted source, rejects parent/symlink escape, and requires a real executable file. The runtime invokes that path with an empty caller argument vector and a fixed generic environment.

## Exact source and trust

The workflow receives a full lowercase SHA already admitted by the source contract. It checks out only that commit with no persisted credentials and verifies exact/clean HEAD before execution and again after cleanup.

Source trust is derived from GitHub event metadata:

- same-repository pull requests → `trusted-pr`;
- fork pull requests → `untrusted-fork`;
- exact push/workflow-dispatch tuples → `trusted-exact`.

Only `audit` accepts `untrusted-fork`. `podman` and `podman-postgres` require `trusted-exact` before privileged Buildah capacity is resolved.

## Runner/backend planning

Profile-owned semantic intent is:

| Profile | Semantic runner | Hosted backend |
|---|---|---|
| `audit` | `portable` | allowed |
| `host` | `portable` | allowed |
| `podman` | `buildah-high` | rejected |
| `podman-postgres` | `buildah-medium` | rejected |

`execution_backend` remains the existing bounded `organization | github-hosted` selector. Central resolves the exact `runs_on_json`; callers never provide infrastructure labels. There is no second Python-specific runner/backend mechanism.

## Runtime identities

| ID | Identity |
|---|---|
| `host-cpython-3.12` | runner-provided CPython `3.12.x` on Linux/x64 |
| `python-3.12.8-slim-amd64` | exact digest-pinned `docker.io/library/python:3.12.8-slim` |
| `python-3.12.13-slim-amd64` | exact digest-pinned `docker.io/library/python:3.12.13-slim` |
| `postgres-16.11-alpine-amd64` | exact digest-pinned `docker.io/library/postgres:16.11-alpine` |

Host validation accepts the reviewed 3.12 family and reports the actual patch. Another major/minor fails before restore. Central never installs/elevates a host runtime to repair drift.

Container profiles require the exact reviewed version input and map it to exactly one digest-pinned linux/amd64 image; callers cannot provide an image reference.

## Dependency restore

`dependency_file` is optional for `host`, `podman`, and `podman-postgres`; `audit` rejects it. It must be normalized and repository-relative.

Validation recursively inspects bounded requirement includes and rejects symlink/parent escape, cycles, editable/VCS dependencies, and floating version ranges. Accepted material is exact-pinned (or exact hash-bound URL material). This keeps dependency choice in the consumer repository while Central owns the generic restore mechanism.

Copied-host execution uses shared Python primitives to resolve the interpreter, create a marker-bound venv, and install with `--no-input --no-cache-dir`. Container execution restores into disposable `/work` state. No Actions cache or workflow-owned persistent volume is introduced.

## Host isolation

Host execution rejects source symlinks, copies the admitted source into registered state, and isolates `HOME`, `TMPDIR`, XDG roots, and optional virtualenv beneath the marker-bound workspace. The only product execution is the checked-in consumer script. Product environment inherited from the runner is filtered out.

The original source is never the command working tree and must remain exact/clean after execution.

## Podman isolation

Podman profiles:

- reject Docker, `dockerd`, and Docker sockets;
- use Podman VFS with marker-bound private graph storage via `--root`;
- leave `--runroot` at the semantic Buildah runner's job-isolated default runroot (Podman 4.9 rejects overly long custom runroot paths; the known limit is 50 characters);
- pull exact digest-pinned linux/amd64 images;
- mount admitted source read-only at `/src`, then copy it to disposable `/work/source`;
- use mode-0600 environment files containing only generic values;
- optionally restore the consumer's bounded exact dependency file;
- invoke only the checked-in consumer script;
- retain no cache and publish no artifact.

## PostgreSQL isolation

`podman-postgres` creates one ephemeral PostgreSQL service with per-run random password, fixed non-production generic username/database, one isolated network, and one isolated volume. Readiness is bounded to 30 attempts at two-second intervals.

The validation container receives exactly one generic connection variable: `CIW_POSTGRES_URL`, using a `postgresql://` URL. Consumer-selected database variable names/schemes/credentials/remote endpoints are forbidden. The URL is not a public input/output/evidence field.

Readiness failure produces `postgres_readiness_timeout` and still enters unconditional cleanup.

## Failure classification

Runtime/setup failures, dependency restoration, script invocation, source drift, policy, and PostgreSQL readiness project to stable generic `PythonValidationError.code` values. Script stderr may be internally classified into bounded diagnostic categories (network/TLS/tool/integrity/general) but arbitrary product text is not promoted to API output.

## Policy and cleanup

Before and after product execution, Central verifies repository policy and exact clean source. The reusable path does not create routine evidence artifacts.

Podman cleanup removes and residue-checks validation/PostgreSQL containers, isolated network/volume, pulled runtime images, environment files, and private storage state. Workspace cleanup runs under `if: always()` with marker-bound no-follow semantics. A final unconditional source check proves exact HEAD and complete cleanliness.

## Deliberate exclusions

The Python API does not own consumer triggers, path filters, command composition, test selection, application environment, product feature policy, release/deployment behavior, or repository registration. It adds no arbitrary shell payload, setup-python action, package-manager runtime installation, privilege elevation, Actions cache, workflow-owned PV, routine artifact, signing, publication, or deployment authority.
