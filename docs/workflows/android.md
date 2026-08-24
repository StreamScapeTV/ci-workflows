# Android validation workflow

`validation.android` version `2.1.0` is the public Android/Gradle validation API implemented by `.github/workflows/reusable-android.yml`. Its stable check name is **CI / Android validation**.

## Build model

One reusable call creates one mobile executor and one registered Gradle workspace. Every job keeps a private writable `GRADLE_USER_HOME`; mobile runners may additionally expose the fixed read-only dependency seed at `/opt/gradle-ro-cache` through `GRADLE_RO_DEP_CACHE`.

Normal `protected-full`, compile, unit, lint, assemble, targeted-unit, script, and ordinary Gradle validation go directly to the caller-owned product operation after source/private-dependency admission. They do **not** run a separate dependency-warm Gradle invocation first. The actual Android executor receives the fixed read-only seed when present, resolves misses through the configured repositories into the job-private Gradle home, and records `gradle_dependency_cache_mode=read-only-seed|cold` in its bounded execution summary.

The optional workflow-call secret `maven_package_read_token` is materialized only on the authoritative product-execution step as the fixed `CIW_MAVEN_PACKAGE_READ_TOKEN` key. The composite Android action does not redeclare a package-token input and does not overwrite that environment key, so GitHub's normal composite-action environment inheritance preserves the execute-step value. The reviewed `ciw` runtime then copies only that fixed key into the bounded child-process environment used by Gradle. Planning, private-dependency checkout/prebuild, evidence, cleanup, residue, cache maintenance/sync, and artifact handling do not receive the package credential, and callers cannot choose another environment-key name or arbitrary environment map. Live-service and unsigned-release use the same inherited fixed-key boundary.

## Protected-full builds once by default

The authoritative `protected-full` pass preserves the caller-owned semantic fields already admitted by `validation_plan_json`: optional `pre_unit_tasks`, optional `compile_tasks`, unit, lint, assemble, and applicable Gradle-backed schema tasks. Central's `validate-android` action fixes the protected-full default to `combined`. The runtime concatenates those exact task identities in the reviewed semantic order and submits them together in one `--no-daemon` Gradle invocation on the same mobile executor, copied source, private Gradle home, and read-only seed. Gradle owns prerequisite deduplication inside that single task graph.

Central does not synthesize product tasks and does not change product-owned Gradle heap, metaspace, worker, parallelism, Kotlin, or test settings. Duplicate task identities across semantic fields fail closed before execution. When Room/schema verification is represented by a checked-in script, the combined Gradle graph finishes first and the script runs afterward in the same copied workspace. When schema verification is Gradle-backed, its tasks participate in the same combined invocation.

The bounded `execution_mode: prefix-isolated` fallback is available only through the existing protected-full plan contract; it adds no workflow or action input. In this mode caller-owned `pre_unit_tasks` are required and run in one fresh isolated Gradle invocation. Caller-owned `compile_tasks`, when present, run in a second fresh isolated invocation. Central then concatenates `unit_tasks + lint_tasks + assemble_tasks + Gradle schema tasks` in semantic order and executes that entire remainder in one Gradle invocation. An absent compile group is skipped rather than creating an empty Gradle process. Script-backed schema verification still runs only after all Gradle work. The executor, checkout, copied workspace, private writable Gradle home, optional read-only seed, package credential boundary, cleanup, and exact-source checks remain the same single-job topology.

The historical grouped #373 experiment remains supported only through explicit `execution_mode: grouped` inside the bounded protected-full plan. That fallback preserves fresh invocations in `pre_unit -> compile -> unit -> lint -> assemble -> schema` order. It is not the routine/default topology. `prefix-isolated` is the narrower fallback when only the caller-declared prefix work needs process isolation while the remainder should retain one shared product graph. Both fallbacks should be selected only after equivalent-source evidence justifies moving away from the combined default. Telemetry records `gradle_execution_mode`, `gradle_invocations`, Gradle wall time, task count, and resource metrics so all modes can be compared without changing source or task semantics.

A caller with a verified private dependency may still provide `dependency_prebuild_plan_json`. Central executes that strict `protected-full` plan before the authoritative application plan on the same mobile executor, private writable Gradle home, read-only seed, and exact private dependency checkout. The prebuild copied caller source is removed and residue-checked before the authoritative execution starts; verified dependency build outputs remain available. The prebuild changes task shape only and does not alter caller-owned Gradle resource settings. Its own explicit grouped or prefix-isolated fallback remains compatible when separately justified by the plan.

## Explicit cache maintenance

Cache maintenance is intentionally separate from normal validation and does not add another public workflow input. Within the existing API, `validation_scope: gradle` with a verified private dependency and no dependency prebuild plan is the bounded cache-maintenance carrier.

That mode performs exactly one Gradle dependency-resolution operation using `actions/warm-gradle-dependencies`. The warm primitive copies the admitted source into disposable registered state and runs the checked-in wrapper with `--no-daemon --write-verification-metadata sha256`. It resolves the real project's resolvable root/subproject, `buildSrc`, included-build, and plugin configurations without selecting application compile/test/lint/assemble tasks. If a verified private dependency subdirectory is present, only that bounded subdirectory is exported through `CI_PRIVATE_DEPENDENCY_PATH`.

Dependency-resolution failure blocks cache maintenance because there is no valid dependency delta to promote. After a successful maintenance warm, Central performs one modules-only promotion through `actions/upload-gradle-seed`. Maintenance then skips product execution, product evidence rendering, optional private-dependency prebuild work, and the later normal-validation sync. Terminal maintenance success requires both the dependency warm and the early promotion to succeed, while cleanup/residue/source-clean checks remain mandatory.

