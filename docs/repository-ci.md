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

## Tracked multi-OS execution plan

A repository that needs one semantic operation to execute on more than one reviewed operating system may add the tracked non-executable regular file `.ci/execution-plan.json` (mode `100644`). The v1 shape is finite and declarative:

```json
{
  "schemaVersion": 1,
  "operations": {
    "build": ["linux", "macos"],
    "test": ["linux", "macos"],
    "full": ["linux", "macos"],
    "ui-test": ["linux", "macos"],
    "release": ["linux", "macos"]
  }
}
```

Only the fixed operation names and reviewed OS identifiers `linux` and `macos` are accepted in v1. Each operation selects one or two unique OS values. Unknown fields, duplicate OS values, unknown OS values, unsupported operations, untracked/symlinked plans, and oversized or ambiguous JSON fail closed. The plan never contains runner labels, commands, arguments, environment maps, secret names, timeouts, or product targets.

When the plan is present, repository source is authoritative for the OS set. The legacy `host_os` request remains only a backwards-compatible single-child fallback for repositories that have not adopted the plan. Central starts one Agent State parent lifecycle, records one exact observed source SHA, runs each selected child concurrently with `fail-fast` disabled, and settles the parent only after every child is terminal. Each child reuses the same fixed operation entrypoint, receives its own `CI_HOST_OS`/`CI_HOST_CLASS`, capability setup, private log/evidence objects, and cleanup lifecycle.

Central resolves each planned child through the CI-service-only Agent State child-OS host resolver. The resolver remains bound to the exact parent project/repository/CI run/operation and freezes a replay-safe decision per child OS. Ordinary callers still cannot supply a host-class array or raw runner matrix.

## Generic host classes

Host selection is trusted Central infrastructure policy. Repository scripts and ordinary callers never select GitHub runner labels, runner groups, machine names, label arrays, or `runs-on` expressions.

The reviewed v1 catalog is:

- `linux-hosted` — ordinary hosted Linux execution.
- `macos-hosted` — ordinary hosted macOS execution.
- `macos-high-capacity` — reviewed higher-capacity macOS execution for workloads whose current fleet evidence requires that class.

Concrete runner labels and machines are Central implementation details and are not part of the repository contract. A new class requires a reviewed reusable Central change; a repository cannot create a class by configuration.

For direct single-OS child execution, Central resolves the effective class through the service-only Agent State `resolve_repository_ci_host_class` decision for the exact project, repository, CI run, and operation. For a tracked multi-OS parent plan, each child uses the service-only `resolve_repository_ci_host_class_for_os` decision bound to the same parent plus the selected child OS. Decisions are replay-safe and OS/class compatibility remains fail-closed. The existing `host_os` request value is a compatibility fallback only when `.ci/execution-plan.json` is absent; it is never a runner-label or host-class input.

The library-package aggregate uses the tagged repository's tracked `.ci/execution-plan.json` for its `full` readiness OS set when that plan exists; repositories without a tracked plan retain the aggregate's reviewed two-OS compatibility readiness. Every generic readiness/preparation child then resolves its execution class through the same trusted OS-aware repository policy as planned Repository CI. The aggregate caller still cannot provide a host class, runner label, or machine identity. Its fixed Maven publication child also reuses that trusted parent to resolve the repository's reviewed macOS `release` class, while the standalone Maven publication route keeps its existing hosted-Linux default. Apple SwiftPM remains on the reviewed macOS/ARM64 lane, so a repository whose release policy selects `macos-high-capacity` can serialize both package publishers on one approved Mac without widening the public workflow API.

## Trusted Agent State policy

Project-specific bindings live only in private Agent State `repository_ci` configuration. For mixed-platform repositories, schema v3 selects policy by the already-admitted operating system before applying an optional operation override. Public examples use non-identifying repositories:

```json
{
  "schemaVersion": 3,
  "repository": "organization/example-native-library",
  "capabilities": ["private_network"],
  "hostPolicy": {
    "operatingSystems": {
      "linux": {
        "default": "linux-hosted",
        "operations": {}
      },
      "macos": {
        "default": "macos-hosted",
        "operations": {
          "full": "macos-high-capacity"
        }
      }
    }
  }
}
```

Schema v1 keeps ordinary hosted selection from the admitted operating system. Schema v2 remains accepted for compatibility with already-reviewed policy, but its project-global host policy is not the mixed-platform model. Schema v3 keeps Linux and macOS policy independent; a missing OS block falls back only to the ordinary hosted class for that admitted OS.

