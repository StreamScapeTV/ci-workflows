# Android validation workflow

Central exposes two product-neutral Android/Gradle validation entry points that share the same typed Android implementation and immutable helper set but have different explicit token permissions:

- `validation.android` version `2.0.0`, implemented by `.github/workflows/reusable-android.yml`, is the routine read-only API with stable check **CI / Android validation** and `contents: read` only.
- `validation.android-seed-warm` version `1.0.0`, implemented by `.github/workflows/reusable-android-seed-warm.yml`, is the protected-push seed-warming API with stable check **CI / Android protected seed validation** and exactly `contents: read` plus `id-token: write`.

A consumer selects exactly one entry point for a validation event. PR, manual, and ordinary work-branch validation use the routine API. A protected integration-branch push may use the seed-warm API. The two APIs do not run as consecutive build stages and never require two Gradle builds for one event.

## Public technology boundary

Both reusable workflows accept the same technology-level caller data: an exact admitted source SHA, a validation scope, a bounded working directory, a checked-in Gradle wrapper path, and one strict bounded `validation_plan_json`. The plan contains only repository-owned Gradle task names, an exact targeted-unit selector, or a checked-in executable script path with bounded argv values. Optional private dependency coordinates are an exact repository, full SHA, bounded identity, and bounded subdirectory.

There is **no public cache-control or promotion input**. The seed-warm API differs only in its explicit reviewed permission profile and internal post-validation promotion step. No caller can select a cache path, endpoint, host, audience, repository identity, runner, credential, or transport. There is no central product ID, repository-specific task registry, runner-label input, shell string, callback, container engine, registry host, signing identity, keystore, release, Helm, Flux, or physical-device input.

Supported validation scopes are `protected-full`, `compile`, `unit`, `assemble`, `lint`, `targeted-unit`, `gradle`, and `script`. Single-purpose Gradle scopes use `{"tasks":[...]}`. `targeted-unit` additionally requires one grammar-bounded JVM `test_selector`. `script` accepts only `{"path":"...","arguments":[...]}` and executes that checked-in file directly without a shell command string.

`protected-full` has exactly `unit_tasks`, `lint_tasks`, `assemble_tasks`, and `schema`. Unit, lint, and assemble task lists are non-empty. The schema mode is `none`, `gradle`, or `script`. A Gradle schema plan contributes bounded KSP/Room/schema tasks to the same one Gradle invocation; a script schema plan executes one checked-in repository-owned verifier after the combined Gradle invocation in the same copied source and workspace. There is no standalone protected-full compile leg when the unit/assemble graph already compiles the application.

Each entry point runs directly on semantic `mobile` capacity (`[linux, amd64, mobile]`). Callers cannot select runner labels or hosts. The runner-provided Android SDK is required and Java is resolved and validated as JDK major 25 before Android work executes.

## Single-executor protected-full model

One reusable workflow call creates exactly one heavy mobile executor job. That job performs one exact Android checkout, prepares one private Gradle workspace, optionally checks out one exact private dependency, executes one Android plan, and crosses one terminal cleanup/residue boundary.

For `protected-full`, unit + lint + assemble + any Gradle-backed Room/KSP/schema tasks are flattened in order into one `run_gradle_tasks` invocation. This lets one Gradle process reuse configuration, dependency resolution, and compiled graph state instead of allocating separate cold mobile jobs. A checked-in schema script, when selected, runs afterward against the same copied source, dependency path, Gradle state, SDK, and JDK. `compile` remains a targeted scope for branch smoke/manual use.

Neither public Android reusable has a matrix or a nested reusable mobile job. The protected consumer chooses the seed-warm reusable **instead of** the routine reusable, so warming adds no second executor or second Gradle graph.

## Bounded performance telemetry

Android execution measures performance inside the existing executor; it creates no monitoring job, service, artifact, cache, or background infrastructure. `test_summary` records finite integer milliseconds for the complete Android execute function, the Gradle invocation, and any checked-in script phase. On Linux cgroup v2 runners, one lightweight in-process sampler reads the process cgroup's `memory.current` and `pids.current` during execution and records observed peak bytes/process count. Child CPU time is reported as a bounded millisecond delta when POSIX child usage is available.

Unsupported host metrics are emitted as JSON `null` with `resource_measurement: "unavailable"`; the adapter never invents zero usage. Evidence contains no command arguments, environment values, absolute cgroup paths, credentials, source payloads, or arbitrary process listings.

## Immutable central helpers

Private callers do not clone Central with caller-scoped credentials. Both Android reusables compose reviewed immutable central helpers from `contracts/action-tool-lock.json`:

- `StreamScapeTV/ci-workflows/actions/validate-android` is pinned to `a01e29210603dc8b4cb9e31b9b0c926c2ab5cf37`, recorded with release `issues #344/#346 Android telemetry and Gradle read-only seed checkpoint`.
- `StreamScapeTV/ci-workflows/actions/upload-gradle-seed` is pinned to `7a0977db839468aac24448831a9a0ffd97b3067b`, recorded with release `issue #347 trusted Gradle seed client`; it appears only in `validation.android-seed-warm`.
- `StreamScapeTV/ci-workflows/actions/exact-checkout`, `prepare-workspace`, `render-evidence`, and `cleanup-workspace` remain pinned to `70e08d4ddf8930046632a7135950e924b82e22bf`, recorded as `issue #116 immutable private-action checkpoint`.
- `StreamScapeTV/ci-workflows/actions/checkout-private-dependency` is pinned to `70e08d4ddf8930046632a7135950e924b82e22bf`, recorded as `issue #104 immutable private-action checkpoint`.

