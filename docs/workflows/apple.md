# Apple validation workflow

`validation.apple` version `2.0.0` is the product-neutral reusable workflow at
`.github/workflows/reusable-apple.yml`. Its stable public check remains
`CI / Apple validation`.

The v2 API keeps the existing legacy profile inputs for current consumers and adds
a `protected-full` mode with one strict caller-owned `validation_plan_json`.
Protected-full validates a bounded list of Apple stages before the heavy executor
runs. Each stage describes only technology-level data: a relative project or
workspace, scheme, configuration, platform, Xcode operation, optional test plan,
package-resolution mode, bounded test selectors, expected outputs, generated
cleanup leaves, and a very small allowlist of non-routing Xcode flags/boolean
build settings. Arbitrary Xcode output-path settings, destinations, signing,
archive/export, keychain/store operations, and arbitrary cleanup targets are
rejected before execution.

`validation.apple` also exposes one explicit `simulator-confidence` scope for
trusted exact callers that need runtime confidence on a single iOS or tvOS
simulator. This is not a second workflow and is not an extension of routine
protected-full. Its strict packet contains only a schema version, bounded packet
identity, `ios` or `tvos`, and up to eight checked-in repository-relative bash
script steps with bounded argv. Central appends its reserved simulator argument
to every step only after Central has created and owns the exact simulator.
Callers cannot provide a simulator UDID, runner selector, shell command string,
signing identity, environment-variable name, or deployment/store control.

## Single-executor protected-full model

A protected-full call has one protected Linux planner and exactly one semantic
`apple` execution job. That Apple job:

1. checks out the exact admitted caller source once;
2. prepares one marker-bound Apple workspace with Actions cache disabled;
3. optionally checks out one exact private dependency once and accepts it only
   after exact SHA, repository, subdirectory, remote-erasure, and credential-
   erasure evidence match the plan;
4. executes the requested Apple stages sequentially in one isolated process
   environment;
5. shares DerivedData and SwiftPM state between stages while giving each stage
   its own result-bundle directory;
6. performs Apple cleanup/residue verification once at the terminal boundary,
   then verifies the source remained exact and clean and removes the registered
   workspace.

The workflow does not create separate cold iOS, tvOS, and macOS jobs merely to
obtain separate status names. `test_summary` preserves bounded per-stage
platform/operation results from the one executor.

Source-only policy or repository-recovery work does not need to be placed in a
protected-full plan. A plan containing only source scripts is rejected so the
caller can keep those checks on sized Linux general capacity instead of
allocating a Mac solely for source inspection. Existing legacy source/profile
calls remain supported during migration.

## Explicit simulator-confidence model

Simulator confidence is a separate scheduling and evidence class. The Linux
planner validates the strict packet, derives trusted exact source identity, and
emits fixed GitHub-hosted `["macos-latest"]` for the one executor. The caller
never selects that runner. Compatible hosted macOS is permitted because the
mode owns only CoreSimulator runtime confidence and receives no physical-device,
signing, release, store, registry, deployment, or production authority.

For one executing packet Central reuses the existing Apple simulator ownership
implementation. It selects the reviewed `ciw-ios` or `ciw-tvos` runtime/device
type from the checked-in Apple contract, creates and boots exactly one CIW-owned
simulator, records ownership, and replaces only the reserved
`{ciw.apple.simulator_udid}` argv token after successful creation. Every caller
script runs sequentially against that same identity. Terminal cleanup shuts down
and deletes only the owned simulator (and any exact owned companion covered by
the existing lifecycle contract), reconciles stale ownership safely, verifies
zero simulator residue, verifies the admitted source remained clean, and removes
the registered workspace.

The returned `test_summary` is marked `simulator-confidence-only` and explicitly
records that it has no physical-device, signing, or release authority. This
result cannot satisfy physical Apple TV acceptance, native MPV/VLC engine proof,
signing/notarization, release publication, or store certification. Real physical
iOS/tvOS work remains exclusively on guarded `validation.device` using
organization-managed `[macOS, ARM64]` Apple capacity.

## Compile and test destinations

Compile-only iOS and tvOS stages use generic simulator-platform destinations.
They do not select, create, boot, claim, inspect, reclaim, shut down, or delete a
concrete simulator. macOS compile stages use the unsigned macOS destination.
Each platform is still an independent SDK build; shared state does not imply that
one platform binary satisfies another platform.

