# Android validation workflow

`validation.android` version `2.0.0` is the public product-neutral Android/Gradle validation API implemented by `.github/workflows/reusable-android.yml`. Its stable required-check name is **CI / Android validation**.

## Public technology boundary

The reusable workflow accepts only technology-level caller data: an exact admitted source SHA, a validation scope, a bounded working directory, a checked-in Gradle wrapper path, a bounded JSON list of Gradle tasks, an optional exact targeted-test selector, or a checked-in executable script plus bounded argv values. Optional private dependency coordinates are an exact repository, full SHA, bounded identity, and bounded subdirectory. There is no central product ID, repository-specific task registry, runner-label input, shell string, callback, container engine, registry host, signing identity, keystore, release, Helm, Flux, or physical-device input.

Supported validation scopes are `compile`, `unit`, `assemble`, `lint`, `targeted-test`, `gradle`, and `script`. The first four accept exactly one caller-owned Gradle task. `targeted-test` adds one grammar-bounded JVM selector. `gradle` accepts up to 32 validated Gradle task names. `script` executes only one bounded checked-in executable path with at most 64 single-line argv values; it never accepts a shell command string.

The execution job runs directly on semantic `mobile` capacity (`[linux, amd64, mobile]`). Callers cannot select runner labels or hosts. The runner-provided Android SDK is required and Java is resolved and validated as JDK major 25 before Android work executes.

## Immutable central helpers

Private callers do not clone the central repository with caller-scoped credentials. The workflow invokes central composite actions at immutable full commit SHAs:

- `StreamScapeTV/ci-workflows/actions/validate-android` is pinned to `0b1be616b4a03891b6b31918001320f09726ed93`, recorded as `issue #332 primitive-backed Android checkpoint`.
- `StreamScapeTV/ci-workflows/actions/exact-checkout`, `prepare-workspace`, `render-evidence`, and `cleanup-workspace` remain pinned to `70e08d4ddf8930046632a7135950e924b82e22bf`, recorded as `issue #116 immutable private-action checkpoint`.
- `StreamScapeTV/ci-workflows/actions/checkout-private-dependency` is pinned to `70e08d4ddf8930046632a7135950e924b82e22bf`, recorded as `issue #104 immutable private-action checkpoint`.

The caller cannot select any helper revision, central-source token, mutable helper ref, or `secrets: inherit` surface.

## Exact source and isolated execution

The admitted caller SHA is checked out exactly into `source`. Workspace preparation uses the `gradle` profile with cache mode `disabled`; no GitHub Actions cache is used. Before execution, the adapter revalidates the exact source SHA and clean tree, then copies the admitted source through the symlink-rejecting Android copy primitive into marker-bound workflow state. Gradle, checked-in scripts, generated code, Room/schema verification, and build output therefore operate on the isolated copy rather than dirtying the admitted checkout.

Only a fixed non-secret runtime environment is forwarded to product execution: isolated `HOME`, `TMPDIR`, `GRADLE_USER_HOME`, runner `PATH`, JDK/Android SDK locations, locale, UTC, and an optional verified private-dependency path. GitHub tokens, private dependency checkout tokens, arbitrary caller environment, workflow metadata, and secret-bearing variables are not forwarded. Gradle always receives `--no-daemon`.

The checked-in script mode is the repository-owned escape hatch for technology-specific verification such as Room/schema integrity. The central workflow owns only path/argv validation and execution mechanics; the consumer repository owns the script content and assertions.

## Exact private dependency boundary

Private dependency coordinates are all-or-none. When requested, the reusable workflow passes `private_dependency_token` only to the immutable `checkout-private-dependency` action. That action checks out the exact SHA into registered workspace state, detaches HEAD, erases remotes and credential-bearing Git configuration, verifies the bounded expected subdirectory, and writes only the verified dependency path to subsequent execution state.

The Android adapter refuses the dependency unless the checkout reports matching repository, dependency identity, exact head SHA, expected subdirectory, verified state, erased remotes, and erased credentials. Product execution receives only the verified subdirectory path. The checkout token never reaches planning, caller-source checkout, Gradle/script execution, evidence rendering, or cleanup.

## Outputs, cleanup, and artifacts

The public workflow exposes `result`, bounded `test_summary`, and `cleanup_result`. The summary contains only technology-level status, scope, task count, JDK major, and whether an exact private dependency was used. Raw command stdout/stderr, environment values, tokens, host paths, and application identifiers are not public outputs.

Android-specific cleanup runs after execution under `if: always()` whenever workspace preparation succeeded. It removes only the known marker-bound copied-source path, then a residue check proves that path is absent. Registered workspace cleanup runs under `if: always()` and removes private dependency state, credentials, Gradle state, temporary files, and evidence. A final exact-source check proves the admitted checkout SHA and worktree remained unchanged. Terminal projection fails the job unless execution and every applicable cleanup/residue check succeed.

Routine runs retain zero GitHub Actions artifacts. There is no `actions/cache`, artifact upload, APK/AAB retention, signing, publication, ADB, emulator, or physical-device authority in this workflow.

## Repository-owned smoke

`.github/workflows/android-validation-smoke.yml` calls the reusable workflow itself on pull requests that change the Android reusable surface. It uses the checked-in synthetic Gradle project under `tests/fixtures/android-validation/smoke-project` and executes its `verifyToolchainSmoke` task through the public `gradle` scope. The fixture performs real Gradle execution and verifies JDK 25, Android API 37, and Android Build Tools 37. The smoke proves reusable-workflow wiring, semantic mobile capacity, exact source handling, and terminal cleanup; it is not application or physical-device certification.
