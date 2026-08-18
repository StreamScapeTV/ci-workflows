# Android validation workflow

`validation.android` version `2.0.0` is the public product-neutral Android/Gradle validation API implemented by `.github/workflows/reusable-android.yml`. Its stable required-check name is **CI / Android validation**.

## Public technology boundary

The reusable workflow accepts only technology-level caller data: an exact admitted source SHA, a validation scope, a bounded working directory, a checked-in Gradle wrapper path, and one strict bounded `validation_plan_json`. The plan contains only repository-owned Gradle task names, an exact targeted-unit selector, or a checked-in executable script path with bounded argv values. Optional private dependency coordinates are an exact repository, full SHA, bounded identity, and bounded subdirectory. There is **no public cache-control input**: shared dependency warming is an internal capability gated by a protected push event and the caller's effective OIDC permission. No caller can select a cache path, endpoint, host, audience, repository identity, runner, credential, or transport. There is no central product ID, repository-specific task registry, runner-label input, shell string, callback, container engine, registry host, signing identity, keystore, release, Helm, Flux, or physical-device input.

Supported validation scopes are `protected-full`, `compile`, `unit`, `assemble`, `lint`, `targeted-unit`, `gradle`, and `script`. Single-purpose Gradle scopes use `{"tasks":[...]}`. `targeted-unit` additionally requires one grammar-bounded JVM `test_selector`. `script` accepts only `{"path":"...","arguments":[...]}` and executes that checked-in file directly without a shell command string.

`protected-full` is deliberately different from the targeted scopes. Its plan is exactly `unit_tasks`, `lint_tasks`, `assemble_tasks`, and `schema`. Unit, lint, and assemble task lists are non-empty. The schema mode is `none`, `gradle`, or `script`. A Gradle schema plan contributes bounded KSP/Room/schema tasks to the same one Gradle invocation; a script schema plan executes one checked-in repository-owned verifier after the combined Gradle invocation in the same copied source and workspace. There is no `compile_tasks` field in `protected-full`, and duplicate tasks across the full-gate categories are rejected.

The execution job runs directly on semantic `mobile` capacity (`[linux, amd64, mobile]`). Callers cannot select runner labels or hosts. The runner-provided Android SDK is required and Java is resolved and validated as JDK major 25 before Android work executes.

## Single-executor protected-full model

One reusable workflow call creates exactly one heavy mobile executor job. That job performs one exact Android checkout, prepares one Gradle workspace, optionally checks out one exact private dependency, executes one Android plan, and then crosses one terminal cleanup/residue boundary.

For `protected-full`, unit + lint + assemble + any Gradle-backed Room/KSP/schema tasks are flattened in that order into one `run_gradle_tasks` invocation. This lets one Gradle process reuse configuration, dependency resolution, and compiled graph state instead of allocating separate cold mobile jobs. A checked-in schema script, when selected, runs afterward against the same copied source, dependency path, Gradle state, SDK, and JDK. A standalone compile leg is intentionally absent from `protected-full`; `compile` remains a targeted scope for smoke/manual use.

The reusable workflow has no matrix and no nested reusable mobile jobs. Separate result metadata does not imply separate Gradle builds.

## Bounded performance telemetry

Android execution measures performance inside the existing executor; it creates no monitoring job, service, artifact, cache, or background infrastructure. `test_summary` records finite integer milliseconds for the complete Android execute function, the Gradle invocation, and any checked-in script phase. On Linux cgroup v2 runners, one lightweight in-process sampler reads this process' fixed cgroup `memory.current` and `pids.current` during the execution window and records the observed peak bytes and process count. Child CPU time is reported as a bounded millisecond delta when the host runtime exposes POSIX child usage.

Unsupported host metrics are emitted as JSON `null` with `resource_measurement: "unavailable"`; the adapter never invents zero usage. The sampler runs beside the existing child process rather than replacing it, and its context manager preserves the original exception, timeout, and failure path. Evidence contains no command arguments, environment values, absolute cgroup paths, credentials, source payloads, or arbitrary process listings.

