# Apple validation workflow

`validation.apple` version `1.0.0` is the product-neutral reusable workflow at
`.github/workflows/reusable-apple.yml`. Its stable public check is
`CI / Apple validation`.

The workflow accepts one exact admitted source SHA, one bounded validation
profile, and one reviewed consumer-contract identifier. Optional project,
workspace, scheme, configuration, test-plan, working-directory, and diagnostic
exception inputs are accepted only when they exactly match the checked-in
consumer contract. Callers cannot select a runner, host, architecture, Xcode
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
rules remain in `contracts/apple-validation.json` or consumer-owned scripts;
the shared Python implementation contains no product-name branch.

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
failure.

## Smoke workflow

`.github/workflows/apple-validation-smoke.yml` calls the exact pull-request
version of the reusable workflow for a product-neutral fixture on iOS simulator,
tvOS simulator, and unsigned macOS. It accepts same-repository pull requests
only and independently verifies that the run retained zero Actions artifacts.
Simulator smoke is not physical-device, signing, release, or store proof.