The caller cannot select a helper revision, central-source token, mutable helper ref, or `secrets: inherit` surface.

## Exact source, private writable state, and owner-managed dependency reuse

The admitted caller SHA is checked out exactly once into `source`. Workspace preparation uses the `gradle` profile with Central cache mode `disabled`; Central creates no shared writable cache and uses no GitHub Actions cache transport. Before execution, the adapter revalidates exact source SHA/cleanliness and copies the admitted source through the symlink-rejecting Android copy primitive into marker-bound workflow state.

Every job retains its own writable `GRADLE_USER_HOME`. On Flux-owned mobile runners, the adapter may additionally forward runner-provided `GRADLE_RO_DEP_CACHE` only when it is exactly `/opt/gradle-ro-cache`, resolves to a real non-symlink directory, and does not alias the writable home. That path is read-only shared dependency seed state. If the fixed seed is absent, execution falls back to ordinary cold dependency resolution into the private writable home; an unexpected non-empty cache path is rejected.

Only a fixed non-secret runtime environment is forwarded to product execution: isolated `HOME`, `TMPDIR`, private `GRADLE_USER_HOME`, runner `PATH`, JDK/Android SDK locations, locale/UTC, the optional fixed read-only Gradle dependency seed, and an optional verified private-dependency path. GitHub tokens, private dependency checkout tokens, arbitrary caller environment, workflow metadata, and secret-bearing variables are not forwarded. Gradle execution policy remains owned by the Android adapter/product contract.

## Permission split and protected-push trusted seed warming

GitHub reusable workflows cannot elevate the caller token. Central therefore does not model seed warming as an optional permission inside `validation.android`.

`validation.android` declares exactly:

- `contents: read`

`validation.android-seed-warm` declares exactly:

- `contents: read`
- `id-token: write`

The seed-warm public contract permits only `push` and `workflow_call`; the consuming repository further restricts its call to the protected integration-branch push. PR/manual/work-branch callers never call this entry point and never receive OIDC authority. There is no behavioral `promote_gradle_seed` flag.

Inside the seed-warm executor, promotion is considered only when the event is a protected `push` and GitHub actually exposed the OIDC request URL/token. After authoritative Android execution succeeds and copied-source cleanup/residue is verified, the immutable #347 client reads portable `caches/modules-*` from the **same marker-bound private `GRADLE_USER_HOME` that just executed validation**. No second Gradle home, runner job, artifact handoff, GitHub cache, caller-selected endpoint, PAT, deploy key, S3, or OCI transport is introduced.

The seed client fixes the OIDC audience, endpoint, candidate policy, framing, file/hash bounds, and request semantics. Promotion uses `continue-on-error`, so cache availability never becomes product correctness. Its cleanup remains authoritative: if the uploader does not prove marker-bound cleanup, the reusable workflow invokes ordinary immutable workspace cleanup as fallback. Terminal success still requires exact-source cleanliness, Android cleanup/residue, and one verified workspace cleanup path.

## Exact private dependency boundary

Private dependency coordinates are all-or-none. When requested, either reusable passes `private_dependency_token` only to immutable `checkout-private-dependency`. That action checks out the exact SHA into registered state, detaches HEAD, erases remotes and credential-bearing Git configuration, verifies the bounded subdirectory, and exposes only the verified dependency path.

The Android adapter refuses the dependency unless repository/id/SHA/subdirectory and credential-erasure state match the planned request. Product execution receives only the verified subdirectory path. The checkout token never reaches planning, Gradle/script execution, evidence rendering, seed promotion, or cleanup.

## Outputs, cleanup, and artifacts

Both APIs expose `result`, bounded `test_summary`, and `cleanup_result`. The summary contains only technology-level status and measurements: scope, single-executor model, task count, JDK major, Gradle/script invocation counts and wall times, complete execute wall time, schema mode, private-dependency use, `gradle_dependency_cache_mode` (`read-only-seed` or `cold`), bounded child CPU time, and sampled cgroup peak memory/process count when available.

Routine validation always performs ordinary registered-workspace cleanup after Android cleanup/residue. Seed-warm validation allows the immutable uploader to consume only eligible private Gradle dependency-module content and perform marker-bound cleanup; ordinary workspace cleanup runs whenever the uploader did not prove cleanup. The runner-owned read-only seed is outside job-owned cleanup. A final exact-source check proves the admitted checkout remained unchanged.

Routine runs retain zero GitHub Actions artifacts. There is no `actions/cache`, artifact bridge, signing, publication, ADB, emulator, or physical-device authority in either validation entry point.

## Repository-owned smoke

`.github/workflows/android-validation-smoke.yml` directly exercises the read-only Android reusable surface on pull requests that change Android reusable code. It uses the checked-in synthetic Gradle project under `tests/fixtures/android-validation/smoke-project` and executes one protected-full plan with three distinct synthetic Gradle tasks, including `verifyToolchainSmoke`. The fixture verifies JDK 25, Android API 37, Android Build Tools 37, single-executor composition, exact source handling, and terminal cleanup. The protected OIDC path is contract-tested statically and is live-proven only by an authorized protected consumer push; PR smoke never receives seed-promotion authority.