Operation overrides are limited to `build`, `test`, `full`, `ui-test`, and `release`. Unknown classes, cross-OS assignments, repository mismatches, stale CI identities, or conflicting replays fail closed in trusted Agent State resolution. Central capability admission accepts the reviewed repository/capability fields for schemas 1-3 but does not interpret `hostPolicy` into runner labels. New projects request a reviewed generic class by updating private Agent State project configuration; they do not edit `.ci/*` scripts to select runners.

## Script environment

Central sets these host values before invoking the repository entrypoint:

- `CI_HOST_OS` — resolved execution OS (`linux` or `macos`).
- `CI_HOST_CLASS` — resolved reviewed generic host class.
- `CI_OPERATION` — fixed operation name.
- `CI_INPUTS_JSON` — validated versioned semantic inputs.
- `CI_LOG_DIR`, `CI_ARTIFACT_DIR`, `CI_PROGRESS_FILE` — Central-owned writable evidence locations.

`CI_HOST_CLASS` and `CI_HOST_OS` are informational. Repository scripts must not branch into runner-selection logic or attempt to acquire runner credentials.

## Capability lifetime

Shared capability authorization is resolved by Central before `.ci/*` execution. Long-lived operation capabilities such as `private_network`, `github_git`, `registry_netrc`, `gradle_maven`, `registry_oci_publish`, and exact-run `backup_s3_read` are established for the reviewed execution lifetime. `protected_deployed_conformance` is different by design: the ordinary tracked `full` entrypoint sees only its informational enablement bit; Central materializes the protected bundle only after that entrypoint succeeds, then executes the separately bound certifier and deterministic reset before cleanup. Product scripts never acquire Central infrastructure credentials.

## What host-class proof establishes

A successful host-class run proves trusted class resolution, Central runner selection, exact source checkout, capability lifetime, repository entrypoint execution, and normal evidence/cleanup on that class. Product/toolchain correctness still requires the repository's own representative operation; a host-class proof is not a substitute for device, signing, publication, or other separately governed acceptance.


## Central execution envelope

For every admitted repository operation, Central owns the reusable execution envelope:

1. claim and validate one Agent State parent request;
2. check out the exact source/ref, record the observed SHA once, and validate the optional tracked execution plan;
3. derive one or more reviewed child OS executions from the plan (or one legacy `host_os` compatibility child when the plan is absent);
4. resolve the trusted generic host class independently for every child OS;
5. establish the same authorized shared capabilities independently for each child lifetime;
6. invoke exactly one fixed tracked repository entrypoint per child, concurrently when more than one OS is selected;
7. capture/scrub private text evidence and package bounded artifacts independently per child;
8. upload OS-distinct private log/evidence objects through the shared Drive mechanisms;
9. remove each child's ephemeral credentials, network state and evidence staging; and
10. settle the single parent Agent State CI run only after all required children settle.

Repository scripts do not implement this lifecycle and do not call Agent State or Drive directly.

The current-fleet non-host capability classification and publication migration plan are recorded in
[`repository-ci-capability-audit.md`](repository-ci-capability-audit.md).

## Shared capability catalog

The frozen v1 non-host capability catalog is:

- `private_network` — Central establishes the reviewed private network before product execution and keeps it available through the repository operation and cleanup lifecycle.
- `github_git` — Central configures ephemeral Git HTTPS authentication for ordinary Git operations.
- `registry_netrc` — Central configures ephemeral HTTPS package-registry read authentication through the operation's tool defaults.
- `gradle_maven` — Central configures ephemeral Gradle private-Maven read properties.
- `registry_oci_publish` — for an authorized Linux repository `release`, Central creates isolated Buildah/Skopeo and Helm registry authentication using the reviewed private registry write credential. Repository code receives authenticated tool defaults plus the fixed registry host, never the raw write token; image/chart namespaces and publication coordinates remain repository-owned.
- `backup_s3_read` — authorized repository `release` only. Central requires a separate private descriptor bound to the exact project, Agent State CI run, repository, tag/ref, observed source SHA, and release operation, then exposes one fixed read-only S3-compatible backup connection bundle only to `.ci/release.sh`. Backup selection, Barman/CloudNativePG restore, PostgreSQL topology, migrations, health/smoke checks, restored-data handling, and cleanup remain repository-owned. Periodic raw-log checkpointing is disabled while this capability is active; final text/artifact evidence is still secret-scrubbed before Drive upload.
- `protected_deployed_conformance` — Linux `full` validation only, and only when the same trusted grant also includes `private_network`. Central resolves a separate private `protected_deployed_conformance` Agent State descriptor bound to the exact repository/run, writes the approved HTTP/WSS scenario documents as mode-0600 files, projects a finite protected bundle into repository-owned environment names, executes one tracked no-argv Python certifier after the ordinary `.ci/test-full.sh` succeeds, stores only bounded private diagnostics/evidence, performs the descriptor's same-origin reset/readback, then deletes the private bundle. Public callers cannot select the target, scenario, credential, entrypoint, environment names, reset target, expected protocol statuses, or pass/defer semantics.

