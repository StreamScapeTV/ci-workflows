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


## Central execution envelope

For every admitted repository operation, Central owns the reusable execution envelope:

1. claim and validate the Agent State request;
2. resolve one reviewed generic host class;
3. check out the exact source/ref and record the observed SHA;
4. resolve the trusted private capability grant;
5. establish granted shared capabilities for the full operation lifetime;
6. invoke exactly one fixed tracked repository entrypoint;
7. capture and scrub private text evidence and package bounded artifacts;
8. upload private log/evidence through the shared Drive mechanisms;
9. remove ephemeral credentials, network state and evidence staging; and
10. settle the Agent State CI run.

Repository scripts do not implement this lifecycle and do not call Agent State or Drive directly.

The current-fleet non-host capability classification and publication migration plan are recorded in
[`repository-ci-capability-audit.md`](repository-ci-capability-audit.md).

## Shared capability catalog

The frozen v1 non-host capability catalog is:

- `private_network` — Central establishes the reviewed private network before product execution and keeps it available through the repository operation and cleanup lifecycle.
- `github_git` — Central configures ephemeral Git HTTPS authentication for ordinary Git operations.
- `registry_netrc` — Central configures ephemeral HTTPS package-registry read authentication through the operation's tool defaults.
- `gradle_maven` — Central configures ephemeral Gradle private-Maven read properties.

Capability authorization is private Agent State policy. Repository source cannot enable a capability,
choose a secret, select a private host, or provide an environment map.

When an authentication capability is active, Central may materialize an ephemeral `HOME`, Git
configuration, `.netrc`, or Gradle properties for the duration of the operation. These are
implementation details, not additional repository-CI API variables. Repository code uses normal
Git/package-manager behavior and never reads the underlying credential values.

## Complete script environment reference

The supported repository-script interface is finite:

| Variable | Availability | Repository use |
| --- | --- | --- |
| `CI_HOST_OS` | Always | Read-only resolved host OS: `linux` or `macos`. |
| `CI_HOST_CLASS` | Always | Read-only reviewed generic class: `linux-hosted`, `macos-hosted`, or `macos-high-capacity`. |
| `CI_OPERATION` | Always | Read-only fixed operation: `build`, `test`, `full`, `ui-test`, or authorized `release`. |
| `CI_INPUTS_JSON` | Always | Read-only canonical versioned semantic-input document. |
| `CI_LOG_DIR` | Always | Writable Central-owned directory for optional text diagnostics. |
| `CI_ARTIFACT_DIR` | Always | Writable Central-owned directory for bounded artifacts. |
| `CI_PROGRESS_FILE` | Always | Writable Central-owned text file for progress/heartbeat information. |
| `CI_APPLE_TEAM_ID` | Authorized macOS `release` only | Existing Apple signing team identifier supplied by Central. |
| `CI_APP_STORE_CONNECT_KEY_ID` | Authorized macOS `release` only | Existing App Store Connect API key identifier supplied by Central. |
| `CI_APP_STORE_CONNECT_ISSUER_ID` | Authorized macOS `release` only | Existing App Store Connect issuer identifier supplied by Central. |
| `CI_APP_STORE_CONNECT_API_KEY_P8_BASE64` | Authorized macOS `release` only | Existing App Store Connect API key material supplied by Central. |

Repository stdout/stderr is captured automatically by Central. The primary Central log path
(`CI_LOG`), `RUNNER_TEMP`, credential files, runner labels, secrets and Drive paths are internal
implementation details and are not supported repository-script inputs.

### Structured semantics

`CI_INPUTS_JSON` is a JSON object with `schemaVersion: 1` and an `inputs` object. Fields are
validated by Central against the operation schema before the repository script runs. The repository
then maps those semantic values to product-owned commands. It must not treat the JSON as arbitrary
argv or shell text.

A minimal parser can be as small as:

```sh
python3 - <<'PY'
import json
import os

document = json.loads(os.environ["CI_INPUTS_JSON"])
assert document["schemaVersion"] == 1
inputs = document["inputs"]
target = inputs.get("product_target")
print(target or "default")
PY
```

## Repository responsibilities

A repository owns only product/toolchain behavior. It must:

