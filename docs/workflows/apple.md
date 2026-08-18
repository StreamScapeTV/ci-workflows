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

## Compile and test destinations

Compile-only iOS and tvOS stages use generic simulator-platform destinations.
They do not select, create, boot, or claim a concrete simulator. macOS compile
stages use the unsigned macOS destination. Each platform is still an independent
SDK build; shared state does not imply that one platform binary satisfies
another platform.

A test stage may acquire only the contract-owned iOS or tvOS simulator matching
its platform. The existing host-user ownership registry, exact runtime/device-
type checks, stale-row reconciliation, exclusive ownership lock, redacted
simulator identity, and no-follow cleanup are reused unchanged. Test stages run
sequentially; a created simulator is reclaimed before a later simulator stage
can acquire its device. Terminal cleanup rechecks all planned simulator families
and fails if owned residue remains.

## Caller-owned plan safety

The public protected-full plan is bounded to eight stages and 32 KiB. Structural
keys are exact; unknown or duplicate stage identities fail closed. Paths must be
relative, non-traversing paths under the admitted source. Test selectors are
bounded and accepted only for `test` operations.

`xcodebuild_arguments` is not an arbitrary Xcode command channel. The public
boundary accepts only `-quiet`, `-showBuildTimingSummary`, or reviewed boolean
build settings such as `ENABLE_TESTABILITY=YES|NO`. Output-routing settings such
as `SYMROOT`, `OBJROOT`, or `CONFIGURATION_BUILD_DIR` are rejected, as are
caller-supplied destinations, DerivedData/result-bundle/package-state paths,
signing identities, provisioning, archive/export, notarization, or store
operations. Central still injects the fixed signing-disabled settings.

Caller cleanup paths are likewise not arbitrary deletion authority. They must be
relative generated leaves such as `build`, `.build`, `.swiftpm`, or `xcuserdata`;
other cleanup targets fail before expensive execution. No-follow removal and the
final exact-source clean check remain mandatory.

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
is product-neutral and does not add a central application repository allowlist,
product ID, product command, or branch/path/concurrency policy.

## Exact toolchain and signing boundary

Apple execution verifies the reviewed Xcode version/build, Swift version,
required iOS/tvOS/macOS SDK versions, and simulator runtime/device type where a
concrete simulator is required. `DEVELOPER_DIR` and other caller-selected Xcode
path controls are removed from the execution environment.

Every Xcode command is centrally constructed with an isolated DerivedData path,
isolated cloned Swift package path, stage-local result bundle, bounded
destination, and:

- `CODE_SIGNING_ALLOWED=NO`;
- `CODE_SIGNING_REQUIRED=NO`;
- empty `CODE_SIGN_IDENTITY`.

The validator never archives, exports, notarizes, uploads to TestFlight/App Store,
imports a keychain, reaches Kubernetes, or deploys a product.

## Immutable helper reuse

The reusable workflow invokes `actions/validate-apple` through guarded issue
#336 checkpoint `2dacd98d19c5e136ce4803ab70b0f7ebd45414bf`.
The corresponding action-lock release label is
`issue-336 final Apple v2 checkpoint`.
That checkpoint includes the protected-full planner/executor and the public plan
filesystem guard. Exact source checkout, workspace preparation, optional private
dependency checkout, evidence rendering, and registered-state cleanup use the
reviewed immutable foundation helpers.

The action archive supplies its Python modules relative to `GITHUB_ACTION_PATH`;
the reusable workflow does not clone a second central checkout or use a caller-
selected helper version. The helper identity is recorded in
`contracts/action-tool-lock.json`.

## Private dependency boundary

Protected-full accepts at most one optional exact private dependency repository,
SHA, subdirectory, and bounded dependency ID plus one named transient
`private_dependency_token`. The standard private-dependency action performs the
exact detached checkout, verifies the selected subpath, erases Git remotes and
credentials, and exposes registered-state identity. Apple execution rejects the
dependency unless every piece of checkout evidence matches the requested exact
identity. `secrets: inherit` is not used.

After that verification succeeds, Apple execution derives the dependency's exact
GitHub HTTPS identity from the validated `owner/name` input and installs a
process-scoped Git `url.<local-file-URI>.insteadOf` mapping for both the exact
HTTPS form and its `.git` spelling. Xcode/SwiftPM can therefore resolve that one
private package from the verified local checkout after remotes and credentials
have been erased. The mapping is supplied only through the validation process
environment, disables interactive Git prompting, and does not write repository,
user, or system Git configuration. It does not create a general private-package
credential channel or permit a caller-selected local path.

## Artifacts and cleanup

Routine Apple validation uploads no GitHub Actions artifact and uses no GitHub
Actions cache. Structured summaries and evidence stay bounded/redacted.

Apple-specific state is marker-bound and removed with lexical, `lstat`-based
no-follow cleanup. The workflow separately removes the exact source checkout and
then calls the registered workspace cleanup action. Cleanup/residue failure,
source mutation, or workspace-cleanup failure makes the terminal Apple result
fail.

## Smoke workflow

`.github/workflows/apple-validation-smoke.yml` is the repository-owned exact-head
acceptance caller. It has one general-small planner, one real semantic Apple job,
and one general-small zero-artifact finalizer. The real Apple job runs the
product-neutral fixture's iOS, tvOS, and macOS compile stages sequentially from
one exact checkout/workspace, proving the protected-full capacity shape without
booting simulators for compile-only work.

The smoke workflow watches Apple implementation, guard, contract-fragment,
public-API, and test authority paths. It directly invokes the local composite
implementation rather than nesting `reusable-apple.yml`, preserving the maximum
reusable-workflow depth. The smoke is unsigned simulator/macOS validation only;
it is not physical-device, signing, release-publication, or store evidence.