Protected-full is the routine simulator-free gate. iOS/tvOS `operation: test`
is rejected by the public plan guard before Apple runner allocation. macOS
host-capable XCTest remains supported. Compile-only protected-full execution,
cleanup, and residue checking do not enter CoreSimulator ownership or inventory.
Generic `platform=iOS Simulator` / `platform=tvOS Simulator` destination text is
an SDK build destination and does not mean a simulator device was booted.

The lower-level reviewed simulator primitives are used only by separately
reviewed explicit runtime/smoke paths such as `simulator-confidence`. Those
primitives retain deterministic runtime/device-type selection, exclusive
ownership, stale-row reconciliation, redacted simulator identity, exact
CIW-owned companion handling, and no-follow cleanup. They are not part of
routine protected-full execution.

## Caller-owned plan safety

The public protected-full plan is bounded to eight stages and 32 KiB. Structural
keys are exact; unknown or duplicate stage identities fail closed. Paths must be
relative, non-traversing paths under the admitted source. Test selectors are
bounded and accepted only for operations that permit test selection; routine
protected-full iOS/tvOS runtime tests are rejected before execution.

`xcodebuild_arguments` is not an arbitrary Xcode command channel. The public
boundary accepts only `-quiet`, `-showBuildTimingSummary`, or reviewed boolean
build settings such as `ENABLE_TESTABILITY=YES|NO`. Output-routing settings such
as `SYMROOT`, `OBJROOT`, or `CONFIGURATION_BUILD_DIR` are rejected, as are
caller-supplied destinations, DerivedData/result-bundle/package-state paths,
signing identities, provisioning, archive/export, notarization, or store
operations. Central still injects the fixed signing-disabled settings.

Simulator-confidence argv is even narrower: every argument is one bounded
non-whitespace token from the strict technology-level character set, the
reserved simulator token is forbidden from caller data, and Central appends
`--simulator` plus that internal token itself. Script paths are checked-in
relative paths and traversal is rejected. No arbitrary environment map or shell
string is accepted.

Caller cleanup paths are likewise not arbitrary deletion authority. They must be
relative generated leaves such as `build`, `.build`, `.swiftpm`, or `xcuserdata`;
other cleanup targets fail before expensive execution. No-follow removal and the
final exact-source clean check remain mandatory.

## Protected Xcode source identity

Protected-full treats the Git index as the source-of-truth boundary for declared
Xcode project/workspace containers. For a container inside a Git checkout,
Central hashes every tracked descendant with its repository-relative path before
and after execution. This keeps tracked project/workspace changes fail-closed
while excluding ordinary ignored Xcode/SwiftPM generated state such as
`xcuserdata` and generated workspace package-resolution data from the protected
source digest.

This is not an ignore-pattern bypass. Central does not learn product-specific
ignored paths. Non-ignored untracked files remain rejected by the exact-source
Git cleanliness check, and tracked file mutation or deletion still changes the
protected digest and fails `source_mutation`. Non-Git synthetic callers retain
the recursive protected-tree hashing behavior. Resolved-file mutation
classification remains unchanged.

## Legacy profiles

The existing checked-in legacy Apple contract remains available while consumers
migrate. It supports:

| Profile | Purpose | Execution boundary |
|---|---|---|
| `source-audit` | Reviewed source/toolchain audit | semantic `apple` legacy path |
| `swift-package` | Reviewed Swift package build or test | semantic `apple` |
| `ios-simulator` | One reviewed iOS build/test | semantic `apple` |
| `tvos-simulator` | One reviewed tvOS build/test | semantic `apple` |
| `macos` | One reviewed unsigned macOS build/test | semantic `apple` |
| `native-dependency-preparation` | Checked-in dependency preparation | semantic `apple` |
| `repository-recovery` | Checked-in recovery/audit script | semantic `apple` |

Legacy consumers keep their checked-in consumer/task mappings. Protected-full
and simulator-confidence are product-neutral and do not add a central
application repository allowlist, product ID, product command, or
branch/path/concurrency policy.

## Exact toolchain and signing boundary

Apple execution verifies the reviewed Xcode version/build, Swift version,
required iOS/tvOS/macOS SDK versions, and simulator runtime/device type only when
a separately reviewed runtime path actually requires a concrete simulator.
`DEVELOPER_DIR` and other caller-selected Xcode path controls are removed from
the execution environment.

Every Xcode command is centrally constructed with an isolated DerivedData path,
isolated cloned Swift package path, stage-local result bundle, bounded
destination, and:

