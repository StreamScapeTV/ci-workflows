# Android validation architecture

## Purpose

Central Android validation provides one product-neutral Gradle execution boundary for Android consumers. The performance model is deliberately simple: one exact source checkout, one mobile executor, one registered workspace, one private writable Gradle home, one optional exact private dependency checkout, and an optional runner-provided read-only dependency cache.

Normal validation consumes the shared dependency seed directly in the real product Gradle execution. A separate explicit cache-maintenance path owns dependency-only warming and seed promotion. Normal protected-full therefore does not pay a second Gradle configuration/dependency-resolution pass before doing useful product work.

For `protected-full`, Central keeps caller-owned Gradle task identities inside one bounded validation plan and one executor/workspace boundary. `combined` remains the default and submits the complete admitted task set as one Gradle graph. A caller that must isolate a required pre-unit prefix may explicitly select `prefix-isolated`: required `pre_unit_tasks` run first, optional `compile_tasks` run in a second isolated invocation when present, and unit + lint + assemble + Gradle-backed schema tasks run together in one remainder invocation. Checked-in script schema still runs afterward in the same copied workspace. Central does not invent, remove, rename, or reorder a product task identity.

## Layers

1. `contracts/android-validation.json` defines the Android validation contract.
2. `src/ci_workflows/android_contract.py` validates the request and resolves a bounded plan.
3. `src/ci_workflows/android_execution.py` verifies toolchain/wrapper state, copies source, runs commands, and checks mutation/output rules.
4. `src/ci_workflows/ciw_android.py` adapts plan/execute/cleanup/residue phases, selects combined, prefix-isolated, or grouped protected-full execution, and forwards the fixed runner-provided Gradle read-only cache when present.
5. `src/ci_workflows/gradle_dependency_warm.py` performs the product-neutral dependency-only bootstrap used only by cache maintenance.
6. `src/ci_workflows/android_resource_metrics.py` measures bounded same-executor wall/CPU/cgroup evidence for authoritative Android execution.
7. `actions/validate-android/action.yml` is the thin Android validation adapter and fixes the Central protected-full default to `combined`.
8. `actions/warm-gradle-dependencies/action.yml` is the thin dependency-only Gradle warm adapter.
9. `actions/upload-gradle-seed/action.yml` is the thin internal dependency-delta sync adapter.
10. `.github/workflows/reusable-android.yml` composes the single mobile job, optional private-dependency prebuild, normal product execution, cache maintenance, cleanup, and terminal projection.

Routine validation has no OIDC dependency and no GitHub Actions cache. The shared dependency cache is read-only to Gradle; each job retains its own private writable home.

## Normal validation path

After source admission, one exact source checkout, one registered Gradle workspace, and optional exact private dependency checkout, normal `protected-full`, compile, unit, lint, assemble, targeted-unit, script, and ordinary Gradle scopes go directly to their requested product operation. They do not run `warm-gradle-dependencies` first.

The Android execution runtime forwards `GRADLE_RO_DEP_CACHE=/opt/gradle-ro-cache` only when that runner-owned directory is present and valid. It cannot alias the private `GRADLE_USER_HOME`. If the seed is present, Gradle consumes cached modules from it and writes misses to the private home. If the seed is absent, the same real product build resolves dependencies from configured repositories into its private home. Cache presence changes acceleration, not correctness.

The optional package-read secret crosses only the authoritative execution boundary. The reusable workflow maps it to the fixed `CIW_MAVEN_PACKAGE_READ_TOKEN` environment key on the execute action. The composite action intentionally exposes no package-token input and defines no same-named environment assignment, so the execute-step environment is inherited by its child shell rather than shadowed. The reviewed `ciw` runtime then copies only that fixed key into the child-process environment used by Gradle. It is not accepted as a generic environment-map input and is not forwarded to planning, private-dependency checkout/prebuild, evidence, cleanup, residue, cache maintenance, cache sync, or unrelated helper execution. The live-service and unsigned-release action/runtime pairs apply the same inherited fixed-key boundary.

### Protected-full execution modes

