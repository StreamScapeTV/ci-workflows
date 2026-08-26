# Flutter validation architecture

## Authority model

The Flutter validator is a data-driven adapter over the existing exact-source,
workspace, policy, evidence, runner, and cleanup primitives. It does not own
product commands, deployment, publication, signing, or physical-device
execution.

The implementation is split into five typed layers:

1. `flutter_types.py` defines immutable requests, plans, commands, toolchains,
   results, stages, and semantic capacities.
2. `flutter_contract.py` validates the versioned machine contract, resolves pin
   authority, checks paths, builds deterministic plans, and parses runtime
   identity.
3. `flutter_execution.py` creates isolated state, executes fixed commands,
   composes the existing Node source-audit adapter when declared, verifies
   output and source authority, generates a deterministic evidence identity,
   and removes residue.
4. `flutter.py` exposes the bounded public façade.
5. `ciw_flutter.py` provides both the future `ciw flutter validate` adapter and
   the pre-registration compatibility entry point used while issue #11 owns
   shared CIW registration files.

The composite action is a thin environment adapter. The reusable workflow does
not clone or check out central repository source. It consumes the current Central library through `@main` for repository-qualified first-party composite actions. Exact admitted
consumer source is checked out separately through the
`exact-checkout@main` helper and reverified before execution. The reusable workflow
owns only orchestration around workspace preparation, reviewed runtime setup,
one semantic execution path, and terminal cleanup. First-party helper identity is
therefore not a separately versioned checkpoint; exact caller source and runtime
identity are verified independently because they are functional correctness
boundaries.

## Deterministic plan

A plan is a pure function of:

- `contracts/flutter-validation.json`;
- exact admitted SHA and trust class;
- consumer-contract identifier;
- profile;
- declared pin values when source is available.

The plan records exact Flutter/Dart identity, runner capability, workspace
profile, timeout, stage order, fixed command IDs, optional Node composition,
checked-in gate path, and optional device-handoff packet. No host path, runner
label, credential, endpoint, or secret is included.

Planning may run without installing Flutter. Execution re-resolves source pin
authority and therefore cannot trust a caller-supplied plan or runtime value.

## Pin and lock invariants

The accepted FVM shape is one JSON object with one `flutter` key. Plain pin files
contain one exact semantic version. Pin paths are fixed names; every path
component is checked for symlinks and containment. Existing recognized sources
must agree even when only one is required by the selected consumer contract.

`pubspec.yaml`, `pubspec.lock`, and all pin files are hashed before commands.
After commands, the same authority set must exist with the same hashes.
Generated output is removed before the final clean-tree check. A changed lock,
pin, manifest, tracked source, or undeclared residue fails the result.

## Runner and platform invariants

The workflow has explicit jobs for `portable`, `mobile`, and `apple`. The plan
selects a semantic capability; no expression derived from caller text becomes a
runner label. Every dynamically scheduled job consumes exactly the reviewed
`runs_on_json` planner output through
`${{ fromJSON(needs.plan.outputs.runs_on_json) }}`.

- `portable` never installs Flutter and handles source audit or device handoff.
- `mobile` handles quality, canonical gates, Android debug, and compatibility.
- `apple` handles iOS simulator compile only.

Android commands contain `--debug`, never release or publication flags. iOS
commands contain `--simulator`, debug/unsigned flags, and never use a keychain,
signing identity, provisioning profile, store credential, TestFlight,
notarization, or a physical device.

## Exact smoke topology

Exact-head smoke proof is deliberately split across two direct pull-request
workflows. `.github/workflows/flutter-validation-smoke.yml` owns portable
source audit, focused tests, and mobile Android execution.
`.github/workflows/flutter-apple-validation-smoke.yml` owns Apple simulator
execution. Each dynamically scheduled execution job consumes the exact trusted
planner output without reconstructing labels or exceeding the reviewed
reusable-workflow depth.

The smoke workflows create disposable product-neutral Flutter projects only
after exact trusted workflow source is admitted and checked out and reviewed
runtime setup is complete. The initial plain `flutter pub get` creates the
disposable fixture lock; the validator then runs the contract-owned
`flutter pub get --enforce-lockfile` command and verifies the lock does not
change. Build products, dependency state, and workspace state are removed by the
normal cleanup path. There is no separate Actions-artifact inventory finalizer;
privacy-sensitive output remains prohibited independently by the shared privacy
boundary.

## State and evidence

Each execution creates a marker-bound Flutter state subtree with isolated home,
pub cache, Flutter state, Gradle home, CocoaPods home, DerivedData, temporary
space, logs, reports, and output directories. Sensitive host environment keys
and production-capable credentials are removed from the child environment.

The result contains only bounded identifiers, exact tool versions/revisions,
stage names, the lock hash, clean/cleanup booleans, output verification, the
optional handoff packet, and a SHA-256 evidence identity over those bounded
facts. It does not retain command output or build products.

## Test model

Focused tests use synthetic consumer trees and a deterministic fake command
runner. They cover exact pins, FVM parsing, agreement and mismatch, malformed
and ranged values, symlink and traversal rejection, Flutter/Dart mismatch,
lock mutation, stage order, checked-in gates, Android/iOS boundaries, Node
composition, command failures, dirty source, residue, forbidden caller inputs,
deterministic results, ordinary setup-action references, first-party `@main`
helper reuse, semantic workflow jobs, independent trusted mobile/Apple
selectors, and real mobile/Apple smoke definitions.
