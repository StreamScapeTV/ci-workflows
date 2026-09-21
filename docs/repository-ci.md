# Repository-owned CI v1

Central CI executes fixed tracked repository scripts and owns shared infrastructure. Repositories own toolchain and product commands.

## Fixed operations

| Operation | Repository entrypoint |
| --- | --- |
| `build` | `.ci/build.sh` |
| `test` | `.ci/test.sh` |
| `full` | `.ci/test-full.sh` |
| `ui-test` | `.ci/test-ui.sh` |
| `release` | `.ci/release.sh` |

Each supported entrypoint must be tracked, executable, and regular. Unsupported operations must fail closed in the repository rather than inventing another Central profile.

## Generic host classes

Host selection is trusted Central infrastructure policy. Repository scripts and ordinary callers never select GitHub runner labels, runner groups, machine names, label arrays, or `runs-on` expressions.

The reviewed v1 catalog is:

- `linux-hosted` — ordinary hosted Linux execution.
- `macos-hosted` — ordinary hosted macOS execution.
- `macos-high-capacity` — reviewed higher-capacity macOS execution for workloads whose current fleet evidence requires that class.

Concrete runner labels and machines are Central implementation details and are not part of the repository contract. A new class requires a reviewed reusable Central change; a repository cannot create a class by configuration.

For normal `validation.repository` and `release.repository` runs, Central resolves the effective class through the service-only Agent State `resolve_repository_ci_host_class` decision for the exact project, repository, CI run, and operation. The decision is frozen on that CI run and replay-safe. The existing `host_os` request value remains only the reviewed default hint used by legacy/v1 project configuration; it is not a runner-label or host-class input.

The legacy library-package aggregate still uses its fixed hosted Linux/macOS readiness split until that parent receives its own repository-CI lifecycle. It cannot request the high-capacity class.

## Trusted Agent State policy

Project-specific bindings live only in private Agent State `repository_ci` configuration. Public examples use non-identifying repositories:

```json
{
  "schemaVersion": 2,
  "repository": "organization/example-native-library",
  "capabilities": ["private_network"],
  "hostPolicy": {
    "default": "macos-hosted",
    "operations": {
      "full": "macos-high-capacity"
    }
  }
}
```

Operation overrides are limited to `build`, `test`, `full`, `ui-test`, and `release`. Unknown classes, repository mismatches, stale CI identities, or conflicting replays fail closed. New projects request a reviewed generic class by updating private Agent State project configuration; they do not edit `.ci/*` scripts to select runners.

## Script environment

Central sets these host values before invoking the repository entrypoint:

- `CI_HOST_OS` — resolved execution OS (`linux` or `macos`).
- `CI_HOST_CLASS` — resolved reviewed generic host class.
- `CI_OPERATION` — fixed operation name.
- `CI_INPUTS_JSON` — validated versioned semantic inputs.
- `CI_LOG_DIR`, `CI_ARTIFACT_DIR`, `CI_PROGRESS_FILE` — Central-owned writable evidence locations.

`CI_HOST_CLASS` and `CI_HOST_OS` are informational. Repository scripts must not branch into runner-selection logic or attempt to acquire runner credentials.

## Capability lifetime

Shared capabilities such as `private_network`, `github_git`, `registry_netrc`, and `gradle_maven` are established by Central before `.ci/*` execution and remain available for the full repository operation and Central cleanup lifecycle. Product scripts consume the resulting environment/tool defaults but never acquire the underlying credentials.

## What host-class proof establishes

A successful host-class run proves trusted class resolution, Central runner selection, exact source checkout, capability lifetime, repository entrypoint execution, and normal evidence/cleanup on that class. Product/toolchain correctness still requires the repository's own representative operation; a host-class proof is not a substitute for device, signing, publication, or other separately governed acceptance.
