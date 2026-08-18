# Android validation architecture

## Purpose

Central Android validation provides one product-neutral Gradle execution boundary for Android consumers. The performance model is deliberately simple: one mobile executor, one copied source tree, one private writable Gradle home, and an optional runner-provided read-only dependency cache.

## Layers

1. `contracts/android-validation.json` defines the Android validation contract.
2. `src/ci_workflows/android_contract.py` validates the request and resolves a bounded plan.
3. `src/ci_workflows/android_execution.py` verifies toolchain/wrapper state, copies source, runs commands, and checks mutation/output rules.
4. `src/ci_workflows/ciw_android.py` adapts plan/execute/cleanup/residue phases and forwards the fixed runner-provided Gradle read-only cache when present.
5. `src/ci_workflows/android_resource_metrics.py` measures bounded same-executor wall/CPU/cgroup evidence.
6. `actions/validate-android/action.yml` is the thin workflow adapter.
7. `.github/workflows/reusable-android.yml` composes one mobile job around those primitives.

There is no second Android build for cache warming and routine validation has no OIDC dependency.

## One-executor protected-full

`protected-full` flattens unit, lint, assemble, and applicable Gradle-backed Room/KSP/schema tasks into one Gradle invocation. A checked-in schema script, when selected, executes afterward in the same copied source/workspace. The workflow has no matrix and no nested mobile reusable job.

This preserves compiled/configured state inside a single Gradle process rather than creating several cold Android jobs.

## Shared dependency cache

Each job receives a private writable `GRADLE_USER_HOME`. The Android runtime may additionally forward `GRADLE_RO_DEP_CACHE=/opt/gradle-ro-cache` when that runner-owned directory exists. Gradle reads dependency modules from that shared cache and writes new resolution state only to the job-private Gradle home.

If the read-only cache is absent, execution falls back to normal cold dependency resolution. Cache misses remain normal Gradle dependency resolution; later reuse is handled by the internal runner/Flux cache-update path owned by Android #800 and Flux #327.

The Gradle process itself never needs GitHub OIDC to read the shared cache. Routine PR, manual, work-branch, and integration validation therefore use only the normal read-only workflow permissions and contain no seed-uploader action.

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

`reusable-android.yml` composes the Central helpers recorded by `contracts/action-tool-lock.json`:

- `validate-android@a01e29210603dc8b4cb9e31b9b0c926c2ab5cf37` — `issues #344/#346 Android telemetry and Gradle read-only seed checkpoint`;
- `exact-checkout@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`;
- `prepare-workspace@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`;
- `render-evidence@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`;
- `cleanup-workspace@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`;
- `checkout-private-dependency@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #104 immutable private-action checkpoint`.

The Gradle seed uploader is not part of the routine Android workflow.

## Runtime state

The admitted checkout is copied into registered disposable workflow state before product execution. Java, Android SDK tools, Gradle, and checked-in scripts use that isolated state. The original admitted checkout remains a verification source and is checked again at the end.

The private dependency, when requested, is checked out once to registered state and only its verified path reaches product execution. Its checkout credential does not reach Gradle.

## Cleanup

Android copied-source cleanup runs after execution, followed by residue verification. Registered workspace cleanup then removes the private writable Gradle home, dependency checkout, temporary files, and evidence. The runner-owned read-only dependency cache is outside job cleanup and persists for later jobs.

Terminal success requires the Android plan/execution plus applicable cleanup/residue/source-clean checks. Cache availability or later cache updating is not a correctness condition.

## Performance ownership

Central owns the generic read-only dependency-cache handoff and timing evidence. The Android product repository owns Gradle/test resource settings and task selection. Flux owns the persistent shared cache and the internal mechanism that incorporates successful dependency misses for later runs.