- `CODE_SIGNING_ALLOWED=NO`;
- `CODE_SIGNING_REQUIRED=NO`;
- empty `CODE_SIGN_IDENTITY`.

The validator never archives, exports, notarizes, uploads to TestFlight/App Store,
imports a keychain, reaches Kubernetes, or deploys a product.

## Immutable helper reuse

The reusable workflow invokes `actions/validate-apple` through the issue #516
simulator-confidence checkpoint
`78b9cacdb001a61e18a84e23532f4f397c13875b`.
That checkpoint preserves the issue #496 simulator-free protected-full behavior
and adds only the explicit strict simulator-confidence dispatch. The corresponding
action-lock release label is `issue #516 simulator-confidence checkpoint`.
Legacy and protected-full requests still dispatch through the existing typed
`ciw apple validate` implementation; only the explicit simulator-confidence
scope enters the strict packet adapter and existing lower-level simulator
ownership functions.

Exact source checkout, workspace preparation, optional private dependency
checkout, evidence rendering, and registered-state cleanup continue to use their
reviewed immutable foundation helpers.

The action archive supplies its Python modules relative to `GITHUB_ACTION_PATH`;
the reusable workflow does not clone a second central checkout or use a caller-
selected helper version. The helper identity is recorded in
`contracts/action-tool-lock.json`. Product callers consume the reusable workflow
through the active library channel such as `@main`; they do not configure this
internal helper checkpoint themselves.

## Private dependency boundary

Protected-full accepts at most one optional exact private dependency repository,
SHA, subdirectory, and bounded dependency ID plus one named transient
`private_dependency_token`. The standard private-dependency action performs the
exact detached checkout, verifies the selected subpath, erases Git remotes and
credentials, and exposes registered-state identity. Apple execution rejects the
dependency unless every piece of checkout evidence matches the requested exact
identity. `secrets: inherit` is not used.

Simulator-confidence does not consume the private-dependency channel. Its
planner emits no dependency request, so the optional checkout step remains
skipped for that mode.

After protected-full dependency verification succeeds, Apple execution derives
the dependency's exact GitHub HTTPS identity from the validated `owner/name`
input and installs a process-scoped Git `url.<local-file-URI>.insteadOf` mapping
for both the exact HTTPS form and its `.git` spelling. Xcode/SwiftPM can therefore
resolve that one private package from the verified local checkout after remotes
and credentials have been erased. The mapping is supplied only through the
validation process environment, disables interactive Git prompting, and does
not write repository, user, or system Git configuration. It does not create a
general private-package credential channel or permit a caller-selected local
path.

## Artifacts and cleanup

Routine Apple validation uploads no GitHub Actions artifact and uses no GitHub
Actions cache. Structured summaries and evidence stay bounded/redacted.

Apple-specific state is marker-bound and removed with lexical, `lstat`-based
no-follow cleanup. Routine protected-full cleanup never enters CoreSimulator
ownership/inventory. The workflow separately removes the exact source checkout
and then calls the registered workspace cleanup action. Cleanup/residue failure,
source mutation, or workspace-cleanup failure makes the terminal Apple result
fail. Explicit runtime modes retain exact-owned-simulator residue guarantees.

## Repository-local smoke workflow

`.github/workflows/apple-validation-smoke.yml` is the public Central repository's
exact-head contract caller. Because `ci-workflows` is public while organization
self-hosted runner groups remain private-repository capacity, its planning and
zero-artifact control jobs use canonical GitHub-hosted `[ubuntu-latest]`.
They prove exact source, Apple plan/contract behavior, and that the ordinary
semantic Apple executor selector remains exactly `["macOS","ARM64"]`.
Simulator-confidence planning is separately contract-tested to emit fixed
`["macos-latest"]` without granting physical-device authority.

The real ordinary Apple job definition remains in the workflow with the
centrally resolved selector and complete native implementation, but is gated to
private repository context. It is therefore skipped, rather than left queued on
inaccessible private Mac capacity, when the public Central repository validates
a pull request. Hosted simulator-confidence is intentionally a different
capability and does not depend on those persistent organization Macs.

The smoke workflow watches Apple implementation, guard, contract-fragment,
public-API, and test authority paths. It directly invokes the local composite
implementation for hosted contract validation rather than nesting
`reusable-apple.yml`, preserving the maximum reusable-workflow depth. This public
smoke is not physical-device, signing, release-publication, store, or native Mac
execution evidence.
