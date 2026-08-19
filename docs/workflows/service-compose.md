# Service and Compose validation

Issue #359 defines the product-neutral service/Compose validation family for Central CI. The implementation is a thin orchestration layer over `ci_workflows.service_compose_primitives`; product repositories keep their Compose topology, service names, ports, readiness expectations and validation assertions in their own source.

## Caller boundary

The validation request is intentionally bounded. A caller supplies an exact admitted source SHA, a repository-relative working directory and Compose file, an optional JSON list of Compose service names, an optional JSON list of repository-relative Compose env-file paths, a required readiness JSON array, and one repository-relative executable validation script. The script is invoked directly with zero arguments; Central does not accept arbitrary shell command strings.

The planned public inputs are:

- `admitted_sha`: exact lowercase 40-character caller source SHA.
- `working_directory`: repository-relative Compose project root, default `.`.
- `compose_file`: Compose YAML beneath the working directory.
- `compose_tool`: bounded `podman` or `docker`; Central must select a runner that actually provides the selected engine before this is exposed publicly.
- `services_json`: JSON array of service names, default `[]` for the whole project.
- `env_files_json`: JSON array of repository-relative env-file paths, default `[]`. Values are paths only; arbitrary environment maps are not a public input.
- `readiness_json`: one to 32 typed readiness checks.
- `validation_script_path`: repository-relative, non-symlink executable validation script beneath the working directory.
- `validation_timeout_seconds`: bounded validation-script timeout, default 900 seconds and maximum 3600 seconds.

Readiness checks are typed rather than arbitrary environment objects:

```json
[
  {"service":"api","kind":"http","url":"http://127.0.0.1:8080/ready","expected_statuses":[200]},
  {"service":"db","kind":"tcp","host":"127.0.0.1","port":5432}
]
```

`tcp` accepts only `host`, `port`, timeout and interval fields. `http` accepts only an HTTP(S) URL without embedded credentials, expected statuses, timeout and interval fields. `postgres` accepts host, port, database, optional user, timeout and interval fields. The initial boundary deliberately does not accept arbitrary secret names or arbitrary environment JSON.

## Run-owned lifecycle

Central derives the Compose project identity from the GitHub run id and run attempt (`ciw-<run-id>-<attempt>`); callers do not choose the project name. The adapter validates the Compose file and env files beneath the exact checked-out working directory, brings up only the selected project/services, waits for all declared readiness checks, and invokes the checked-in validation script directly.

Any attempted `compose up`, including a partially failed startup, enters terminal cleanup. Success, readiness failure, validation failure and timeout all execute an exact `compose down --remove-orphans` for the same validated project. If validation and teardown both fail, the validation/startup failure remains the primary error and the cleanup code is retained separately.

The reusable workflow must also perform normal Central workspace cleanup under `if: always()` after the adapter has completed its stack teardown. No GitHub Actions cache or routine artifact upload is part of this API.

## Failure diagnostics

Failure handling collects bounded Compose service state and bounded log tails. Diagnostics remove the checked-out project path, URL credentials and common token/password/authorization/secret/API-key assignments before writing to stderr. Validation-script stdout/stderr receives the same bounded redaction. Diagnostic collection is best-effort and never replaces the primary validation error.

The machine-readable success summary contains only selected service names, typed readiness results and the validation return code. The intended reusable outputs are `result`, `test_summary`, `cleanup_result`, `failure_code`, `cleanup_code` and the run-owned `project_name`.

## Runner prerequisite before public workflow publication

As of the #359 implementation checkpoint based on `9e85bd68ad5c26c8b087f7907acb1427f76fcca0`, the current runner contract does not provide a valid public execution target for this workflow: `general-small` explicitly forbids assuming a container engine, while Podman is guaranteed only on Buildah profiles whose allowed workflow APIs and trust class do not include PR-capable service/Compose validation.

Therefore `.github/workflows/reusable-service-compose.yml` must not be advertised as implemented until runner authority is reconciled. The resolution must either provide container-capable semantic general validation capacity or explicitly authorize a bounded Compose validation profile/trust model. The #359 adapter/tests can remain product-neutral while that infrastructure prerequisite is resolved; weakening existing runner policy inside the adapter is not an acceptable substitute.
