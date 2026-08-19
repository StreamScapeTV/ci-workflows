# Android validation architecture

## Purpose

Central Android validation provides one product-neutral Gradle execution boundary for Android consumers. The performance model is deliberately simple: one mobile executor, one private writable Gradle home, one optional exact private dependency checkout, and an optional runner-provided read-only dependency cache. Authoritative validation still uses one copied source tree at a time.

## Layers

1. `contracts/android-validation.json` defines the Android validation contract.
2. `src/ci_workflows/android_contract.py` validates the request and resolves a bounded plan.
3. `src/ci_workflows/android_execution.py` verifies toolchain/wrapper state, copies source, runs commands, and checks mutation/output rules.
4. `src/ci_workflows/ciw_android.py` adapts plan/execute/cleanup/residue phases and forwards the fixed runner-provided Gradle read-only cache when present.
5. `src/ci_workflows/android_resource_metrics.py` measures bounded same-executor wall/CPU/cgroup evidence.
6. `actions/validate-android/action.yml` is the thin validation adapter.
7. `actions/upload-gradle-seed/action.yml` is the thin best-effort internal dependency-delta sync adapter.
8. `.github/workflows/reusable-android.yml` composes one mobile job around those primitives and may reuse the grouped Android primitive for an optional private-dependency prebuild pass before authoritative validation.

There is no second runner or cache-specific warming build and routine validation has no OIDC dependency.

## One-executor protected-full

`protected-full` keeps the entire request inside one mobile executor, one copied source tree, one private writable `GRADLE_USER_HOME`, one exact private dependency checkout, and the same runner-provided read-only dependency seed. Within that executor, the caller-owned Gradle task families are executed sequentially as non-empty unit, lint, assemble, and Gradle-schema groups. Each group uses the existing `--no-daemon` primitive, so Gradle starts a fresh single-use daemon and releases task-family class metadata before the next group. A checked-in schema script, when selected, executes afterward in the same copied source/workspace. The workflow has no matrix and no nested mobile reusable job.

For private dependency graphs whose preparation itself is too large to coexist with the authoritative application task graph under the caller-owned memory profile, the workflow accepts optional `dependency_prebuild_plan_json`. It is validated and executed through the same immutable `protected-full` primitive before authoritative execution, using the same mobile executor, private Gradle home, read-only dependency seed, and verified private dependency checkout. The prebuild copied caller source is then removed and residue-checked before the normal validation copy is created. This preserves dependency build outputs while releasing Gradle process/class metadata between bounded dependency layers and again before authoritative application validation.

The prebuild plan does not change Gradle memory, worker, Kotlin, or test settings; it only selects caller-owned Gradle work and reuses the existing four ordered daemon boundaries. It is optional, requires a private dependency request, and does not replace the authoritative unit/lint/assemble/schema plan. Terminal success requires every requested prebuild plan/execute/cleanup/residue phase plus the authoritative plan/execution/cleanup chain to succeed.

## Shared dependency cache

Each job receives a private writable `GRADLE_USER_HOME`. The Android runtime may additionally forward `GRADLE_RO_DEP_CACHE=/opt/gradle-ro-cache` when that runner-owned directory exists. Gradle reads dependency modules from that shared cache and writes new resolution state only to the job-private Gradle home.

If the read-only cache is absent, execution falls back to normal cold dependency resolution. Cache misses remain normal Gradle dependency resolution.

After successful authoritative Android execution, copied-source cleanup, and residue verification, the same executor makes one best-effort cache-sync call **before** registered workspace cleanup removes the private `GRADLE_USER_HOME`. Optional dependency prebuild and authoritative validation share that private Gradle home, so one final sync can include bounded dependency misses from both phases. The sync client reads only that job's `GRADLE_USER_HOME/caches/modules-*`, uses the existing bounded framing/filtering contract, and streams the delta to the fixed cluster-local Flux service. It does not invoke Gradle again.

Central owns this invocation because it owns the registered workspace lifecycle. Flux owns the internal ClusterIP writer and the single-writer generation merge. Android product callers never gain a cache endpoint, token, OIDC permission, or separate warming job.