- provide the fixed `.ci/*` files needed by its admitted operations;
- keep every supported entrypoint tracked, regular, non-symlinked and executable;
- make unsupported operations fail closed explicitly;
- parse `CI_INPUTS_JSON` and translate reviewed semantics into product-owned target/test/release choices;
- own Xcode, Gradle, Android SDK, Node, Python, Flutter, Maven and other toolchain commands;
- own product-local simulator/emulator/test-environment lifecycle where required;
- write optional diagnostics only to `CI_LOG_DIR`, `CI_ARTIFACT_DIR` and `CI_PROGRESS_FILE`; and
- avoid credential acquisition, runner selection, Drive upload, Agent State mutation and private-network setup.

### Minimal Linux build/test

```sh
#!/usr/bin/env bash
set -Eeuo pipefail
test "${CI_HOST_OS}" = linux
npm ci
npm test
npm run build
printf 'linux build complete\n' > "${CI_PROGRESS_FILE}"
```

### Minimal macOS build/test

```sh
#!/usr/bin/env bash
set -Eeuo pipefail
test "${CI_HOST_OS}" = macos
swift package resolve
swift test
printf 'macOS tests complete\n' > "${CI_PROGRESS_FILE}"
```

### Optional evidence

```sh
mkdir -p "${CI_LOG_DIR}" "${CI_ARTIFACT_DIR}"
printf 'phase=tests-complete\n' > "${CI_LOG_DIR}/summary.txt"
cp build/report.json "${CI_ARTIFACT_DIR}/report.json"
printf 'report-ready\n' > "${CI_PROGRESS_FILE}"
```

Central validates, scrubs and packages these locations under its fixed evidence bounds. Product
scripts must not upload them directly.

## Private Agent State configuration

A generic configuration example is:

```json
{
  "schemaVersion": 2,
  "repository": "organization/example-service",
  "capabilities": ["private_network", "registry_netrc"],
  "hostPolicy": {
    "default": "linux-hosted",
    "operations": {
      "full": "macos-high-capacity"
    }
  }
}
```

This policy is private. Public Central source and documentation do not contain the concrete migration
ledger or consumer-to-capability/host bindings.

## Release authorization boundary

`.ci/release.sh` is still the only generic product release entrypoint, but execution is separately
authorized by Central and requires an admitted immutable tag. The repository owns release commands,
targets, package coordinates, store metadata and product semantics.

Provider publication/signing credentials are not caller-selected capability names. For an authorized
macOS `release.repository` operation, Central supplies the existing Apple signing/App Store Connect
material only through the four fixed `CI_APPLE_*` variables documented above and includes those same
values in its redaction surface. Validation and non-macOS release operations receive none of that
secret material. The repository-owned `.ci/release.sh` owns signing, archive/export, validation, upload,
and product release semantics; Central accepts no caller-selected secret names, arbitrary environment
maps, or product release commands. The direct immutable-tag `release.repository` semantic is
`release_kind=publish`; `release_kind=prepare` remains only for the existing aggregate library-package
preparation path while that compatibility flow is live.

## Private evidence, retention and cleanup

Central captures repository stdout/stderr into its private log, scrubs configured secrets, packages
bounded eligible evidence, and uses the shared private Drive transport. Stable-log replacement,
retention and follow behavior are Central policy and are not controlled by the repository.

Cleanup runs independently of product success. Ephemeral authentication files, private-network state
owned by the Central capability, log/evidence staging and other run-owned temporary state are removed
before lifecycle settlement. Drive availability does not widen repository execution authority.

## Migration checklist

A repository is migrated only when:

1. required fixed `.ci/*` files exist, are tracked and executable;
2. unsupported operations fail closed;
3. required private capabilities and host policy are configured in trusted Agent State;
4. product/toolchain semantics live in repository scripts and consume only the documented interface;
5. required operations run through the generic executor on exact observed source;
6. private logs/evidence, failure handling and cleanup behave correctly;
7. required representative live proof is recorded in the private migration ledger; and
8. only after the ledger has no remaining live caller for a compatibility lane may that lane be retired.

Release migration additionally requires the write-side publication plan above; migration must never be
declared merely because a validation entrypoint exists.
