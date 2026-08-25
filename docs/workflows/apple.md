# Apple validation workflow

`validation.apple` version `2.1.0` is the product-neutral reusable workflow at
`.github/workflows/reusable-apple.yml`. Its stable public check remains
`CI / Apple validation`.

The v2 API keeps the existing legacy profile inputs for current consumers and adds
a `protected-full` mode with one strict caller-owned `validation_plan_json`.
Version 2.1 adds one optional full `owner/name` `source_repository` for Central-
dispatched validation. When that value is absent, existing callers retain their
current repository identity and credential behavior. When it identifies a source
repository different from the Central workflow repository, the Apple execution
job uses fixed named GitHub App credentials to mint a transient contents-read
token restricted to that exact repository. The token is masked, used only for
exact source checkout, and is neither a public workflow output nor product-source
input. The request authority remains the admitted source/ref contract; repository
credentials do not add source-selection, signing, release, or deployment authority.

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

1. when an external `source_repository` is selected, mints one exact repository
   contents-read token and checks out the exact admitted caller source once;
2. prepares one marker-bound Apple workspace with Actions cache disabled;
3. optionally checks out one exact private dependency once and accepts it only
   after exact SHA, repository, subdirectory, remote-erasure, and credential-
   erasure evidence match the plan; Central-dispatched execution mints a separate
   exact dependency contents-read token in that same Apple job when needed;
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
branch/path/concurrency policy. `source_repository` is a full repository identity,
not a StreamScapeTV prefix or product selector, so an authorized GitHub App
installation may serve a private repository in another organization without
changing the validation API.

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

The reusable workflow invokes `actions/validate-apple` through the issue #495
external-source identity checkpoint
`33da58aed7f0423d33cea69ebd7eb829b283ec0d`.
That checkpoint preserves the previously reviewed hosted simulator-confidence
and bounded compiler-diagnostics behavior while adding only explicit external
source repository identity to the existing typed Apple request. The corresponding
action-lock release label is `issue #495 external-source identity checkpoint`.

When Central must access a private source or private dependency repository, the
same Apple execution job invokes `actions/github-app-repository-token` through
the issue #495 bounded repository-token checkpoint
`56f4859ae09944df6eaaafa7c808e5a1081e61af`. That helper accepts only one full
`owner/name` repository, reads the App id/private key from fixed environment
variables, requests only `contents: read` for that repository, masks the issued
token before exporting it as a step output, and exposes no caller-selected secret
name or permission surface.

Result-bundle fallback remains fail-closed and caller-independent: Central accepts
only the exact stage-local `.xcresult` path already constructed under its owned
Apple validation state, parses a bounded build-results response, projects at
most one compiler diagnostic, strips paths to safe basenames, redacts URLs and
secret-shaped values, and prefixes every emitted payload line so GitHub
workflow-command-shaped text remains inert. Missing, corrupt, oversized,
symlinked, or out-of-state bundles fall back to the existing bounded sanitized
stdout/stderr diagnostic. No result bundle, DerivedData tree, build log, or
other routine diagnostic artifact is uploaded, and emission happens before the
existing terminal cleanup/residue/source-clean boundary removes run-owned state.

Legacy and protected-full requests still dispatch through the existing typed
`ciw apple validate` implementation; only the explicit simulator-confidence
scope enters the strict packet adapter and existing lower-level simulator
ownership functions. Physical-device authority remains outside this workflow in
`validation.device`.

Exact source checkout, workspace preparation, optional private dependency
checkout, evidence rendering, and registered-state cleanup continue to use their
reviewed immutable foundation helpers.

The action archive supplies its Python modules relative to `GITHUB_ACTION_PATH`;
the reusable workflow does not clone a second central checkout or use a caller-
selected helper version. The helper identity is recorded in
`contracts/action-tool-lock.json`. Product callers consume the reusable workflow
through the active library channel such as `@main`; they do not configure these
internal helper checkpoints themselves.

## Private dependency boundary

Protected-full accepts at most one optional exact private dependency repository,
SHA, subdirectory, and bounded dependency ID. Existing callers may continue to
supply the named transient `private_dependency_token`. When Central-dispatched
execution selects an external `source_repository`, the Apple job instead mints a
separate transient contents-read token for the exact dependency repository using
the same fixed GitHub App credentials. The standard private-dependency action
performs the exact detached checkout, verifies the selected subpath, erases Git
remotes and credentials, and exposes registered-state identity. Apple execution
rejects the dependency unless every piece of checkout evidence matches the
requested exact identity. `secrets: inherit` is not used, and neither App
credential nor issued token is exposed to product source.

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