`protected-full` remains inside one mobile executor and one registered workspace. Caller-owned `pre_unit_tasks`, `compile_tasks`, `unit_tasks`, `lint_tasks`, `assemble_tasks`, and Gradle-backed schema tasks remain bounded fields inside `validation_plan_json`. Duplicate task identities across pre-unit/compile/unit/lint/assemble/Gradle-schema groups fail closed before execution.

`combined` is the Central default. Every non-empty Gradle task list is concatenated in the reviewed semantic order and submitted in one `--no-daemon` Gradle invocation. A Gradle-backed schema task participates in that same combined graph. A checked-in schema script is outside Gradle and runs afterward in the same copied source.

`prefix-isolated` is an explicit bounded execution mode for callers whose required pre-unit work must complete before the rest of the graph. It requires a non-empty caller-owned `pre_unit_tasks` group. That group runs in one isolated Gradle invocation. If caller-owned `compile_tasks` are present, they run in one subsequent isolated invocation; if absent, that invocation is omitted. Unit, lint, assemble, and Gradle-backed schema tasks are then concatenated in their reviewed order and submitted together as one remainder Gradle invocation. A checked-in schema script still runs only after the Gradle remainder. The mode changes only process boundaries; it adds no workflow input, product task, runner/resource policy, cache, credential path, artifact bridge, or workspace.

The preserved grouped experiment remains available only as an explicit compatibility/fallback mode: a bounded plan may set `execution_mode: grouped`. That mode retains the fresh invocation order `pre_unit -> compile -> unit -> lint -> assemble -> schema` and the existing family markers. It is not the Central default and is not selected implicitly by `prefix-isolated`.

For large private dependency graphs, callers may still provide `dependency_prebuild_plan_json`. It is validated and executed before the authoritative application plan using the same mobile executor, private writable Gradle home, read-only seed, and exact private dependency checkout. Its copied caller source is removed and residue-checked before authoritative execution starts, while verified dependency build outputs remain available. No preliminary dependency-warm pass is required for this prebuild. An explicit grouped execution override inside such a prebuild plan remains a compatibility mechanism rather than the normal authoritative topology.

## Explicit cache maintenance

The existing public API identifies cache maintenance without adding a new caller-selected infrastructure flag: `validation_scope: gradle` plus a verified private dependency and no dependency-prebuild plan enters maintenance mode.

Maintenance creates a disposable exact-source copy and invokes `actions/warm-gradle-dependencies` once. The helper runs the checked-in wrapper with the fixed `--no-daemon --write-verification-metadata sha256` bootstrap operation, resolving the real project's resolvable root/subproject, `buildSrc`, included-build, and plugin configurations. It does not select application compile, test, lint, KSP, CMake, assemble, or Room work. Any generated verification metadata remains in the disposable copy and is removed before terminal cleanup.

If a verified private dependency subdirectory is present, only the plan-validated bounded subdirectory is exposed through `CI_PRIVATE_DEPENDENCY_PATH`. GitHub/private-dependency credentials and unrelated host state are filtered from the Gradle runtime.

A successful maintenance warm immediately invokes the modules-only uploader once. The upload step remains `continue-on-error` so deterministic cleanup always runs, but terminal maintenance success requires the upload outcome itself to be successful. Maintenance skips private-dependency prebuild execution, product validation, product evidence rendering, and the later normal-validation cache sync. The maintenance product work is therefore one dependency-resolution Gradle command plus one promotion.

## Shared dependency cache

Each job receives a private writable `GRADLE_USER_HOME`. The warm and Android runtimes may additionally forward the fixed runner-owned `GRADLE_RO_DEP_CACHE=/opt/gradle-ro-cache` when it exists.

Normal validation resolves misses during the real task graph. After successful authoritative Android execution, copied-source cleanup, and residue verification, one best-effort post-execution cache-sync call runs before registered workspace cleanup. The sync client reads only `GRADLE_USER_HOME/caches/modules-*`, uses the bounded framing/filtering contract, and streams that delta to the fixed cluster-local Flux service. It does not invoke Gradle.

The normal post-execution sync is acceleration-only and remains `continue-on-error`: a missing cache, empty delta, unavailable internal service, or rejected promotion cannot overturn a correct product build. Explicit maintenance is the only path where promotion success is itself a terminal requirement.

