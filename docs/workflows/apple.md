# Apple validation workflow

`validation.apple` version `1.0.0` is the product-neutral reusable workflow at
`.github/workflows/reusable-apple.yml`. Its stable public check is
`CI / Apple validation`.

The workflow accepts one exact admitted source SHA, one bounded validation
profile, one required bounded platform, and one reviewed consumer-contract
identifier. Optional version-file, working-directory, checked-in script, scheme,
destination-profile, and diagnostic-exception inputs are accepted only when
they exactly match the selected checked-in task. Project/workspace/package
paths, build configuration, test plans, simulator runtime/device identity, and
command arguments are never caller inputs; they remain contract-owned. Callers cannot select a runner, host, architecture, Xcode
path, destination, device, command, arbitrary argument list, signing identity,
provisioning profile, keychain, archive/export/store operation, dependency URL,
secret, registry, Kubernetes target, or deployment operation.

## Profiles

| Profile | Purpose | Execution boundary |
|---|---|---|
| `source-audit` | Verify exact source, reviewed paths, toolchain identity, and clean state without a product build | semantic `apple` |
| `swift-package` | Run one reviewed Swift package build or test command with isolated SwiftPM state | semantic `apple` |
| `ios-simulator` | Build or test one reviewed project/workspace on a contract-owned iOS simulator | semantic `apple` |
| `tvos-simulator` | Build or test one reviewed project/workspace on a contract-owned tvOS simulator | semantic `apple` |
| `macos` | Run one reviewed unsigned macOS build or test | semantic `apple` |
| `native-dependency-preparation` | Invoke one checked-in dependency-preparation script with fixed arguments and registered state | semantic `apple` |
| `repository-recovery` | Invoke checked-in recovery/audit scripts with fixed arguments | semantic `apple` |

A protected `portable` planner validates the contract and resolves the semantic
runner mapping. All source execution occurs on semantic `apple`; the caller
never supplies a concrete label.

## Bounded iptv-apple Release certification

The existing `iptv-apple` consumer contract remains the Debug validation
mapping. `iptv-apple-release` is a separate checked-in consumer contract for
exact-SHA certification and exposes only the existing `ios-simulator`,
`tvos-simulator`, and `macos` profiles. Those three mappings select fixed tasks
with `configuration: Release` and compile-only `xcodebuild build` actions.

Release configuration is not a workflow input. The caller selects the reviewed
consumer contract and bounded platform profile; a supplied configuration field
or other unregistered input fails closed. iOS/tvOS simulator destinations remain
contract-owned and macOS remains unsigned. Every execution still forces
`CODE_SIGNING_ALLOWED=NO`, `CODE_SIGNING_REQUIRED=NO`, and an empty
`CODE_SIGN_IDENTITY`; Release certification adds no signing, provisioning,
archive/export, notarization, store, physical-device, registry, or deployment
authority.

## Streamscape Media native dependency profiles

`streamscape-media-apple` remains the routine Media consumer contract. Its
`native-dependency-preparation` profile still selects the existing
`media-native-dependency` task and invokes
`scripts/ci/run-validation-scope.sh apple-mpv-native`; that MPV preparation path
is not redefined as VLC evidence.

`streamscape-media-vlc-tvos-apple` is the separate reviewed consumer contract for
one disposable private VLC tvOS candidate. It exposes only
`native-dependency-preparation` and selects the fixed
`media-vlc-tvos-native-dependency` task. That task executes the Media-owned
`scripts/ci/build-private-tvos-vlc-candidate.sh` directly from the exact admitted
Media source with no caller-supplied command or argument override. The protected
contract paths include the VLC source lock, tvOS build-support inventory, source
fetch/build/inspection/package scripts, and the candidate entry point.

The Media-owned script remains responsible for product assertions: exact locked
VLCKit/VLC identities, tvOS arm64 output, minimum-OS and Mach-O identity,
non-system dependency checks, private-package separation, redacted identity
evidence, and removal of runner-local source/build/binary state. Central
validation remains responsible for exact source admission, semantic Apple runner
placement, reviewed toolchain identity, bounded execution, registered cleanup
and residue enforcement, and the zero-default Actions artifact policy.

## Immutable private helper reuse

Private same-organization consumers do not clone the private central repository
with their caller-scoped token. The planner and Apple execution job invoke the
reviewed `validate-apple` composite action through the immutable Media VLC tvOS
checkpoint `5c1a9a060650159f180a308063ce5c4a055bdca4`. Exact caller checkout,
workspace preparation, and registered-state cleanup use the already reviewed
immutable foundation helpers.