## Immutable central helpers

Private callers do not clone the central repository with caller-scoped credentials. The workflow invokes central composite actions at immutable full commit SHAs:

- `StreamScapeTV/ci-workflows/actions/validate-android` is pinned to `a01e29210603dc8b4cb9e31b9b0c926c2ab5cf37`, recorded with release `issues #344/#346 Android telemetry and Gradle read-only seed checkpoint`.
- `StreamScapeTV/ci-workflows/actions/upload-gradle-seed` is pinned to `7a0977db839468aac24448831a9a0ffd97b3067b`, recorded with release `issue #347 trusted Gradle seed client`.
- `StreamScapeTV/ci-workflows/actions/exact-checkout`, `prepare-workspace`, `render-evidence`, and `cleanup-workspace` remain pinned to `70e08d4ddf8930046632a7135950e924b82e22bf`, recorded as `issue #116 immutable private-action checkpoint`.
- `StreamScapeTV/ci-workflows/actions/checkout-private-dependency` is pinned to `70e08d4ddf8930046632a7135950e924b82e22bf`, recorded as `issue #104 immutable private-action checkpoint`.

The caller cannot select any helper revision, central-source token, mutable helper ref, or `secrets: inherit` surface.

## Exact source, private writable state, and owner-managed dependency reuse

The admitted caller SHA is checked out exactly once into `source`. Workspace preparation uses the `gradle` profile with Central cache mode `disabled`; this means Central creates no shared writable cache and uses no GitHub Actions cache transport. Before execution, the adapter revalidates the exact source SHA and clean tree, then copies the admitted source through the symlink-rejecting Android copy primitive into marker-bound workflow state. Gradle, checked-in scripts, generated code, Room/schema verification, and build output therefore operate on the isolated copy rather than dirtying the admitted checkout.

Every job retains its own writable `GRADLE_USER_HOME`. On Flux-owned mobile runners, the adapter may additionally forward the runner-provided `GRADLE_RO_DEP_CACHE` only when it is exactly the fixed owner path `/opt/gradle-ro-cache` and resolves to a real non-symlink directory. That path is read-only shared dependency seed state, not a writable Gradle home. If the fixed seed is absent or not mounted, execution falls back to ordinary cold dependency resolution into the private writable home; an unexpected non-empty cache path is rejected rather than treated as caller input. There is no public reusable-workflow cache-path input and no shared daemon, transforms, locks, checkout output, credentials, or arbitrary home state.

Only a fixed non-secret runtime environment is forwarded to product execution: isolated `HOME`, `TMPDIR`, private `GRADLE_USER_HOME`, runner `PATH`, JDK/Android SDK locations, fixed `C.UTF-8` locale, UTC, the optional fixed read-only Gradle dependency seed, and an optional verified private-dependency path. GitHub tokens, private dependency checkout tokens, arbitrary caller environment, workflow metadata, and secret-bearing variables are not forwarded. Gradle always receives `--no-daemon`.

The checked-in script mode is the repository-owned escape hatch for technology-specific verification such as Room/schema integrity. The central workflow owns only path/argv validation and execution mechanics; the consumer repository owns the script content and assertions.

## Protected-push trusted seed warming

Shared Gradle seed warming is deliberately **not** a public workflow input. `reusable-android.yml` intentionally declares no workflow- or job-level token permissions; it inherits the explicit ceiling from the calling job. This is required because GitHub reusable workflows may maintain or reduce caller permissions but may not elevate them. Standard PR/manual/work-branch callers grant only `contents: read`. A protected warming caller in the same organization grants `contents: read` plus `id-token: write`, and GitHub makes that OIDC capability available inside the reusable workflow's caller context. A read-only caller therefore cannot fail startup merely because the reusable workflow supports warming.