Central owns the maintenance promotion and normal post-execution sync because it owns the registered workspace lifecycle. Flux owns the internal ClusterIP writer and the single-writer generation merge. Android product callers never gain a cache endpoint, token, OIDC permission, memory override, or shared writable Gradle home.

For bounded diagnosis, the sync client reports only selected dependency-delta file count and total bytes. HTTP `409` maps to `gradle_seed_writer_busy`, HTTP `422` to `gradle_seed_promotion_rejected`, and other non-success responses to `gradle_seed_upload_rejected`. Dependency paths, payloads, endpoints, tokens, headers, credentials, and arbitrary server bodies are not logged.

## Performance evidence

Normal Android execution reports:

- total execute wall time;
- aggregate Gradle wall time;
- actual Gradle invocation count;
- `gradle_execution_mode=combined|prefix-isolated|grouped|single|not-applicable`;
- optional checked-in script wall time;
- child CPU time when available;
- sampled cgroup peak memory/process count when available;
- `gradle_dependency_cache_mode=read-only-seed|cold`.

For the default protected-full topology, the primary signal remains `gradle_execution_mode=combined` with `gradle_invocations=1`. An explicit `prefix-isolated` plan reports one pre-unit invocation, zero or one compile invocation, and one remainder invocation, so the expected count is two or three without introducing any extra workspace or dependency-warm process. Explicit `grouped` remains the compatibility fallback. Because normal validation has no separate dependency-warm Gradle invocation, protected-full wall time measures the requested validation path rather than useful work plus a redundant warm pre-pass.

Explicit maintenance reports bounded `gradle_dependency_cache_mode=read-only-seed|cold` and warm wall time. Maintenance and product-build measurements therefore remain distinct.

Failed dependency-warm or Android operations emit sanitized bounded diagnostic tails before preserving stable failure codes. Successful operations retain bounded summaries only.

## Helper checkpoints

`reusable-android.yml` composes reviewed Central helpers by immutable source identity:

- `validate-android@91e5ba5af11ec717f829000edad062c664fb86f7` — `issue #534 prefix-isolated protected-full checkpoint`;
- `warm-gradle-dependencies@13de46c51efcf65df798dfec82a620c484350dfa` — `issue #346 dependency warm checkpoint`;
- `upload-gradle-seed@fa67b6a1580ff2eb7386a9e58de09896b9990696` — `issue #346 bounded Gradle cache sync diagnostics checkpoint`;
- `exact-checkout@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`;
- `prepare-workspace@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`;
- `render-evidence@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`;
- `cleanup-workspace@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`;
- `checkout-private-dependency@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #104 immutable private-action checkpoint`.

`reusable-android-live-service.yml` and `reusable-android-release.yml` remain on their existing #443 action checkpoints because #534 changes only the generic protected-full task-process composition path. Their package-credential boundary is unchanged.

The action lock must record the same helper identities before the candidate is merge-state.

## Runtime state and cleanup

The admitted checkout remains verification source. Normal validation creates only the copies required by an optional dependency prebuild and/or authoritative product execution. Explicit cache maintenance creates its own disposable warm copy instead and skips product execution.

The private dependency, when requested, is checked out once to registered state. Its checkout credential does not reach the warm helper, product validation, or cache-sync client.

All applicable copied-source cleanup and residue checks remain mandatory. Registered workspace cleanup always removes the private writable Gradle home, dependency checkout, temporary files, and evidence. The runner-owned read-only dependency cache is outside job cleanup and persists for later jobs.

Normal terminal success requires plan/source/workspace/dependency admission, all requested prebuild/product execution, Android cleanup/residue, workspace cleanup, and source-clean verification. Cache promotion is not a correctness condition for normal validation. Maintenance terminal success instead requires the one dependency warm, one maintenance promotion, and the same cleanup/source-clean boundaries.

## Performance ownership

Central owns the combined default, the explicit prefix-isolated and grouped process-boundary modes, cache-maintenance warming/promotion, one-executor boundary, the read-only dependency-cache handoff, normal post-execution best-effort miss sync, and timing evidence. Android product repositories own Gradle/test resource settings and exact task selection. Product callers select `prefix-isolated` only when their bounded plan requires pre-unit process isolation, and may select the preserved grouped fallback explicitly for compatibility; Central does not silently switch modes. Flux owns the persistent shared cache and single-writer generation merge.
