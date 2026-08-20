# Android validation workflow

`validation.android` version `2.0.0` is the public Android/Gradle validation API implemented by `.github/workflows/reusable-android.yml`. Its stable check name is **CI / Android validation**.

## Build model

One reusable call creates one mobile executor and one Gradle workspace. `protected-full` first resolves the real project's complete Gradle dependency graph into that job's private writable Gradle home, then continues with the caller-owned validation task families. The dependency warm phase uses a disposable copy of the admitted source, the same exact private dependency checkout when present, and Gradle's bounded `--no-daemon --write-verification-metadata sha256` bootstrap operation. That operation resolves all resolvable configurations, including root/subprojects, `buildSrc`, included builds, and plugin configurations, without selecting an application build/test/lint task. The generated verification metadata exists only inside the disposable copy and is removed before product validation.

When the admitted Android plan includes a private dependency subdirectory, Central passes that already-validated bounded subdirectory into the warm primitive. The warm runtime resolves it beneath the exact private-dependency checkout before exporting `CI_PRIVATE_DEPENDENCY_PATH` to Gradle. This keeps composite builds pointed at the same verified build root used by authoritative Android execution instead of accidentally exposing the repository root.

After the warm phase succeeds, Central immediately attempts one best-effort `caches/modules-*` delta sync while the job-private `GRADLE_USER_HOME` is still available. The normal Android validation then proceeds in the same executor/private Gradle home. Dependency-resolution failure blocks the expensive build early; cache-promotion failure does not, because the updater remains acceleration-only and `continue-on-error`.

The authoritative `protected-full` pass preserves the caller-owned unit, lint, assemble, and applicable Gradle-backed KSP/Room/schema task groups in that same executor/workspace, but executes each non-empty group sequentially through its own `--no-daemon` Gradle invocation. A caller may additionally provide optional `pre_unit_tasks` and `compile_tasks` inside the existing bounded `validation_plan_json`. When present, `pre_unit_tasks` runs first and `compile_tasks` runs immediately afterward, before the semantic unit group. This supports caller-owned code generation followed by main compilation in separate short-lived Gradle processes, without changing Gradle memory, worker, Kotlin, or test settings. Duplicate tasks across pre-unit, compile, unit, lint, assemble, and Gradle-schema groups are rejected. Existing plans that omit both optional fields keep the previous unit → lint → assemble → schema ordering; callers that already use only `pre_unit_tasks` keep pre-unit → unit → lint → assemble → schema.

A caller with a verified private dependency may additionally provide `dependency_prebuild_plan_json`. This is another strict `protected-full` Gradle plan executed after dependency warming and before the authoritative validation plan on the same mobile executor, private writable Gradle home, read-only dependency seed, and exact private dependency checkout. Its copied caller source is removed and residue-checked before the authoritative execution starts, while build outputs in the verified private dependency checkout remain available. The protected-full task groups therefore provide bounded process-isolation layers for dependency preparation without changing Gradle memory/worker settings or adding another runner. The prebuild is optional and does not replace authoritative pre-unit/compile/unit/lint/assemble/schema coverage.

The workflow exposes only Android technology inputs: admitted source SHA, validation scope, working directory, Gradle wrapper path, one bounded authoritative validation plan, an optional bounded private-dependency prebuild plan, and optional exact private-dependency coordinates. Dependency warming is Central-owned behavior and adds no caller cache path, cache endpoint, promotion flag, memory/worker setting, runner selector, signing, release, Docker, Helm, or device input.

## Shared Gradle dependency cache

Every job has a private writable `GRADLE_USER_HOME`. On mobile runners, Central additionally forwards the runner-provided `GRADLE_RO_DEP_CACHE` when it points to `/opt/gradle-ro-cache`. Gradle can therefore reuse dependencies already present in the shared cache while writing misses only to the job-private Gradle home.

If the shared cache is absent, the warm phase and later validation resolve dependencies normally from the configured repositories. Cache availability is an acceleration, not a correctness requirement.

For `protected-full`, dependency resolution is deliberately moved to the front of the pipeline. `actions/warm-gradle-dependencies` runs against a disposable exact-source copy and records only bounded cache mode/wall-time evidence. It passes the same filtered `GRADLE_USER_HOME`, fixed read-only seed, Android SDK/JDK state, and verified `CI_PRIVATE_DEPENDENCY_PATH` that the subsequent Android execution uses, while filtering GitHub/private-dependency credentials. If the private checkout declares a verified subdirectory, only that bounded subdirectory is exported to Gradle. The warm source copy is removed before the build continues, so Gradle's generated verification metadata cannot modify the admitted checkout.

Immediately after a successful warm, the same executor attempts a first best-effort dependency-delta sync. That sync reads only `caches/modules-*`, streams bounded content to the fixed internal Flux service, and never runs Gradle itself. If it succeeds, later runners can reuse those modules through the Flux-owned shared read-only seed. The current job also benefits immediately from the same already-populated private Gradle home.