After successful Android execution plus copied-source cleanup/residue verification, the workflow considers promotion only when the caller event is a protected `push`. A small authority step then verifies that GitHub actually provided the OIDC request capability for that job. Only when both conditions hold does the immutable issue #347 client run against the **same marker-bound private `GRADLE_USER_HOME` that just executed validation**. The protected consumer warming call therefore opts in by granting `id-token: write`; it does not pass another behavioral flag. No second Gradle home, runner job, artifact handoff, GitHub cache, caller-selected endpoint, PAT, deploy key, S3, or OCI transport is introduced.

The seed client fixes the audience, endpoint, candidate path policy, framing, file/hash bounds, and OIDC request semantics; the caller supplies no cache-specific data. Promotion uses `continue-on-error` so cache availability never becomes product correctness. The seed action nevertheless performs its own terminal marker-bound workspace cleanup under `if: always()`. If that cleanup cannot prove completion, the reusable workflow invokes the ordinary immutable workspace cleanup as a fallback. The final Android result deliberately ignores upload success/failure, but it still requires one verified cleanup path, exact-source cleanliness, Android cleanup/residue, and the authoritative validation result. This keeps a promoter/network/OIDC failure non-fatal while refusing to hide unverified local residue.

## Exact private dependency boundary

Private dependency coordinates are all-or-none. When requested, the reusable workflow passes `private_dependency_token` only to the immutable `checkout-private-dependency` action. That action checks out the exact SHA into registered workspace state, detaches HEAD, erases remotes and credential-bearing Git configuration, verifies the bounded expected subdirectory, and writes only the verified dependency path to subsequent execution state.

The Android adapter refuses the dependency unless the checkout reports matching repository, dependency identity, exact head SHA, expected subdirectory, verified state, erased remotes, and erased credentials. Product execution receives only the verified subdirectory path. The checkout token never reaches planning, caller-source checkout, Gradle/script execution, evidence rendering, seed promotion, or cleanup.

## Outputs, cleanup, and artifacts

The public workflow exposes `result`, bounded `test_summary`, and `cleanup_result`. The summary contains only technology-level status and measurements: scope, single-executor model, task count, JDK major, Gradle/script invocation counts and wall times, complete execute wall time, schema mode, whether an exact private dependency was used, whether dependency resolution ran with the fixed read-only seed or cold mode, bounded child CPU time, and sampled cgroup peak memory/process count when available. Raw command stdout/stderr, environment values, tokens, host paths, and application identifiers are not public outputs.

Android-specific cleanup runs once after execution under `if: always()` whenever workspace preparation succeeded. It removes only the known marker-bound copied-source path, then one residue check proves that path is absent. If trusted seed warming runs, its immutable action consumes only eligible private Gradle `caches/modules-*` content and then removes registered workflow state; otherwise the ordinary registered workspace cleanup removes private dependency state, credentials, private writable Gradle state, temporary files, and evidence. A fallback cleanup runs whenever the seed action did not return `cleanup_verified=true`. The owner-managed read-only dependency seed is runner infrastructure outside job-owned cleanup. A final exact-source check proves the admitted checkout SHA and worktree remained unchanged. Terminal projection fails the job unless authoritative execution and every applicable cleanup/residue check succeed; the upload result itself is not an authoritative success condition.

Routine runs retain zero GitHub Actions artifacts. There is no `actions/cache`, artifact upload, APK/AAB retention, signing, publication, ADB, emulator, or physical-device authority in this workflow.

## Repository-owned smoke

`.github/workflows/android-validation-smoke.yml` calls the reusable workflow itself on pull requests that change the Android reusable surface. It uses the checked-in synthetic Gradle project under `tests/fixtures/android-validation/smoke-project` and calls `protected-full` once with three distinct synthetic Gradle tasks, including `verifyToolchainSmoke`. The adapter combines those tasks into one Gradle invocation. The fixture performs real Gradle execution and verifies JDK 25, Android API 37, and Android Build Tools 37. The smoke proves reusable-workflow wiring, semantic mobile capacity, exact source handling, single-executor composition, and terminal cleanup; it is not application or physical-device certification.