The cache sync is `continue-on-error`: a missing cache, empty delta, unavailable internal service, or rejected promotion cannot overturn a correct product build. Registered workspace cleanup always runs afterward and remains the only owner of private workspace deletion.

For bounded live diagnosis, the sync client reports only the selected private dependency-delta file count and total bytes before upload. HTTP `409` is projected as `gradle_seed_writer_busy`, HTTP `422` as `gradle_seed_promotion_rejected`, and other non-success HTTP statuses as `gradle_seed_upload_rejected`. It does not log dependency paths, payload content, endpoints, tokens, headers, credentials, or arbitrary server response bodies.

The Gradle process and cache-sync client use no GitHub OIDC. Routine PR, manual, work-branch, and integration validation therefore require only `contents: read`; there is no protected-branch-only warming topology.

## Performance evidence

The authoritative Android execute phase reports:

- total execute wall time;
- aggregate Gradle wall time across all non-empty task groups;
- the actual Gradle invocation count;
- optional checked-in script wall time;
- child CPU time when available;
- sampled cgroup peak memory and process count when available;
- `gradle_dependency_cache_mode=read-only-seed|cold`.

The optional prebuild is a distinct action execution in the same job, so its outcome and elapsed job time remain independently observable without changing the authoritative public `test_summary`. End-to-end performance decisions use the reusable-workflow duration plus authoritative `test_summary`; no monitoring job or service is added.

Failed or timed-out reviewed `android.*` primitive operations emit only a sanitized bounded last-80-line/16-KiB diagnostic tail before preserving the existing stable failure code. Successful Android operations and non-Android primitives retain their prior output behavior.

## Helper checkpoints

`reusable-android.yml` composes reviewed Central helpers by immutable source identity:

- `validate-android@ac56fd7b3fac55f231e7b2ba715a5aebebbe51ef` — `issue #373 protected-full Gradle group isolation checkpoint`;
- `upload-gradle-seed@fa67b6a1580ff2eb7386a9e58de09896b9990696` — `issue #346 bounded Gradle cache sync diagnostics checkpoint`;
- `exact-checkout@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`;
- `prepare-workspace@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`;
- `render-evidence@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`;
- `cleanup-workspace@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`;
- `checkout-private-dependency@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #104 immutable private-action checkpoint`.

The action lock must record the same helper identities before the candidate is merge-state.

## Runtime state

The admitted checkout is copied into registered disposable workflow state before each Android execution. With an optional dependency prebuild, that temporary copy is removed and residue-checked before the authoritative validation copy is created. Java, Android SDK tools, Gradle, and checked-in scripts use registered isolated state. The original admitted checkout remains a verification source and is checked again at the end.

The private dependency, when requested, is checked out once to registered state and only its verified path reaches Android execution. Its checkout credential does not reach Gradle or the cache-sync client. Dependency build outputs intentionally survive the optional prebuild copied-source cleanup because the exact private dependency checkout remains inside the registered workspace until terminal workspace cleanup.

## Cleanup

If present, dependency-prebuild copied-source cleanup and residue verification complete before authoritative execution. Authoritative Android copied-source cleanup then runs after product execution, followed by residue verification. A successful authoritative run may attempt the internal dependency-delta sync while the private Gradle home still exists. Registered workspace cleanup always follows and removes the private writable Gradle home, dependency checkout, temporary files, and evidence. The runner-owned read-only dependency cache is outside job cleanup and persists for later jobs.

Terminal success requires the Android plan/execution plus all applicable prebuild/cleanup/residue/source-clean checks. Cache availability or cache updating is not a correctness condition.

## Performance ownership

Central owns generic one-executor process isolation, the read-only dependency-cache handoff, same-executor best-effort miss sync, and timing evidence. The Android product repository owns Gradle/test resource settings and task selection, including any bounded private-dependency prebuild task layers. Flux owns the persistent shared cache and the internal single-writer generation merge that makes successful dependency misses reusable by later runs.