The private action archive supplies Apple scripts and Python modules relative to
`GITHUB_ACTION_PATH`; no `.ciw` action checkout, central PAT input,
`secrets: inherit`, mutable helper ref, or caller-selected helper version is
required. Product source authority remains separate and exact.

Persistent macOS cleanup remains fail closed. The reusable workflow removes any
stale fixed `.ciw` root and the fixed `source` checkout through inline
`lstat`-based no-follow removal before or after execution as appropriate. This
preserves the old fixed-root safety boundary without depending on a checked-out
central script. Apple-specific state, simulator ownership, registered workspace
state, source checkout, and stale central-checkout residue all remain part of
the terminal failure projection.

## Exact toolchain

The contract verifies the full reviewed Xcode version and build, Swift version,
required iOS/tvOS/macOS SDK versions, and required iOS/tvOS simulator runtime
identities before build or test execution. `DEVELOPER_DIR` and other
caller-selected Xcode-path controls are removed from the execution environment.
A mismatch fails before product commands run.

Project/workspace commands use explicit container, scheme, configuration,
destination, timeout, isolated DerivedData, isolated cloned Swift packages, and
an isolated result bundle. Signing is disabled with
`CODE_SIGNING_ALLOWED=NO`, `CODE_SIGNING_REQUIRED=NO`, and an empty
`CODE_SIGN_IDENTITY`. The validator never archives, exports, notarizes, uploads,
or contacts a store.

## Deterministic simulators

An iOS or tvOS profile binds to a reviewed runtime, product family, device type,
and device-type identifier. The job derives a unique job-owned simulator name,
rejects malformed or ambiguous matches, rejects every matching simulator not
registered by the current job, and creates a simulator only when the profile
allows creation. Existing booted state is accepted only when it is already
registered to the same job. Public outputs contain only a hashed simulator
identity; a simulator is never reported as physical-device evidence.

Every created simulator is registered before boot. Terminal cleanup shuts down
and deletes only registered simulator IDs, rereads the simulator inventory, and
fails if any registered ID remains.

## Package and script authority

SwiftPM resolution mode is contract-owned. Locked mode requires exact checked-in
resolved files and passes both automatic-resolution rejection flags. Any lock or
source mutation fails. Checked-in Python or shell scripts must be regular,
repository-tracked files and receive only fixed contract arguments. Consumer
specific environment names, schemes, commands, media preparation, and recovery
rules remain in `contracts/apple-validation.json`, additive reviewed
`contracts/apple-validation-*.json` fragments, or consumer-owned scripts; the
shared Python implementation contains no product-name branch. Fragments may add
only new task and consumer-contract identifiers; collisions with the base
contract fail closed and every added task is validated by the same Apple task
schema before it can be selected.

## Outputs and artifacts

The workflow returns deterministic result, stage summary, exact verified
runtime versions, redacted simulator identity, evidence ID, clean-tree state,
cleanup state, and diagnostic-exception state. Routine runs upload no GitHub
Actions artifact. A non-empty diagnostic exception is accepted only when the
selected task already registers that exact named exception; absence remains the
default.

## Cleanup

The validator registers state below the prepared workflow root and removes, in
an `always()` path, DerivedData, result bundles, SwiftPM and CocoaPods state,
logs, reports, native output, caches, generated source output, and job-created
simulators. Cleanup uses lexical contract paths and `lstat`-based no-follow
removal. A symlink is unlinked rather than traversed, caller deletion paths are
not accepted, outside sentinels are preserved, and residue is a terminal
failure. Fixed source and stale central-checkout roots are also removed without
following links and participate in the combined cleanup outcome.

## Smoke workflows

`.github/workflows/apple-validation-smoke.yml` checks out the exact
pull-request implementation and executes the same planner, composite action,
contract, workspace isolation, and cleanup path directly for a product-neutral
Debug fixture on iOS simulator, tvOS simulator, and unsigned macOS.

`.github/workflows/apple-certification-smoke.yml` independently proves the
Release path. Its fixed three-row, non-fail-fast matrix resolves
`ciw-apple-release-smoke`, runs iOS simulator, tvOS simulator, and macOS Release
compile jobs on the exact same pull-request SHA, verifies Apple-specific and
workspace cleanup/residue, and requires zero routine Actions artifacts.

Both smoke workflows are direct repository-owned callers rather than nested
calls to `reusable-apple.yml`; this preserves the repository's maximum reusable
depth of one. Simulator smoke is not physical-device, signing, release
publication, or store proof.
