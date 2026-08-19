# Android validation architecture

## Purpose

Central Android validation provides one product-neutral Gradle execution boundary for Android consumers. The performance model is deliberately simple: one mobile executor, one copied source tree, one private writable Gradle home, and an optional runner-provided read-only dependency cache.

## Layers

1. `contracts/android-validation.json` defines the Android validation contract.
2. `src/ci_workflows/android_contract.py` validates the request and resolves a bounded plan.
3. `src/ci_workflows/android_execution.py` verifies toolchain/wrapper state, copies source, runs commands, and checks mutation/output rules.
4. `src/ci_workflows/ciw_android.py` adapts plan/execute/cleanup/residue phases and forwards the fixed runner-provided Gradle read-only cache when present.
5. `src/ci_workflows/android_resource_metrics.py` measures bounded same-executor wall/CPU/cgroup evidence.
6. `actions/validate-android/action.yml` is the thin validation adapter.
7. `actions/upload-gradle-seed/action.yml` is the thin best-effort internal dependency-delta sync adapter.
8. `.github/workflows/reusable-android.yml` composes one mobile job around those primitives.

There is no second Android build for cache warming and routine validation has no OIDC dependency.

## One-executor protected-full

`protected-full` keeps the entire request inside one mobile executor, one copied source tree, one private writable `GRADLE_USER_HOME`, one exact private dependency checkout, and the same runner-provided read-only dependency seed. Within that executor, the caller-owned Gradle task families are executed sequentially as non-empty unit, lint, assemble, and Gradle-schema groups. Each group uses the existing `--no-daemon` primitive, so Gradle starts a fresh single-use daemon and releases task-family class metadata before the next group. A checked-in schema script, when selected, executes afterward in the same copied source/workspace. The workflow has no matrix and no nested mobile reusable job.

This preserves one runner/workspace/cache lifecycle while bounding Gradle metaspace lifetime. It does not create separate Android jobs, alter task coverage, or add a cache-warming invocation.

## Shared dependency cache

Each job receives a private writable `GRADLE_USER_HOME`. The Android runtime may additionally forward `GRADLE_RO_DEP_CACHE=/opt/gradle-ro-cache` when that runner-owned directory exists. Gradle reads dependency modules from that shared cache and writes new resolution state only to the job-private Gradle home.

If the read-only cache is absent, execution falls back to normal cold dependency resolution. Cache misses remain normal Gradle dependency resolution.

After successful Android execution, copied-source cleanup, and residue verification, the same executor makes one best-effort cache-sync call **before** registered workspace cleanup removes the private `GRADLE_USER_HOME`. The sync client reads only that job's `GRADLE_USER_HOME/caches/modules-*`, uses the existing bounded framing/filtering contract, and streams the delta to the fixed cluster-local Flux service. It does not invoke Gradle again.

Central owns this invocation because it owns the registered workspace lifecycle. Flux owns the internal ClusterIP writer and the single-writer generation merge. Android product callers never gain a cache endpoint, token, OIDC permission, or separate warming job.

The cache sync is `continue-on-error`: a missing cache, empty delta, unavailable internal service, or rejected promotion cannot overturn a correct product build. Registered workspace cleanup always runs afterward and remains the only owner of private workspace deletion.

For bounded live diagnosis, the sync client reports only the selected private dependency-delta file count and total bytes before upload. HTTP `409` is projected as `gradle_seed_writer_busy`, HTTP `422` as `gradle_seed_promotion_rejected`, and other non-success HTTP statuses as `gradle_seed_upload_rejected`. It does not log dependency paths, payload content, endpoints, tokens, headers, credentials, or arbitrary server response bodies.

The Gradle process and cache-sync client use no GitHub OIDC. Routine PR, manual, work-branch, and integration validation therefore require only `contents: read`; there is no protected-branch-only warming topology.

## Performance evidence

The Android execute phase reports:

- total execute wall time;
- aggregate Gradle wall time across all non-empty task groups;
- the actual Gradle invocation count;
- optional checked-in script wall time;
- child CPU time when available;
- sampled cgroup peak memory and process count when available;
- `gradle_dependency_cache_mode=read-only-seed|cold`.

This evidence is collected inside the existing executor and does not create monitoring jobs or services.

Failed or timed-out reviewed `android.*` primitive operations emit only a sanitized bounded last-80-line/16-KiB diagnostic tail before preserving the existing stable failure code. Successful Android operations and non-Android primitives retain their prior output behavior.

## Helper checkpoints

`reusable-android.yml` composes reviewed Central helpers by immutable source identity:

- `validate-android@410fed5fb5fd7c930b28c545758a5f3992e43b0c` — `issue #344 bounded Android failure diagnostics checkpoint`;
- `upload-gradle-seed@fa67b6a1580ff2eb7386a9e58de09896b9990696` — `issue #346 bounded Gradle cache sync diagnostics checkpoint`;
- `exact-checkout@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`;
- `prepare-workspace@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`;
- `render-evidence@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`;
- `cleanup-workspace@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`;
- `checkout-private-dependency@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #104 immutable private-action checkpoint`.

The action lock must record the same helper identities before the candidate is merge-state.

## Runtime state

The admitted checkout is copied into registered disposable workflow state before product execution. Java, Android SDK tools, Gradle, and checked-in scripts use that isolated state. The original admitted checkout remains a verification source and is checked again at the end.

The private dependency, when requested, is checked out once to registered state and only its verified path reaches product execution. Its checkout credential does not reach Gradle or the cache-sync client.

## Cleanup

Android copied-source cleanup runs after execution, followed by residue verification. A successful run may then attempt the internal dependency-delta sync while the private Gradle home still exists. Registered workspace cleanup always follows and removes the private writable Gradle home, dependency checkout, temporary files, and evidence. The runner-owned read-only dependency cache is outside job cleanup and persists for later jobs.

Terminal success requires the Android plan/execution plus applicable cleanup/residue/source-clean checks. Cache availability or cache updating is not a correctness condition.

## Performance ownership

Central owns the generic read-only dependency-cache handoff, same-executor best-effort miss sync, and timing evidence. The Android product repository owns Gradle/test resource settings and task selection. Flux owns the persistent shared cache and the internal single-writer generation merge that makes successful dependency misses reusable by later runs.
