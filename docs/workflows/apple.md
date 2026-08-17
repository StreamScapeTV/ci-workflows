# Apple validation workflow

`validation.apple` version `2.0.0` is the product-neutral reusable workflow at
`.github/workflows/reusable-apple.yml`. Its stable public check remains
`CI / Apple validation`.

Version 2 adds a protected-full multi-stage path while retaining the legacy
profile API for existing callers. New protected-full callers provide one exact
admitted source SHA plus one strict caller-owned `validation_plan_json`. The
plan contains only bounded Apple technology fields: stage identifier, platform,
operation, working directory, Xcode project/workspace, scheme, configuration,
optional checked-in test plan, SwiftPM resolution mode and lock files, bounded
Xcode arguments, bounded test selectors, expected outputs, cleanup paths, or a
checked-in bash/Python script. At most eight stages are accepted.

Callers cannot select a runner, host, architecture, Xcode installation, raw
destination, simulator identity, signing identity, provisioning profile,
keychain, archive/export/store operation, arbitrary command, secret name,
registry, Kubernetes target, deployment target, DerivedData path, SwiftPM state
path, or result-bundle path. Signing, destination construction, toolchain
identity, simulator ownership, workspace state and cleanup remain centrally
fenced.

## Protected-full single-executor model

A protected `portable` planner validates the bounded stage plan and resolves one
semantic `apple` executor. The heavy path then:

1. checks out the exact admitted product source once;
2. prepares one isolated Apple workspace once;
3. optionally checks out one exact private dependency once, after exact SHA,
   checkout identity, remote erasure and credential erasure are verified;
4. verifies Xcode, Swift, SDK and required simulator-runtime identities once;
5. executes requested Apple stages sequentially in that same workspace;
6. shares SwiftPM and DerivedData state across stages while giving each stage a
   separate result-bundle directory;
7. performs Apple-specific cleanup/residue once at the terminal boundary,
   followed by source and registered-workspace cleanup.

The public workflow allocates exactly one heavy macOS job for protected-full.
Separate iOS, tvOS and macOS stage summaries therefore do not imply separate
cold runner jobs or repeated source/dependency/workspace setup.

## Compile and test destination behavior

Apple platforms remain distinct validations. iOS, tvOS and macOS stages each
run against the requested SDK/platform; the workflow never claims that one
platform binary satisfies another platform.

Compile-only iOS/tvOS stages use the bounded generic simulator-platform
Xcode destination. They do **not** select, create, boot, or consume a concrete
simulator. A `test` stage is different: it acquires only the reviewed simulator
for that platform through the existing ownership registry, runs the requested
bounded test work, then shuts down/reclaims that simulator before a later test
stage can acquire another one. This prevents multiple simulators from being
launched concurrently by one protected-full gate.

macOS remains unsigned and uses the centrally constructed macOS destination.
All Xcode stages force `CODE_SIGNING_ALLOWED=NO`,
`CODE_SIGNING_REQUIRED=NO`, and an empty `CODE_SIGN_IDENTITY`.

## Targeted and legacy scopes

The existing checked-in Apple consumer/profile contracts remain available
through the default `validation_scope: legacy` path. Existing bounded profiles
such as `ios-simulator`, `tvos-simulator`, `macos`, Swift package,
native-dependency preparation and repository recovery therefore retain their
reviewed task mappings and behavior while callers migrate.

Protected-full itself executes exactly the stages present in the supplied plan.
A caller that needs only one platform build or one bounded test supplies only
that stage. Source-only recovery/policy work is intentionally not accepted as a
protected-full Apple plan; it stays on the existing legacy/source path so it can
continue using appropriately sized general capacity rather than consuming a
Mac solely to inspect source.

## Optional exact private dependency

Protected-full may request at most one private dependency using an exact
repository, full SHA, bounded dependency identifier and bounded subdirectory.
The reusable workflow checks it out once through the immutable
`checkout-private-dependency` helper. Execution accepts the dependency only
when the exact checkout receipts prove repository identity, SHA, expected
subpath, remote erasure and credential erasure. The private dependency token is
transient, fixed-purpose `contents:read` authority and is never exposed to
product commands.

No product repository allowlist or application product identifier exists in the
shared protected-full implementation. Product repositories decide whether a
private dependency is required and supply its exact coordinates.

## Legacy profiles

| Profile | Purpose | Execution boundary |
|---|---|---|
| `source-audit` | Verify exact source, reviewed paths, toolchain identity, and clean state without a product build | semantic `apple` |
| `swift-package` | Run one reviewed Swift package build or test command with isolated SwiftPM state | semantic `apple` |
| `ios-simulator` | Build or test one reviewed project/workspace on a contract-owned iOS simulator | semantic `apple` |
| `tvos-simulator` | Build or test one reviewed project/workspace on a contract-owned tvOS simulator | semantic `apple` |
| `macos` | Run one reviewed unsigned macOS build or test | semantic `apple` |
| `native-dependency-preparation` | Invoke one checked-in dependency-preparation script with fixed arguments and registered state | semantic `apple` |
| `repository-recovery` | Invoke checked-in recovery/audit scripts with fixed arguments | semantic `apple` |