After successful authoritative Android execution plus Android cleanup/residue verification, the same executor performs the existing second best-effort delta sync before workspace cleanup. This captures dependencies that are discovered only during task execution or optional private-dependency prebuild work and were not part of the initial all-resolvable-configuration warm pass. Both sync attempts are `continue-on-error`, so an unavailable updater cannot fail a correct dependency resolution or product build.

The sync emits only the selected private delta file count and total bytes so cache-miss activity is measurable without exposing dependency paths or content. A concurrent writer returns the stable `gradle_seed_writer_busy` code; a writer-side promotion rejection returns `gradle_seed_promotion_rejected`; other non-success HTTP responses become `gradle_seed_upload_rejected`.

Central owns both same-executor sync points because it owns the lifetime of the registered Gradle workspace. Flux owns the internal service and single-writer generation merge; the Android product repository only selects the reviewed Central revision and provides bounded task data rather than implementing cache transport or Gradle orchestration in product YAML.

Routine Android validation requires no GitHub OIDC permission. PR, manual, work-branch, and integration runs use the same cache read/update model. The initial implementation warms automatically only for `protected-full`; consumer repositories may later avoid unnecessary warm work by using dependency-file event filters without changing the Central warm primitive.

Workspace preparation still uses Central `cache_mode: disabled` because Central does not create a GitHub Actions cache or a shared writable Gradle home. The shared read-only dependency cache is runner infrastructure and is independent from the private job workspace.

## Performance telemetry

The dependency warm phase reports `gradle_dependency_cache_mode=read-only-seed|cold` and bounded warm wall time before the product build begins. Terminal logging projects only those two fields. This makes the dependency bootstrap cost separately observable from Media preparation and application compilation.

Authoritative Android execution records bounded wall times for the full execute function, the aggregate of all Gradle task-group invocations, and any checked-in script phase. `test_summary.gradle_invocations` reports the actual number of non-empty authoritative Gradle groups, including optional `pre_unit_tasks` and `compile_tasks` when requested. On supported Linux runners it also records sampled cgroup peak memory/process count and child CPU time. `test_summary` reports `gradle_dependency_cache_mode` as `read-only-seed` or `cold`, allowing cold/warm measurements without another monitoring job. When the optional dependency prebuild is enabled, its action outcome remains separately visible in the same job while end-to-end workflow duration captures its cost; the public `test_summary` continues to describe the authoritative validation pass.

Failed or timed-out reviewed Android operations emit only a sanitized bounded diagnostic tail before preserving the existing stable error code. The dependency-warm primitive likewise emits only a sanitized bounded diagnostic tail on failure and never prints dependency paths, credentials, or arbitrary environment content.

## Central helper checkpoints

The reusable workflow uses reviewed immutable Central helper checkpoints:

- `StreamScapeTV/ci-workflows/actions/validate-android@8eaa37ad0fe3231b202e878b26f66aa23753e38a` — `issue #373 compile Gradle isolation checkpoint`.
- `StreamScapeTV/ci-workflows/actions/warm-gradle-dependencies@13de46c51efcf65df798dfec82a620c484350dfa` — `issue #346 dependency warm checkpoint`.
- `StreamScapeTV/ci-workflows/actions/upload-gradle-seed@fa67b6a1580ff2eb7386a9e58de09896b9990696` — `issue #346 bounded Gradle cache sync diagnostics checkpoint`.
- `StreamScapeTV/ci-workflows/actions/exact-checkout@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`.
- `StreamScapeTV/ci-workflows/actions/prepare-workspace@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`.
- `StreamScapeTV/ci-workflows/actions/render-evidence@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`.
- `StreamScapeTV/ci-workflows/actions/cleanup-workspace@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`.
- `StreamScapeTV/ci-workflows/actions/checkout-private-dependency@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #104 immutable private-action checkpoint`.

The checked-in action lock must record the same helper identities before the candidate is merge-state.

## Private dependency and cleanup

When a private dependency is requested, it is checked out once at the exact planned revision before dependency warming and only its verified build subpath is passed into the warm/build runtimes. The dependency token is confined to that checkout step and is not forwarded to Gradle or either cache sync.

The warm primitive creates and removes its own disposable source copy. When `dependency_prebuild_plan_json` is present, Central then executes it through the immutable grouped Android primitive, removes that pass's copied caller source, and verifies zero Android residue. Terminal success requires dependency warming plus every requested prebuild plan/execute/cleanup/residue phase to succeed. The authoritative Android copied-source cleanup and residue verification then run after product validation. Registered workspace cleanup finally removes the private writable Gradle home, dependency state, temporary files, and evidence. A final source check verifies the admitted checkout remained exact and clean.

Routine Android validation retains zero GitHub Actions artifacts and does not use `actions/cache`.

## Repository-owned smoke

`.github/workflows/android-validation-smoke.yml` exercises one real synthetic `protected-full` validation on mobile capacity and verifies the dependency warm phase, expected Android/JDK toolchain, ordered Gradle task-group execution, and cleanup. Application performance proof and any product-specific dependency prebuild or optional pre-unit/compile plan belong in the Android consumer repository after Central source/contract checks are green.