# Android validation workflow

`validation.android` version `1.0.0` is the public, product-neutral Android and Gradle validation API implemented by `.github/workflows/reusable-android.yml`. Its stable required-check name is **CI / Android validation**.

## Invocation boundary

A caller supplies an exact admitted source SHA and chooses only checked-in identifiers: a validation profile, repository task profile, reviewed working directory, reviewed Gradle wrapper path, and the profile-specific bounded fields. The API accepts no arbitrary Gradle task, command, arguments, shell, callback, runtime download URL, runner label, engine, registry, signing identity, keystore, store operation, database or backend URL, device selector, mutable ref, release, deployment, Helm, or Flux input.

Planning runs on protected semantic `portable` capacity and resolves the checked-in contract. Android execution runs only on the semantic `mobile` profile selected by the protected planner. Callers never select labels, hosts, architectures, or engines. Same-repository trusted PRs and exact trusted source are admitted; fork source is rejected before mobile or private dependency execution.

Private same-organization callers do not provide a credential for `StreamScapeTV/ci-workflows`, and the reusable workflow does not clone the private central repository with the caller-scoped Actions token. Instead it invokes reviewed central composite actions directly through immutable full-SHA references. `validate-android` is pinned to `8c7e16003b56da9f6bd2e20b0b5b78e4bbfaceaf`, recorded as the `issue #262 immutable Media audio-output sentinel checkpoint`. Exact checkout, workspace preparation, evidence rendering, and cleanup remain pinned to `70e08d4ddf8930046632a7135950e924b82e22bf`, recorded as `issue #116 immutable private-action checkpoint`; `checkout-private-dependency` currently uses the same SHA but retains its separate `issue #104 immutable private-action checkpoint` lock identity. The caller cannot select any of those revisions, and no `central_source`, generic checkout token, PAT, mutable helper ref, or `secrets: inherit` surface exists.

The current bounded profiles are:

- `toolchain-smoke`: verifies JDK/Javac 25, Android API 37, build-tools 37, the exact SDK package inventory, locale, isolated state, and the reviewed Gradle distribution, then executes one fixed synthetic Gradle verification task without claiming a product build;
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

The optional `private_dependency_token` is not a central-source credential. When required, it is passed only to the exact `checkout-private-dependency` primitive selected by the protected plan. It is never passed to Android planning, caller-source checkout, Android execution, evidence rendering, or cleanup. Fork source cannot request the dependency, and ordinary toolchain smoke uses no private token.

## Toolchain and Gradle execution

The contract requires JDK and Javac major 25, Android platform API 37, command-line tools revision `22.0`, `platform-tools`, `platforms;android-37`, and `build-tools;37.0.0`. The runner must already provide those identities; this workflow does not silently downgrade them. The installed-package listing may report the reviewed equivalent `platforms;android-37.0`, but validation still requires the canonical on-disk `platforms/android-37/android.jar` path.

Every Gradle-capable profile verifies three explicit contract-owned wrapper components relative to the reviewed working directory: the executable launcher, wrapper properties, and, for a standard Gradle wrapper, the wrapper JAR. The launcher, properties, and JAR each have their own path and Git-blob identity; a JAR digest is never compared to the launcher. Verification also requires the exact distribution URL, the declared distribution checksum when available, and the expected Gradle version. It invokes only the reviewed launcher with fixed `--no-daemon --console=plain --warning-mode=all --stacktrace` arguments. A targeted selector is appended only after strict grammar validation. Caller property injection, init scripts, project/system properties, arbitrary tasks, and caller Gradle state paths are rejected.

The central synthetic fixture is the only wrapper mode that installs its own Gradle runtime. Its launcher contains one fixed official HTTPS URL for Gradle `9.6.1`, verifies SHA-256 `9c0f7faeeb306cb14e4279a3e084ca6b596894089a0638e68a07c945a32c9e14` before extraction, rejects traversal, duplicate destinations, unsupported archive members, excessive members, and excessive expanded size, and installs only beneath isolated `GRADLE_USER_HOME`. It then executes the fixed `:verifyToolchainSmoke` task, which rechecks JDK 25 plus the API-37 and build-tools-37 files through real Gradle execution. The archive, installation, generated state, and logs are deleted by the normal Android cleanup contract.

