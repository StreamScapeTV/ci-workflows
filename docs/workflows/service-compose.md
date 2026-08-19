# Service and Compose validation

Issue #359 defines the product-neutral service/Compose validation family for Central CI. The implementation is a thin workflow and composite-action shell over `ci_workflows.ciw_compose`, `ci_workflows.ciw_compose_entrypoint` and `ci_workflows.service_compose_primitives`; product repositories keep their Compose topology, service names, readiness expectations and validation assertions in their own source.

## Caller boundary

The public reusable follows the standard Central validation shape:

- `admitted_sha`: exact lowercase 40-character caller source SHA.
- `working_directory`: repository-relative Compose project root, default `.`.
- `validation_plan_json`: bounded JSON object, maximum 16 KiB.

`validation_plan_json` owns only product topology and validation choices:

```json
{
  "compose_file": "compose.test.yml",
  "services": ["api", "db"],
  "env_files": [".env.test"],
  "readiness": [
    {"service":"api","kind":"http","url":"http://127.0.0.1:8080/ready","expected_statuses":[200]},
    {"service":"db","kind":"tcp","host":"127.0.0.1","port":5432}
  ],
  "validation_script_path": "scripts/validate-services.sh",
  "validation_timeout_seconds": 900
}
```

The required plan fields are `compose_file`, `readiness` and `validation_script_path`. `services` defaults to the whole Compose project, `env_files` defaults to an empty list, and `validation_timeout_seconds` defaults to 900 seconds with a maximum of 3600 seconds. The validation script must be a repository-relative non-symlink executable beneath the working directory and is invoked directly with zero arguments; Central does not accept arbitrary shell command strings.

The container engine is **not** a public caller input. The executable boundary fixes Podman internally for the Central-selected runner. Consumers cannot provide `container_engine`, Compose commands, runner labels, arbitrary environment maps, secret names or infrastructure identity.

Readiness checks are typed rather than arbitrary environment objects. `tcp` accepts only service, host, port, timeout and interval fields. `http` accepts only service, an HTTP(S) URL without embedded credentials, expected statuses, timeout and interval fields. `postgres` accepts service, host, port, database, optional user, timeout and interval fields. The initial boundary deliberately does not accept arbitrary secret names or arbitrary environment JSON.

## Run-owned lifecycle

Central derives the Compose project identity from the GitHub run id and run attempt (`ciw-<run-id>-<attempt>`); callers do not choose the project name. The adapter validates the Compose file and env files beneath the exact checked-out working directory, brings up only the selected project/services, waits for all declared readiness checks, and invokes the checked-in validation script directly.

Any attempted `compose up`, including a partially failed startup, enters terminal cleanup. Success, readiness failure, validation failure and timeout all execute an exact `compose down --remove-orphans` for the same validated project. If validation and teardown both fail, the validation/startup failure remains the primary error and the cleanup code is retained separately.

The reusable checks out the caller source once under `source/`, prepares one marker-bound container workspace, delegates the lifecycle to the immutable `validate-service-compose` action checkpoint, then performs normal Central workspace cleanup under `if: always()` and verifies the caller source is still exact and clean. No GitHub Actions cache or routine artifact upload is part of this API.

## Failure diagnostics

Failure handling collects bounded Compose service state and bounded log tails. Diagnostics remove the checked-out project path, URL credentials and common token/password/authorization/secret/API-key assignments before writing to stderr. Validation-script stdout/stderr receives the same bounded redaction. Diagnostic collection is best-effort and never replaces the primary validation error.

The public reusable outputs are `result`, `test_summary` and `cleanup_result`. The internal composite action also exposes stable failure/cleanup codes and the run-owned project name for Central diagnostics without expanding the public caller contract.

## Runner prerequisite before public workflow publication

The exact-source planner uses semantic `general-small` capacity before the container-capable execution job is scheduled. The execution job is Central-owned and fixed to the existing daemonless Buildah-small selector, whose current runner image already includes Podman and `podman-compose`; callers never select it.

The workflow remains unpublished until runner authority explicitly permits the service/Compose validation API on that exact-source Buildah-small capacity. This is an additive Central runner-policy registration only: it must not broaden callers to choose runner identities, must not enable fork-PR execution on privileged container capacity, and must not weaken the existing Docker/DinD retirement policy.