This keeps the cache-maintenance product work to one dependency-resolution Gradle command plus one upload. It does not publish product binaries, compile the Android application, run KSP/tests/lint/assemble/Room work, or execute a second Gradle command merely to finish the maintenance job.

## Shared Gradle dependency cache

Every job has a private writable `GRADLE_USER_HOME`. Central forwards runner-provided `GRADLE_RO_DEP_CACHE` only when it resolves to the fixed `/opt/gradle-ro-cache` directory and cannot alias the writable home. Credentials and unrelated runner environment state are not forwarded.

If the read-only seed is absent, normal validation remains correct: the real build resolves dependencies from configured repositories into its private Gradle home. Cache availability is acceleration only.

Normal validation does not pre-warm. After successful Android execution plus Android cleanup/residue verification, Central performs one best-effort post-execution cache-sync call before registered workspace cleanup. The uploader reads only `GRADLE_USER_HOME/caches/modules-*`, streams bounded content to the fixed internal Flux service, and does not invoke Gradle. This captures misses discovered by the actual product task graph or optional dependency prebuild without paying another configuration/dependency pass before the build.

The normal post-execution sync remains `continue-on-error`, so cache-promotion failure does not overturn a correct product validation. Explicit maintenance is different: its early promotion is the purpose of the job, so terminal maintenance status checks the promotion outcome even though the upload step itself remains `continue-on-error` to preserve deterministic cleanup.

The sync emits only selected file count and total bytes. A concurrent writer returns `gradle_seed_writer_busy`; a writer-side promotion rejection returns `gradle_seed_promotion_rejected`; other non-success HTTP responses become `gradle_seed_upload_rejected`. No dependency paths, payload content, credentials, or arbitrary server body is emitted.

Central owns the cache-maintenance warm/promotion and the normal post-execution sync because it owns the registered Gradle workspace lifecycle. Flux owns the persistent read-only seed and single-writer generation merge. Android product repositories provide only bounded validation/private-dependency data and do not receive cache paths, endpoints, OIDC permissions, shared writable Gradle state, or runner selectors.

Workspace preparation still uses Central `cache_mode: disabled`: this means there is no GitHub Actions cache and no shared writable Gradle home. The runner-owned read-only dependency seed is separate infrastructure.

## Performance telemetry

Normal Android execution reports bounded total/Gradle/script wall time, Gradle invocation count, `gradle_execution_mode`, optional child CPU/cgroup metrics, and `gradle_dependency_cache_mode=read-only-seed|cold`. For the primary protected-full path the expected topology fields are `gradle_execution_mode=combined` and `gradle_invocations=1`.

A controlled fallback run may explicitly report `gradle_execution_mode=grouped` or `gradle_execution_mode=prefix-isolated`; the source SHA, caller task identities, resource profile, cache state, and executor class must otherwise remain equivalent before that result can justify moving away from build-once. Prefix-isolated reports two Gradle invocations when compile is absent and three when compile is present; Gradle-backed schema tasks remain inside the one remainder invocation. A protected-full timing result no longer includes an extra dependency-warm Gradle pass.

Cache maintenance reports only bounded warm wall time and `gradle_dependency_cache_mode`. The modes are therefore measurable without conflating cache construction with product-build duration.

Failed or timed-out reviewed Android operations emit a sanitized bounded diagnostic tail before preserving the stable error code. The dependency-warm primitive does the same for explicit maintenance failures.

## Central helper checkpoints

The reusable workflow uses reviewed immutable Central helper checkpoints:

- `StreamScapeTV/ci-workflows/actions/validate-android@91e5ba5af11ec717f829000edad062c664fb86f7` — `issue #534 prefix-isolated protected-full checkpoint`.
- `StreamScapeTV/ci-workflows/actions/warm-gradle-dependencies@13de46c51efcf65df798dfec82a620c484350dfa` — `issue #346 dependency warm checkpoint`.
- `StreamScapeTV/ci-workflows/actions/upload-gradle-seed@fa67b6a1580ff2eb7386a9e58de09896b9990696` — `issue #346 bounded Gradle cache sync diagnostics checkpoint`.
- `StreamScapeTV/ci-workflows/actions/exact-checkout@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`.
- `StreamScapeTV/ci-workflows/actions/prepare-workspace@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`.
- `StreamScapeTV/ci-workflows/actions/render-evidence@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`.
- `StreamScapeTV/ci-workflows/actions/cleanup-workspace@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`.
- `StreamScapeTV/ci-workflows/actions/checkout-private-dependency@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #104 immutable private-action checkpoint`.

The live-service and unsigned-release reusable workflows remain on their corresponding #443 helper checkpoints because #534 changes only the generic protected-full task-graph composition path. Their fixed package-credential boundary is unchanged.

The checked-in action lock must record the same helper identities before the candidate is merge-state.

## Private dependency and cleanup

When a private dependency is requested, it is checked out once at the exact planned revision. Its checkout token is confined to that step and is not forwarded to Gradle or cache sync.

Explicit cache maintenance creates/removes only its disposable warm source copy. Normal validation may create a dependency-prebuild copy and the authoritative Android copy; each has bounded cleanup/residue checks. Registered workspace cleanup finally removes the private writable Gradle home, dependency checkout, temporary files, and evidence. The runner-owned read-only dependency seed is outside job cleanup and persists for later jobs.

Routine Android validation retains zero GitHub Actions artifacts and does not use `actions/cache`.

## Repository-owned smoke

`.github/workflows/android-validation-smoke.yml` directly exercises the primitive-backed protected-full execution and the dependency-warm helper on mobile capacity. Product-specific performance proof, explicit grouped/prefix-isolated fallback justification, private-dependency prebuilds, and cache-maintenance acceptance remain consumer/integration evidence after Central source and contract tests are green.