Execution uses isolated `HOME`, `TMPDIR`, `GRADLE_USER_HOME`, Android user state, logs, caches, and temporary dependency state beneath the marker-bound workflow workspace. The same isolated paths apply to Java, Javac, SDK-manager, wrapper, and task execution; inherited host home or temporary paths are not used. Strict `C.UTF-8` locale and UTC are applied. Protected Gradle configuration, lock, version-catalog, wrapper, schema, and consumer-script paths are hashed before execution and reverified after it. Tracked mutation and unexpected untracked source fail closed.

## Repository source-policy projection

The shared repository policy remains the default authority for tracked-file, token-shaped content, generated-output, clean-tree, path, and artifact checks. Android adds only the reviewed projection in `contracts/android-source-policy.json`; it does not disable or weaken the shared scanner.

A synthetic protocol or redaction marker is accepted only when all reviewed fields match: exact repository, exact Android validation profile, exact repository-relative path, exact rule, and exact Git blob digest. There is no extension-wide or directory-wide exception. The reviewed Streamscape Media playback-lab bootstrap and lifecycle sentinels are each exact path/blob bindings within one playback-lab exception, while the guided-acceptance and audio-output sentinels remain separate named, digest-bound entries. A content change, path move, profile mismatch, repository mismatch, or real credential-shaped replacement therefore fails closed.

Policy failures are classified before entering the public facade. `dirty_tree` is reserved for a real tracked or untracked worktree mutation. Separate codes cover tracked secret-shaped content, forbidden tracked files, symlink or path escape, generated-output drift, artifact policy, and policy-contract failure. Diagnostics contain only a stable rule ID plus one normalized repository-relative path, contract-relative path, or SHA-256 status digest. File content, credential text, absolute host paths, and broad scan output are never projected.

## Outputs and artifacts

The reusable workflow emits bounded outputs for result, exact source SHA, selected profiles, test summary, resolved Java/API/Gradle identities, private-dependency use, unsigned-debug and schema verification, device-handoff JSON, clean-tree state, cleanup result, and deterministic evidence identity.

Routine runs retain zero GitHub Actions artifacts. A diagnostic artifact can be requested only through the reviewed name `android-redacted-diagnostics-v1`; it is bounded, excludes APK/AAB/keystore material, is absent by default, and must use the central action lock and artifact policy. APK, AAB, build output, reports, logs, caches, Gradle state, temporary Media source, generated schemas, Android state, SDK probes, and the synthetic Gradle distribution are always removed after verification.

## Cleanup and failure projection

Android-specific cleanup and marker-bound workspace cleanup run under `if: always()`. Android cleanup removes copied source, build output, Gradle/Android state, logs, SDK probes, and synthetic Gradle download/install state; the registered workspace cleanup separately removes the exact private dependency checkout, Git state, and credentials. Neither cleanup accepts a deletion path or follows symlinks, and any process, output, cache, or path residue fails closed without hiding the original validation failure. A final terminal step fails the workflow unless execution, Android cleanup, residue verification, and workspace cleanup all succeed.

Failures are projected through stable contract codes such as `toolchain_mismatch`, `sdk_package_missing`, `wrapper_distribution_drift`, `test_filter_rejected`, `private_dependency_rejected`, `compile_failed`, `tests_failed`, `lint_failed`, `schema_drift`, `dirty_tree`, `tracked_secret_detected`, `forbidden_tracked_file`, `symlink_path_escape`, `generated_output_drift`, `artifact_policy_failed`, `policy_contract_failed`, and `cleanup_failed`. Stored diagnostic output is bounded and redacts credentials and credential-bearing URLs.

## Repository-owned smoke

`.github/workflows/android-validation-smoke.yml` stages the exact local Android action on same-repository pull-request heads. Its protected portable planner resolves the synthetic `central-toolchain-smoke` fixture, and the dependent execution job uses the resulting semantic `mobile` selector. The mobile job performs real checksum-pinned Gradle distribution installation and `:verifyToolchainSmoke` execution against the synthetic project, then proves registered-state cleanup, zero residue, and zero Actions artifacts. The smoke certifies only the fixed Android toolchain, wrapper/distribution contract, workflow wiring, and cleanup; it is not product certification and does not produce physical-device evidence.
