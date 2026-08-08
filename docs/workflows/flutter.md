# Reusable Flutter validation

`validation.flutter` version `1.0.0` is implemented by
`.github/workflows/reusable-flutter.yml`. Its stable required check is
`CI / Flutter validation`.

The workflow accepts only an exact admitted source SHA, one reviewed
product-neutral consumer-contract identifier through the historical bounded
`command_profile` field, one bounded validation profile, and exact optional
contract-match assertions for version file, working directory, script path,
platform, and the reserved empty artifact-exception field. Source trust is
derived internally from the admitted GitHub event. It does not accept a runner,
label, matrix, Flutter download URL, runtime, package manager, command, argument
list, shell, callback, device, engine, signing identity, registry, database,
Flux target, or deployment input.

## Profiles

| Profile | Semantic capacity | Runtime installation | Purpose |
| --- | --- | --- | --- |
| `source-audit` | `portable` | none | Validate pins, lock authority, contract compatibility, and source-only policy. |
| `quality` | `mobile` | exact Flutter | Locked restore, analysis, and tests. |
| `canonical-gate` | `mobile` | exact Flutter | Execute one reviewed checked-in consumer gate script. |
| `android-debug` | `mobile` | exact Flutter | Produce, verify, and remove one debug unsigned Android output. |
| `ios-simulator` | `apple` | exact Flutter | Produce, verify, and remove one unsigned simulator app. |
| `compatibility-smoke` | `mobile` | exact Flutter | Execute one reviewed checked-in regular compatibility script. |
| `device-handoff` | `portable` | none | Emit a bounded deferred packet; no device is selected or used. |

The planner always executes on `portable`. It chooses exactly one of the three
explicit execution jobs. Android work cannot consume `apple`; iOS simulator work
cannot consume `mobile` or `portable`; no profile grants physical-device
authority.

## Runtime authority

The selected consumer contract declares exact allowed pin sources. Supported
sources are:

- `.flutter-version`, containing exactly one `x.y.z` value;
- `.fvmrc`, containing exactly `{ "flutter": "x.y.z" }`;
- a contract value, allowed only for the checked-in central smoke fixture.

Every recognized pin file that exists is read as a regular non-symlink file and
must remain within the admitted source root. All values must agree. Channels,
ranges, aliases, whitespace-separated values, malformed JSON, extra FVM keys,
missing pins, symlinks, traversal, mutable refs, and caller-selected runtimes
fail closed.

The immutable setup action is pinned by full SHA in
`contracts/flutter-validation.json`. After setup, the validator parses
`flutter --version --machine` and verifies the exact Flutter version, exact Dart
version, reviewed framework revision, and reviewed engine revision when the
contract provides one. The setup path cannot accept a caller download URL.

## Dependency, Node, and command boundaries

`pubspec.yaml` and committed `pubspec.lock` must be regular files. The validator
fingerprints both pin and package authority before execution, restores with
`flutter pub get --enforce-lockfile`, and rejects any mutation afterward.

Commands are fixed arrays in `contracts/flutter-validation.json`; consumers
cannot append arguments. Checked-in gates are resolved below the source root,
must be regular non-symlink files, and execute with fixed arguments. Finance
Hub's optional web-asset preflight composes the existing bounded Node source
audit contract before its checked-in gate. Flutter code does not implement a
second Node download, npm lockfile parser, public-environment model, or Node
cleanup path.

The current reviewed shapes cover:

- Directus Front: exact `.flutter-version`, canonical `tool/ci_gate.sh`, analysis,
  tests, split-per-ABI arm64 debug APK verification, iOS simulator verification,
  and checked-in compatibility execution;
- Finance Hub: exact `.fvmrc`, repository audit, quality, debug Android APK,
  unsigned iOS simulator compile, and embedded web-asset validation through the
  checked-in quality gate plus bounded Node composition.

## Outputs and cleanup

Android outputs are debug and unsigned only. iOS outputs are simulator and
unsigned only. Outputs are checked for existence and then removed. No APK, AAB,
app bundle, result bundle, log, report, or diagnostic is uploaded.

Validation uses the existing `minimal`, `gradle`, and `apple` workspace
profiles and then creates a Flutter-specific marker-bound subtree. It isolates
`HOME`, `PUB_CACHE`, Flutter state, Gradle state, CocoaPods state, DerivedData,
temporary files, logs, reports, and build output. Real consumer iOS profiles run
`pod install --deployment` before the simulator compile. Cleanup is terminal and
fail closed. It removes Flutter/Dart, Gradle, Pods, DerivedData, coverage, logs,
reports, and build residue; verifies pin and lock hashes; and requires a clean
admitted source. Routine Actions artifacts remain zero.

## Caller example

```yaml
jobs:
  flutter:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-flutter.yml@<full-sha>
    with:
      admitted_sha: ${{ needs.source.outputs.admitted_sha }}
      validation_profile: canonical-gate
      command_profile: directus-canonical
      version_file: .flutter-version
      working_directory: .
      script_path: tool/ci_gate.sh
      platform: flutter
      artifact_exception_id: ""
```

The caller selects reviewed contract data, not infrastructure or commands.
`command_profile` is retained only to preserve the versioned public workflow
surface; it names a checked-in consumer contract and is never interpreted as an
arbitrary command.