## Bounded iptv-apple Release certification

The existing `iptv-apple` consumer contract remains the Debug validation
mapping. `iptv-apple-release` is a separate checked-in consumer contract for
exact-SHA certification and exposes only the existing `ios-simulator`,
`tvos-simulator`, and `macos` legacy profiles. Those three mappings select fixed
tasks with `configuration: Release` and compile-only `xcodebuild build`
actions.

Release configuration is not a public protected-full privilege. A supplied
stage configuration remains a bounded ordinary Xcode configuration and cannot
turn validation into signing, archive/export, notarization, store,
physical-device, registry, or deployment authority.

## Immutable private helper reuse

The reusable workflow invokes the protected-full-aware `validate-apple`
composite action through immutable checkpoint
`db50df67a34672502ab0fe9815af0a88ea2f475c`, recorded in the action lock as
`issue #336 single-executor Apple checkpoint`. Exact caller checkout, optional
private dependency checkout, workspace preparation, evidence rendering and
registered-state cleanup use already reviewed immutable foundation helpers.

The public workflow does not clone a mutable `.ciw` helper checkout and does not
accept a caller-selected helper version. Product source authority remains
separate and exact. Fixed source checkout roots are removed through inline
`lstat`-based no-follow cleanup before or after execution as appropriate.

## Exact toolchain and package resolution

The contract verifies the full reviewed Xcode version and build, Swift version,
required iOS/tvOS/macOS SDK versions, and required iOS/tvOS simulator runtime
identities before build or test execution. `DEVELOPER_DIR` and other
caller-selected Xcode-path controls are removed from the execution environment.
A mismatch fails before product commands run.

Protected-full Xcode commands use explicit container, scheme, configuration,
centrally constructed destination, timeout, shared isolated DerivedData and
SwiftPM state, plus a stage-local isolated result bundle. Locked package mode
requires exact checked-in resolved files and passes both automatic-resolution
rejection flags. Any protected lock or source mutation fails closed.

## Deterministic simulators and stale-state reconciliation

Test stages reuse the existing Apple simulator ownership implementation. An iOS
or tvOS simulator binds to a reviewed runtime, product family, device type and
device-type identifier. The job derives a job-owned simulator identity, rejects
malformed or ambiguous matches, rejects unowned matching simulators, registers
created simulators before boot, and uses the ownership registry to reclaim
stale job-owned state safely.

Per-test shutdown/reclamation prevents overlap between sequential simulator
tests. Terminal cleanup runs the same reconciliation again and fails if any
registered simulator or exact job-owned simulator remains. A simulator result
is never represented as physical-device evidence.

## Additive legacy contract authority

Legacy consumer-specific schemes, commands, media preparation and recovery
rules remain in the validated base `contracts/apple-validation.json`, bounded
additive `contracts/apple-validation-*.json` fragments, or consumer-owned
scripts; the shared Python implementation contains no product-name branch.

An additive fragment may contain only `tasks` and `consumer_contracts`. Added
tasks and consumer mappings pass the same checked-in Apple validators, and any
identifier collision or malformed fragment fails closed. Protected-full does
not add a second product/task registry: its stage plan is caller-owned and
strictly technology-bounded.

## Outputs, artifacts and cleanup

The v2 public workflow returns `result`, bounded `test_summary`,
`cleanup_result`, and `artifact_exception_used`. Internal execution evidence
also records exact verified runtime versions, evidence identity, source-clean
state and bounded per-stage results.

Routine runs upload zero GitHub Actions artifacts. No GitHub Actions cache is
used; persistent runner-side caching/storage remains infrastructure-owned.

Apple-specific state includes DerivedData, result bundles, SwiftPM/CocoaPods
state, logs, reports, native output, generated output and job-owned simulators.
Cleanup uses lexical contract paths and `lstat`-based no-follow removal. A
symlink is unlinked rather than traversed, outside sentinels are preserved, and
residue is a terminal failure. Source checkout and marker-bound workspace
cleanup also participate in the terminal status projection.

## Smoke workflows

`.github/workflows/apple-validation-smoke.yml` is the protected-full acceptance
smoke. It validates the exact PR implementation, plans a three-stage iOS/tvOS/
macOS compile sequence, asserts semantic `[macOS, ARM64]` resolution, and runs
all three builds sequentially in **one** real Mac job with one exact source
checkout and one Apple workspace. Because those stages are compile-only, the
smoke proves that no simulator boot is necessary for compile coverage. It then
runs Apple cleanup, residue, source-clean verification, source removal,
workspace cleanup and a zero-routine-artifact finalizer.

`.github/workflows/apple-certification-smoke.yml` independently preserves the
legacy Release-certification contract. Simulator smoke/certification is not
physical-device, signing, publication, notarization or store proof.
