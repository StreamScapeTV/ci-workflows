# Android validation workflow

`validation.android` version `1.0.0` is the public, product-neutral Android and Gradle validation API implemented by `.github/workflows/reusable-android.yml`. Its stable required-check name is **CI / Android validation**.

## Invocation boundary

A caller supplies an exact admitted source SHA and chooses only checked-in identifiers: a validation profile, repository task profile, reviewed working directory, reviewed Gradle wrapper path, and the profile-specific bounded fields. The API accepts no arbitrary Gradle task, command, arguments, shell, callback, runtime download URL, runner label, engine, registry, signing identity, keystore, store operation, database or backend URL, device selector, mutable ref, release, deployment, Helm, or Flux input.

Planning runs on protected semantic `portable` capacity and resolves the checked-in contract. Android execution runs only on the semantic `mobile` profile selected by the protected planner. Callers never select labels, hosts, architectures, or engines. Same-repository trusted PRs and exact trusted source are admitted; fork source is rejected before mobile or private dependency execution.

The current bounded profiles are:

- `toolchain-smoke`: verifies JDK/Javac 25, Android API 37, build-tools 37, the exact SDK package inventory, locale, isolated state, and the reviewed Gradle distribution without claiming a product build;
- `compile`: runs one reviewed compile task profile;
- `unit-targeted`: runs one reviewed JVM unit task with one exact selector matching the checked-in grammar;
- `unit-full`: runs the reviewed full JVM unit task set;
- `performance`: runs only checked-in deterministic performance tests;
- `lint`: runs reviewed Android lint tasks;
- `assemble-debug`: builds unsigned debug output, validates its shape, rejects AAB/release/signed output, and removes it;
- `room-schema`: runs reviewed Room schema generation/integrity commands and rejects committed Room history drift;
- `consumer-script`: invokes one checked-in script profile with fixed contract-owned arguments;
- `device-handoff`: emits only a bounded request packet for a separate device workflow. It never runs ADB, acquires a device, starts an emulator, uses a live backend, or claims physical acceptance.

Product commands and product assertions remain in the consumer repositories and are represented only by reviewed task data in `contracts/android-validation.json`. Shared orchestration contains no repository-name branches.

## Exact private dependency

Current IPTV Android composite builds may use the named contract `streamscape-media-android-v1`. The reusable workflow composes the merged `checkout-private-dependency` primitive. The contract binds the exact `StreamScapeTV/streamscape-media` repository, an approved full lowercase SHA, expected `android` subdirectory, expected Gradle paths, detached checkout, credential-free Git state, erased remotes, and complete registered-state cleanup. There is no generic private repository input and no checkout or authentication implementation in Android workflow YAML.

The token, when required, is passed only to the exact dependency primitive. Fork source cannot request the dependency, and ordinary toolchain smoke uses no private token.

## Toolchain and Gradle execution

The contract requires JDK and Javac major 25, Android platform API 37, command-line tools build `14742923` / revision `19.0`, `platform-tools`, `platforms;android-37.0`, and `build-tools;37.0.0`. The runner must already provide those identities; this workflow does not silently downgrade them.

Every Gradle-capable profile verifies the checked-in wrapper or launcher, the exact distribution URL, the declared distribution checksum when available, and the expected Gradle version. It invokes only the reviewed wrapper with fixed `--no-daemon --console=plain --warning-mode=all --stacktrace` arguments. A targeted selector is appended only after strict grammar validation. Caller property injection, init scripts, project/system properties, arbitrary tasks, and caller Gradle state paths are rejected.

Execution uses isolated `HOME`, `TMPDIR`, `GRADLE_USER_HOME`, Android user state, logs, caches, and temporary dependency state beneath the marker-bound workflow workspace. Strict `C.UTF-8` locale and UTC are applied. Protected Gradle configuration, lock, version-catalog, wrapper, schema, and consumer-script paths are hashed before execution and reverified after it. Tracked mutation and unexpected untracked source fail closed.

## Outputs and artifacts

The reusable workflow emits bounded outputs for result, exact source SHA, selected profiles, test summary, resolved Java/API/Gradle identities, private-dependency use, unsigned-debug and schema verification, device-handoff JSON, clean-tree state, cleanup result, and deterministic evidence identity.

Routine runs retain zero GitHub Actions artifacts. A diagnostic artifact can be requested only through the reviewed name `android-redacted-diagnostics-v1`; it is bounded, excludes APK/AAB/keystore material, is absent by default, and must use the central action lock and artifact policy. APK, AAB, build output, reports, logs, caches, Gradle state, temporary Media source, generated schemas, Android state, and SDK probes are always removed after verification.

## Cleanup and failure projection

Android-specific cleanup and marker-bound workspace cleanup run under `if: always()`. Android cleanup removes copied source, build output, Gradle/Android state, logs, and SDK probes; the registered workspace cleanup separately removes the exact private dependency checkout, Git state, and credentials. Neither cleanup accepts a deletion path or follows symlinks, and any process, output, cache, or path residue fails closed without hiding the original validation failure. A final terminal step fails the workflow unless execution, Android cleanup, residue verification, and workspace cleanup all succeed.

Failures are projected through stable contract codes such as `toolchain_mismatch`, `sdk_package_missing`, `wrapper_distribution_drift`, `test_filter_rejected`, `private_dependency_rejected`, `compile_failed`, `tests_failed`, `lint_failed`, `schema_drift`, `dirty_tree`, `artifact_policy_failed`, and `cleanup_failed`. Stored diagnostic output is bounded and redacts credentials and credential-bearing URLs.

## Repository-owned smoke

`.github/workflows/android-validation-smoke.yml` calls the exact local reusable workflow on same-repository pull-request heads with the synthetic `central-toolchain-smoke` fixture. The smoke uses organization-managed semantic `mobile` capacity and proves only the fixed Android toolchain, wrapper contract, workflow wiring, cleanup, residue, and zero-artifact behavior. It is not product certification and does not produce physical-device evidence.