Capability authorization is private Agent State policy. Repository source cannot enable a capability,
choose a secret, select a private host, or provide an environment map.

For Central-managed Swift binary publication, the exact trusted Agent State grant also determines
registry read authentication without adding a caller-controlled switch. A valid bound grant containing
`registry_netrc` keeps strict authenticated reads. A valid bound grant without `registry_netrc` permits
credential-free reads only on the lane's required private network/TLS path and only when the exact
artifact digest and size match. Missing, malformed, or unbound policy never enables that mode. Registry
publication remains separately authenticated, and read-only consumers never receive publication
credentials.

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
| `CI_OCI_REGISTRY` | `registry_oci_publish` Linux release only | Read-only fixed private OCI registry host. |
| `CI_BACKUP_S3_ENDPOINT` | `backup_s3_read` authorized release only | Fixed private S3-compatible backup endpoint. |
| `CI_BACKUP_S3_BUCKET` | `backup_s3_read` authorized release only | Fixed private production-backup bucket. |
| `CI_BACKUP_S3_REGION` | `backup_s3_read` authorized release only | Optional fixed region; empty when not required by the provider. |
| `CI_BACKUP_S3_ACCESS_KEY_ID` | `backup_s3_read` authorized release only | Read-only backup-store access-key identifier. |
| `CI_BACKUP_S3_SECRET_ACCESS_KEY` | `backup_s3_read` authorized release only | Read-only backup-store secret key. |
| `CI_PROTECTED_DEPLOYED_CONFORMANCE` | `protected_deployed_conformance` Linux full only | Informational `true`/`false`; protected targets, scenarios, credentials and identity values are passed only to the separately executed protected certifier, not through `CI_INPUTS_JSON`. |
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

For ordinary validation, the optional `build_configuration` semantic is deliberately finite:
`debug` or `release`. It is admitted only for `build`, `test`, and `full`. This is a
configuration intent, not an Xcode/Gradle argument: the repository translates the value into its
own toolchain terminology. Absence means the repository's reviewed default. The value does not
authorize signing, provisioning, TestFlight/App Store publication, or the generic `release`
operation.

For `test` and `ui-test`, `test_selectors` is the same bounded semantic string-list: at most 64
items, each at most 240 characters from the reviewed selector alphabet, with leading-dash values
rejected. Central never translates selectors into tool arguments. The tracked repository entrypoint
validates its own selector namespace and maps the semantic packet to its fixed product test graph.
`ui-test` may continue to use the finite `ui_mode` values (`smoke` or `full-ordinary`) when no
custom selector packet is needed.

A minimal parser can be as small as:

```sh
python3 - <<'PY'
import json
import os

document = json.loads(os.environ["CI_INPUTS_JSON"])
assert document["schemaVersion"] == 1
inputs = document["inputs"]
target = inputs.get("product_target")
configuration = inputs.get("build_configuration", "debug")
print(target or "default", configuration)
PY
```

## Repository responsibilities

A repository owns only product/toolchain behavior. It must:

