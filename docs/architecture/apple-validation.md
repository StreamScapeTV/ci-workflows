# Apple validation architecture

## Authority model

The reusable Apple validator owns orchestration, contract validation, semantic
runner selection, exact toolchain checks, deterministic simulator lifecycle,
hermetic command construction, redacted reporting, and terminal cleanup.
Consumer repositories retain authority over their checked-in project/workspace
or package path, scheme, configuration, optional test plan, exact command
profile, dependency-preparation script, recovery scripts, package-resolution
mode, protected source paths, and accepted diagnostic exception. The public
workflow exposes only the already-reviewed API vocabulary: version file, working
directory, command profile, script path, bounded platform, scheme, destination
profile, and named diagnostic exception. Every supplied value must equal the
selected task; container paths, configurations, test plans, device identities,
and arguments stay internal.

`contracts/apple-validation.json` is the reviewed data boundary. The shared
modules do not branch on repository or product names. Repository compatibility
is selected by a consumer-contract identifier that must map back to the caller's
exact `owner/repository` identity.

## Data flow

1. The workflow invokes the reviewed first-party Apple action through the current
   shared Central library channel `@main` for planning and execution.
2. The `portable` planning job parses the checked-in contract, binds the caller
   repository/profile to one task, rejects forbidden inputs, and resolves the
   semantic `apple` runner mapping.
3. The Apple job checks out the exact admitted caller SHA with persistent Git
   credentials disabled and verifies the detached source and clean tree.
4. The workspace action creates a registered `apple` state profile.
5. The Apple action verifies Xcode, Swift, SDKs, and simulator runtime/device
   type authority before executing any contract-owned stage.
6. Commands are constructed from typed plan values; caller strings never become
   a shell command or arbitrary argument vector.
7. Source authority is rehashed, Git cleanliness is rechecked, registered state
   is removed, simulator deletion is verified, and residue is rejected.
8. Stable outputs expose only bounded deterministic values and redacted
   identities.

## Typed modules

- `apple_types.py` defines profiles, runner capabilities, stages, requests,
  plans, results, simulator contracts, and stable failures.
- `apple_contract.py` parses the versioned JSON contract, validates safe paths,
  repository bindings, commands, simulator families, environment bindings,
  cleanup paths, and named diagnostic exceptions.
- `apple_execution.py` verifies source/toolchain authority, owns simulator
  lifecycle, constructs `xcodebuild`/Swift/script invocations, detects mutation,
  and performs no-follow cleanup.
- `apple.py` is the public Python facade and GitHub input boundary.
- `ciw_apple.py` adapts the facade to `ciw apple validate`.
- `actions/validate-apple/action.yml` and `scripts/ci/apple.py` remain thin
  adapters.

## Trust boundary

Only `trusted-pr` and `trusted-exact` source are admitted. Fork source is
rejected by contract planning before Apple execution. Apple capacity grants no
signing identity, provisioning profile, keychain, physical device, App Store,
TestFlight, notarization, registry, Kubernetes, production database, or
production credential authority.

The isolated environment removes signing, provisioning, destination, keychain,
store, deployment, and secret-bearing controls. It uses registered HOME,
temporary, module-cache, CocoaPods, DerivedData, result, native-output, and cache
roots. Contract-specific environment variables may point only to one of those
registered directories.

## Simulator ownership

The simulator contract includes exact platform, runtime identifier/version,
device family, device type, and device-type identifier. A per-job deterministic
name prevents cross-run adoption. Selection accepts at most one exact match and
requires the UDID to be present in the current job registry. Booted but unowned,
shutdown but unowned, ambiguous, unavailable, wrong-family, wrong-runtime, and
malformed devices fail closed.

Creation writes the registry before boot. Cleanup reads only that registry,
shuts down and deletes each registered UDID, checks the complete simulator
inventory for residue, and then removes the registry. No global `shutdown all`,
`delete unavailable`, or caller-provided UDID operation exists.

## Command safety

`xcodebuild` receives an explicit project/workspace, scheme, configuration,
destination, DerivedData root, cloned-package root, result-bundle root, fixed
action, and fixed contract arguments. Simulator destinations are generated
internally as `platform=<reviewed simulator platform>,id=<owned UDID>`; macOS is
exactly `platform=macOS`. Generic destinations and physical-device values are
not inputs.

Swift commands receive an exact package path and isolated scratch path.
Checked-in script commands are admitted only as tracked regular files with fixed
arguments. No command path uses `shell=True`, `eval`, a callback, a pipe, or a
caller-provided executable.

## Bounded Release certification profiles

Release configuration is selected only through checked-in consumer contracts;
it is not a public string input. Existing `command_profile: iptv-apple` remains
the compatible Debug contract. `command_profile: iptv-apple-release` is a
separate reviewed contract for `StreamScapeTV/iptv-apple` that maps exactly the
existing `ios-simulator`, `tvos-simulator`, and `macos` validation profiles to
`iptv-ios-release`, `iptv-tvos-release`, and `iptv-macos-release`.

Those three tasks use the same `streamscapetv.xcworkspace` and `streamscapetv`
scheme as the Debug tasks, but their contract-owned configuration is exactly
`Release` and their fixed action is compile-only `build`. The iOS and tvOS
variants retain the centrally selected simulator contracts; macOS remains
exactly `platform=macOS`. The normal execution builder still injects
`CODE_SIGNING_ALLOWED=NO`, `CODE_SIGNING_REQUIRED=NO`, and an empty
`CODE_SIGN_IDENTITY`, so Release validation gains no archive, signing,
provisioning, notarization, store, physical-device, deployment, or publication
authority.

A Release certification caller therefore makes three independent Apple calls
with the same admitted SHA and the same `iptv-apple-release` command profile,
changing only the bounded validation profile/platform/destination tuple. Each
plan/result projects the exact admitted `source_sha`, making same-SHA evidence
explicit. Supplying `INPUT_CONFIGURATION` remains forbidden, and a direct
request that conflicts with the selected task's `Release` value fails closed.

`ciw-apple-release-smoke` mirrors the same three-task shape against the checked-in
Apple smoke fixture. It exists only for exact-head central runtime evidence;
normal `ciw-apple-smoke` Debug tasks remain unchanged.

## Mutation and cleanup invariants

Protected files and package-resolution files are hashed before execution and
again before cleanup. Git status detects every tracked or untracked mutation.
A resolved-file change receives the package-resolution failure classification;
other changes receive source-mutation/dirty-source failures.

All deletion targets originate in the selected checked-in task or the registered
state root. Each path is resolved lexically, every parent is checked with
`lstat`, and recursive deletion never follows a symlink. Cleanup failure is
reported without replacing the primary execution failure, and a separate
cleanup result marks terminal cleanup failure.

## Smoke topology

The smoke caller executes the exact checked-out Apple action and contract
directly instead of nesting the public reusable workflow. This is deliberate:
the repository permits public reusable depth one, so a nested smoke caller would
create rejected depth two. The direct smoke uses the same plan/execute/cleanup
modules and exact source. Existing Debug jobs continue to prove iOS, tvOS,
macOS, source integrity, cleanup/residue behavior, and private-output
confidentiality; the Release matrix adds independent iOS, tvOS, and macOS jobs
against the same pull-request SHA and retains those same cleanup and
confidentiality boundaries. Public or non-private artifact behavior remains
feature-scoped rather than a repository-wide zero-artifact invariant.
