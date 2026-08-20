# Bounded execution backends

Issue #405 adds compute-backend selection without turning runner labels into caller input.

## Public intent

The selected portable reusable workflows accept one optional string:

```yaml
execution_backend: organization # or github-hosted
```

Omitting the input is exactly equivalent to `organization`. Repository visibility never selects a backend automatically.

Callers cannot supply a runner label, `runs-on` value, runner group, host, container engine, storage driver, or arbitrary matrix. `ci-workflows` owns every concrete mapping.

## Separation from workload profiles

Execution backend and workload/profile semantics are independent dimensions.

- The ordinary semantic runner resolver first determines the existing execution profile and organization selector from the workload API, source trust, and validation profile.
- The bounded backend resolver then either preserves that exact organization selector or replaces it with the fixed reviewed standard hosted selector for combinations explicitly declared portable.
- `organization` never rewrites an existing selector.
- `github-hosted` currently maps supported Linux/x64 portable work to `ubuntu-latest`.
- Unknown backend values fail closed.
- A hosted backend request for an unsupported semantic profile fails closed instead of switching engines or weakening isolation.

For Python this means `audit` and `host` may use `github-hosted` because both retain the existing `general-small` semantics. `podman` and `podman-postgres` retain their current Buildah-backed isolation and reject `github-hosted`; Docker is not substituted for Podman.

## Initial API scope

Only these public APIs receive the new input in #405:

- `source.resolve`;
- `validation.node`;
- `validation.python`.

Android, Apple, Flutter, physical-device, signing, Flux/control-plane, OCI product publication, Helm publication, and other centralized workflows remain unchanged unless a separate reviewed issue adds a backend mapping.

## Planning and scheduling

Node and Python retain their existing trusted planning jobs. The planner resolves the ordinary workload contract and emits the exact `runs_on_json` used by the execution job. A hosted choice therefore changes only scheduling, not command/runtime/product policy.

`source.resolve` adds a minimal planning job because source admission previously used one fixed organization selector directly. The source-admission job consumes the same trusted planner output shape as the validation workflows.

The current planner jobs remain on the existing organization general capacity. They are small control-plane work and preserve the established trusted-planner architecture; the substantial source/Node/Python execution job is what moves to standard hosted capacity when requested. This avoids making caller-controlled expressions or arbitrary labels part of `runs-on`.

## Failure boundary

Execution-backend-specific internal errors are projected through each public API's existing error vocabulary. The new scheduling dimension does not add arbitrary failure strings to Node/Python public outputs.

The canonical runner contract and generated mapping remain the final authority for supported backend/profile combinations. The implementation helper must stay synchronized with that contract; generated drift and focused tests fail the final candidate if the two disagree.
