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

`protected-full` flattens unit, lint, assemble, and applicable Gradle-backed Room/KSP/schema tasks into one Gradle invocation. A checked-in schema script, when selected, executes afterward in the same copied source/workspace. The workflow has no matrix and no nested mobile reusable job.

This preserves compiled/configured state inside a single Gradle process rather than creating several cold Android jobs.

## Shared dependency cache

Each job receives a private writable `GRADLE_USER_HOME`. The Android runtime may additionally forward `GRADLE_RO_DEP_CACHE=/opt/gradle-ro-cache` when that runner-owned directory exists. Gradle reads dependency modules from that shared cache and writes new resolution state only to the job-private Gradle home.

If the read-only cache is absent, execution falls back to normal cold dependency resolution. Cache misses remain normal Gradle dependency resolution.

After successful Android execution, copied-source cleanup, and residue verification, the same executor makes one best-effort cache-sync call **before** registered workspace cleanup removes the private `GRADLE_USER_HOME`. The sync client reads only that job's `GRADLE_USER_HOME/caches/modules-*`, uses the existing bounded framing/filtering contract, and streams the delta to the fixed cluster-local Flux service. It does not invoke Gradle again.

Central owns this invocation because it owns the registered workspace lifecycle. Flux owns the internal ClusterIP writer and the single-writer generation merge. Android product callers never gain a cache endpoint, token, OIDC permission, or separate warming job.

The cache sync is `continue-on-error`: a missing cache, empty delta, unavailable internal service, or rejected promotion cannot overturn a correct product build. Registered workspace cleanup always runs afterward and remains the only owner of private workspace deletion.

The Gradle process and cache-sync client use no GitHub OIDC. Routine PR, manual, work-branch, and integration validation therefore require only `contents: read`; there is no protected-branch-only warming topology.

## Performance evidence

The Android execute phase reports:

- total execute wall time;
- Gradle wall time;
- optional checked-in script wall time;
- child CPU time when available;
- sampled cgroup peak memory and process count when available;
- `gradle_dependency_cache_mode=read-only-seed|cold`.

This evidence is collected inside the existing executor and does not create monitoring jobs or services.

## Helper checkpoints

`reusable-android.yml` composes reviewed Central helpers by immutable source identity:

- `validate-android@a01e29210603dc8b4cb9e31b9b0c926c2ab5cf37` — `issues #344/#346 Android telemetry and Gradle read-only seed checkpoint`;
- `upload-gradle-seed@b17f37545dec6da1e158edcc2092545cfa5435ce` — `issue #346 internal no-OIDC Gradle cache sync checkpoint`;
- `exact-checkout@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`;
- `prepare-workspace@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`;
- `render-evidence@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`;
- `cleanup-workspace@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`;
- `checkout-private-dependency@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #104 immutable private-action checkpoint`.

The action lock must record the same cache-sync checkpoint before the candidate is merge-state.

## Runtime state

The admitted checkout is copied into registered disposable workflow state before product execution. Java, Android SDK tools, Gradle, and checked-in scripts use that isolated state. The original admitted checkout remains a verification source and is checked again at the end.

The private dependency, when requested, is checked out once to registered state and only its verified path reaches product execution. Its checkout credential does not reach Gradle or the cache-sync client.

## Cleanup

Android copied-source cleanup runs after execution, followed by residue verification. A successful run may then attempt the internal dependency-delta sync while the private Gradle home still exists. Registered workspace cleanup always follows and removes the private writable Gradle home, dependency checkout, temporary files, and evidence. The runner-owned read-only dependency cache is outside job cleanup and persists for later jobs.

Terminal success requires the Android plan/execution plus applicable cleanup/residue/source-clean checks. Cache availability or cache updating is not a correctness condition.

## Performance ownership

Central owns the generic read-only dependency-cache handoff, same-executor best-effort miss sync, and timing evidence. The Android product repository owns Gradle/test resource settings and task selection. Flux owns the persistent shared cache and the internal single-writer generation merge that makes successful dependency misses reusable by later runs.
