# Android validation workflow

`validation.android` version `2.1.0` is the public Android/Gradle validation API implemented by `.github/workflows/reusable-android.yml`. Its stable check name is **CI / Android validation**.

## Build model

One reusable call creates one mobile executor and one registered Gradle workspace. Every job keeps a private writable `GRADLE_USER_HOME`; mobile runners may additionally expose the fixed read-only dependency seed at `/opt/gradle-ro-cache` through `GRADLE_RO_DEP_CACHE`.

Normal `protected-full`, compile, unit, lint, assemble, targeted-unit, script, and ordinary Gradle validation go directly to the caller-owned product operation after source/private-dependency admission. They do **not** run a separate dependency-warm Gradle invocation first. The actual Android executor receives the fixed read-only seed when present, resolves misses through the configured repositories into the job-private Gradle home, and records `gradle_dependency_cache_mode=read-only-seed|cold` in its bounded execution summary.

The authoritative `protected-full` pass preserves the caller-owned unit, lint, assemble, and applicable Gradle-backed KSP/Room/schema task groups in the same executor/workspace. Optional `pre_unit_tasks` and `compile_tasks` inside the existing bounded `validation_plan_json` remain supported. Each non-empty group runs through its own `--no-daemon` Gradle invocation so task-family class metadata is released between groups. Duplicate tasks across pre-unit, compile, unit, lint, assemble, and Gradle-schema groups fail closed.

A caller with a verified private dependency may provide `dependency_prebuild_plan_json`. Central executes that strict `protected-full` plan before the authoritative application plan on the same mobile executor, private writable Gradle home, read-only seed, and exact private dependency checkout. The prebuild copied caller source is removed and residue-checked before the authoritative execution starts; verified dependency build outputs remain available. The prebuild changes task shape only and does not alter caller-owned Gradle memory, worker, Kotlin, or test settings.

## Explicit cache maintenance

Cache maintenance is intentionally separate from normal validation and does not add another public workflow input. Within the existing API, `validation_scope: gradle` with a verified private dependency and no dependency prebuild plan is the bounded cache-maintenance carrier.

That mode performs exactly one Gradle dependency-resolution operation using `actions/warm-gradle-dependencies`. The warm primitive copies the admitted source into disposable registered state and runs the checked-in wrapper with `--no-daemon --write-verification-metadata sha256`. It resolves the real project's resolvable root/subproject, `buildSrc`, included-build, and plugin configurations without selecting application compile/test/lint/assemble tasks. If a verified private dependency subdirectory is present, only that bounded subdirectory is exported through `CI_PRIVATE_DEPENDENCY_PATH`.

After a successful maintenance warm, Central performs one modules-only promotion through `actions/upload-gradle-seed`. Maintenance then skips product execution, product evidence rendering, optional private-dependency prebuild work, and the later normal-validation sync. Terminal maintenance success requires both the dependency warm and the early promotion to succeed, while cleanup/residue/source-clean checks remain mandatory.

This keeps the cache-maintenance product work to one dependency-resolution Gradle command plus one upload. It does not publish product binaries, compile the Android application, run KSP/tests/lint/assemble/Room work, or execute a second Gradle command merely to finish the maintenance job.

## Shared Gradle dependency cache

Every job has a private writable `GRADLE_USER_HOME`. Central forwards runner-provided `GRADLE_RO_DEP_CACHE` only when it resolves to the fixed `/opt/gradle-ro-cache` directory and cannot alias the writable home. Credentials and unrelated runner environment state are not forwarded.

If the read-only seed is absent, normal validation remains correct: the real build resolves dependencies from configured repositories into its private Gradle home. Cache availability is acceleration only.

Normal validation does not pre-warm. After successful Android execution plus Android cleanup/residue verification, Central performs one best-effort post-execution cache-sync call before registered workspace cleanup. The uploader reads only `GRADLE_USER_HOME/caches/modules-*`, streams bounded content to the fixed internal Flux service, and does not invoke Gradle. This captures misses discovered by the actual product task graph or optional dependency prebuild without paying another configuration/dependency pass before the build.

The normal post-execution sync remains `continue-on-error`, so cache-writer availability cannot overturn a correct product validation. Explicit maintenance is different: its early promotion is the purpose of the job, so terminal maintenance status checks the promotion outcome even though the upload step itself remains `continue-on-error` to preserve deterministic cleanup.

The sync emits only selected file count and total bytes. A concurrent writer returns `gradle_seed_writer_busy`; a writer-side promotion rejection returns `gradle_seed_promotion_rejected`; other non-success HTTP responses become `gradle_seed_upload_rejected`. No dependency paths, payload content, credentials, or arbitrary server body is emitted.

Central owns the cache-maintenance warm/promotion and the normal post-execution sync because it owns the registered Gradle workspace lifecycle. Flux owns the persistent read-only seed and single-writer generation merge. Android product repositories provide only bounded validation/private-dependency data and do not receive cache paths, endpoints, OIDC permissions, shared writable Gradle state, or runner selectors.

Workspace preparation still uses Central `cache_mode: disabled`: this means there is no GitHub Actions cache and no shared writable Gradle home. The runner-owned read-only dependency seed is separate infrastructure.

## Performance telemetry

Normal Android execution reports bounded total/Gradle/script wall time, Gradle invocation count, optional child CPU/cgroup metrics, and `gradle_dependency_cache_mode=read-only-seed|cold`. Those fields describe the actual product execution, so a protected-full timing result no longer includes an extra dependency-warm Gradle pass.

Cache maintenance reports only bounded warm wall time and `gradle_dependency_cache_mode`. The two modes are therefore measurable without conflating cache construction with product-build duration.

Failed or timed-out reviewed Android operations emit a sanitized bounded diagnostic tail before preserving the stable error code. The dependency-warm primitive does the same for explicit maintenance failures.

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

When a private dependency is requested, it is checked out once at the exact planned revision. Its checkout token is confined to that step and is not forwarded to Gradle or cache sync.

Explicit cache maintenance creates/removes only its disposable warm source copy. Normal validation may create a dependency-prebuild copy and the authoritative Android copy; each has bounded cleanup/residue checks. Registered workspace cleanup finally removes the private writable Gradle home, dependency checkout, temporary files, and evidence. The runner-owned read-only dependency seed is outside job cleanup and persists for later jobs.

Routine Android validation retains zero GitHub Actions artifacts and does not use `actions/cache`.

## Repository-owned smoke

The public `ci-workflows` smoke runs its Android planning/contract checks and terminal zero-artifact verification on `ubuntu-latest`. Its real mobile executor is private-context gated and is skipped in this public repository. Product-specific performance proof, private-dependency prebuilds, and cache-maintenance acceptance belong in an appropriate consumer/private execution path after Central source and contract tests are green.