- provide the fixed `.ci/*` files needed by its admitted operations;
- when an operation needs more than one OS, provide tracked `.ci/execution-plan.json` with only the reviewed finite OS set;
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
  "schemaVersion": 3,
  "repository": "organization/example-service",
  "capabilities": ["private_network", "registry_netrc"],
  "hostPolicy": {
    "operatingSystems": {
      "linux": {
        "default": "linux-hosted",
        "operations": {}
      }
    }
  }
}
```

This policy is private. Public Central source and documentation do not contain the concrete migration
ledger or consumer-to-capability/host bindings.

### Backup S3 read descriptor

When `backup_s3_read` is present in `repository_ci.capabilities`, the same private project state must carry `repository_ci_backup_s3_read_v1`. Its schema is deliberately identity-only: `schemaVersion`, `projectKey`, `ciRunId`, `repository`, `ref`, `sourceIsTag`, `sourceSha`, and `operation`. Every field must match the exact claimed Agent State release run and exact observed source, and `operation` must be `release`; otherwise capability admission fails closed. No endpoint, bucket, region, credential, backup selector, object prefix, command, or environment-map value is stored in Agent State.

The fixed endpoint/bucket/optional-region/read-only credentials remain Central secrets. They are projected only into the single tracked `.ci/release.sh` process through the five `CI_BACKUP_S3_*` variables. Central disables periodic raw-log checkpoints while the capability is active, then performs the normal configured-secret scrub and artifact quarantine before any final private Drive evidence upload. Repository code owns backup selection and all recovery/database/migration/application/smoke/cleanup behavior.

### Protected deployed-conformance descriptor

The generic capability authorization remains in `repository_ci.capabilities`. When `protected_deployed_conformance` is granted, the same private project state must also carry a separate `protected_deployed_conformance` descriptor. It is not a public workflow input. Schema version 2 binds exactly one repository, the `full` operation, one tracked `scripts/*.py` or `tools/*.py` no-argv entrypoint, a finite environment projection, the canonical SemVer release version, and a bounded same-origin reset/readback plan. The confidential target URLs, compressed scenario JSON and setup credential remain fixed Central secrets and are never stored in Agent State.

The environment projection maps only the finite bundle fields (`httpsTarget`, `wssTarget`, the two mode-0600 scenario paths, `setupCredential`, canonical `releaseVersion`, run id and a fixed live flag) to repository-owned variable names. Git SHAs, image/chart digests, deployed digests and environment hashes are not part of this release-identity projection. Central rejects duplicate names and Central-owned prefixes such as `CI_`, `GITHUB_`, `RUNNER_`, `AGENT_STATE_`, registry, Drive or runner credential variables. The protected certifier is executed only after the normal tracked `full` entrypoint succeeds. Its stdout must be one bounded JSON object; protected targets, scenario paths and setup credentials are rejected from the retained evidence.

The reset descriptor is lifecycle-only: it cannot change conformance expectations or pass/defer decisions. It may perform one same-origin authenticated POST/PATCH/DELETE with a subject selected from a protected scenario, followed by one same-origin GET readback whose exact scalar JSON-pointer value is checked. Redirects, transport errors, missing selectors, non-200 statuses, failed readback or incomplete private-file cleanup fail the Repository CI operation.

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

An authorized Linux `release.repository` may additionally receive the privately granted `registry_oci_publish` capability. Central establishes isolated Buildah/Skopeo and Helm authentication and exposes only standard tool configuration plus `CI_OCI_REGISTRY`; the raw write token never enters the repository process. The repository owns image/chart names, namespaces, build/package commands, immutability checks, push/read-back behavior, and release decisions.

An authorized `release.repository` may also receive `backup_s3_read` only when its separate exact-run descriptor matches the current project, Agent State CI run, immutable source ref/SHA, repository, and release operation. Central exposes the fixed read-only S3 connection variables only to `.ci/release.sh`; it does not select backups or run Barman, CloudNativePG, PostgreSQL, migrations, application health checks, smoke assertions, or cleanup.

## Private evidence, retention and cleanup

Central captures repository stdout/stderr into a bounded private rolling log, retaining startup
context plus a terminal tail while continuously draining product output. The rolling capture stays
below the reviewed text-evidence limit, preserves enough overlap for configured-secret values that
cross a rollover boundary to remain detectable, and never changes the product exit status. Central
then scrubs configured secrets, packages bounded eligible evidence, and uses the shared private Drive
transport. Stable-log replacement, retention and follow behavior are Central policy and are not
controlled by the repository.

Cleanup runs independently of product success. Ephemeral authentication files, private-network state
owned by the Central capability, log/evidence staging and other run-owned temporary state are removed
before lifecycle settlement. Drive availability does not widen repository execution authority.

## Migration checklist

A repository is migrated only when:

1. required fixed `.ci/*` files exist, are tracked and executable;
2. any multi-OS requirement is declared only through tracked `.ci/execution-plan.json`;
3. unsupported operations and invalid execution plans fail closed;
4. required private capabilities and OS-aware host policy are configured in trusted Agent State;
5. product/toolchain semantics live in repository scripts and consume only the documented interface;
6. required operations run through the generic executor on exact observed source, including concurrent Linux/macOS children where declared;
7. child-private logs/evidence, failure handling and cleanup behave correctly and the parent settles only after all children;
8. required representative live proof is recorded in the private migration ledger; and
9. only after the ledger has no remaining live caller for a compatibility lane may that lane be retired.

Release migration additionally requires the write-side publication plan above; migration must never be
declared merely because a validation entrypoint exists.
