# Android validation architecture

## Authority and layers

The Android gate follows the repository's named-function architecture:

1. `contracts/android-validation.json` is the reviewed behavior and compatibility authority.
2. `contracts/android-source-policy.json` is the narrow Android projection for stable policy failure mapping and exact synthetic-marker exceptions.
3. `src/ci_workflows/android_types.py` defines immutable requests, plans, commands, wrappers, results, stable errors, and bounded policy diagnostics.
4. `src/ci_workflows/android_contract.py` validates the complete validation contract, derives source trust, parses bounded inputs, and resolves one deterministic plan.
5. `src/ci_workflows/android_policy.py` preserves shared repository policy while applying only exact repository/profile/path/rule/blob exceptions and safe failure subjects.
6. `src/ci_workflows/android_execution.py` owns direct process execution, exact toolchain and wrapper verification, source copying, mutation checks, output checks, redaction, and no-follow cleanup.
7. `src/ci_workflows/android.py` applies the projected repository policy before and after one plan and maps policy findings to stable Android codes.
8. `src/ci_workflows/ciw_android.py` adapts the named `ciw android validate` command to plan, execute, cleanup, and residue phases and forwards only the fixed runner-owned read-only Gradle dependency seed path when present.
9. `src/ci_workflows/android_resource_metrics.py` samples bounded same-executor wall/CPU/cgroup evidence without changing child execution.
10. `actions/validate-android/action.yml` is a thin composite adapter.
11. `actions/upload-gradle-seed/action.yml` is the bounded OIDC + portable Gradle modules promotion adapter; it owns no product task selection and no caller-selected endpoint.
12. `.github/workflows/reusable-android.yml` owns protected planning, semantic mobile scheduling, exact source/dependency primitives, capability-gated same-workspace seed warming, evidence, and verified terminal cleanup.

Workflow YAML does not implement Gradle task selection, repository compatibility, authentication policy, cleanup traversal, test-filter parsing, source-policy exception selection, product policy, cache candidate filtering/framing, or resource-monitoring infrastructure. Those decisions remain in typed code and checked-in contract data.

## Immutable private helper source

Private reusable callers do not clone the private `StreamScapeTV/ci-workflows` repository with the caller-scoped Actions token. `reusable-android.yml` composes reviewed central primitives through independently locked identities from `contracts/action-tool-lock.json`: `validate-android` uses `a01e29210603dc8b4cb9e31b9b0c926c2ab5cf37` with release `issues #344/#346 Android telemetry and Gradle read-only seed checkpoint`; `upload-gradle-seed` uses `7a0977db839468aac24448831a9a0ffd97b3067b` with release `issue #347 trusted Gradle seed client`; `exact-checkout`, `prepare-workspace`, `render-evidence`, and `cleanup-workspace` use `70e08d4ddf8930046632a7135950e924b82e22bf` with release `issue #116 immutable private-action checkpoint`; and `checkout-private-dependency` currently resolves to that same source SHA while retaining its separately recorded release `issue #104 immutable private-action checkpoint`.

Each composite resolves its implementation and contracts relative to its own `GITHUB_ACTION_PATH`, so the called helper source is the immutable action-lock revision rather than a caller PR merge SHA or a second central checkout. The caller cannot select or override any helper revision. The workflow does not accept a central-repository PAT, generic checkout token, mutable ref, cache endpoint, cache path, or `secrets: inherit`.

The existing optional `private_dependency_token` remains a separate product dependency credential. It is passed only to `checkout-private-dependency` when the protected Android plan selects the reviewed dependency contract; it is never used to retrieve central helper source and never reaches planning, Android execution, evidence, seed promotion, or cleanup actions.

## Trust and runner resolution

The planner derives trust from immutable GitHub event metadata. Only `trusted-pr` and `trusted-exact` are permitted by the Android profiles. Untrusted forks fail before mobile execution, shared cache, private dependency, live backend, signing, device, or privileged state. No public input can select a semantic profile or raw label.

The planner requests semantic `mobile` through the central runner resolver. The execution job consumes only the protected resolver's JSON output. Bare `self-hosted`, hosted labels, Apple profiles, Buildah profiles, composite label mixtures, and caller expressions are excluded.

The `mobile` profile means Android build capacity, not a physical Android device. `device-handoff` produces a data packet for another workflow and has no executable task.

Seed warming does not add a public workflow input. The reusable Android workflow declares no token-permission elevation of its own; it inherits the caller's effective permission ceiling. Promotion is considered only for a protected `push`, and a bounded authority step requires GitHub's OIDC request capability to actually be present. Ordinary PR/manual/work-branch callers grant only `contents: read`, so OIDC remains absent and the uploader is skipped. A trusted protected-branch warming caller opts in solely by granting `id-token: write` to that exact reusable call; Central cannot elevate a read-only caller into an OIDC-capable one.

## Contract-owned command model

A public request identifies one repository task profile. Contract resolution requires an exact repository/task/profile/working-directory/wrapper tuple. The resulting command sequence contains only fixed argv arrays. Gradle tasks use strict colon-qualified task syntax and are invoked through the verified wrapper. Consumer hooks must be checked-in `scripts/` files and use fixed arguments.

The only request-derived command value is an optional targeted test selector. Its grammar admits a fully qualified class or method identifier and excludes wildcards, traversal, whitespace, shell operators, hashes, Gradle properties, init scripts, and arbitrary arguments.

Repository-specific values are data, not code branches. The contract currently describes the Android application, Streamscape Media's Android build, and the central synthetic smoke fixture. Consumers remain responsible for the meaning of their tasks and product assertions.

The `synthetic-smoke` wrapper mode has one fixed internal behavior: after exact wrapper/distribution version verification, execution invokes `:verifyToolchainSmoke`. The task belongs to the issue-owned synthetic fixture and cannot be selected or replaced by a caller. Other wrapper modes receive no implicit task and continue to execute only their resolved contract command sequence.

## Runtime isolation

Before execution, the exact caller worktree must equal the admitted SHA and have no tracked or untracked changes. Source is copied without following symlinks into registered disposable state. Execution never mutates the caller checkout.

The runtime creates private mode-0700 locations for home, temporary files, Gradle state, Android user state, and logs beneath the registered Android state root. Java, Javac, SDK manager, Gradle wrapper, and Gradle tasks all receive those same paths. Inherited host `HOME` and `TMPDIR` values are not part of the execution environment. `GRADLE_OPTS` disables the daemon and enforces UTF-8. The checked-in wrapper receives `--no-daemon` on every invocation.

`GRADLE_USER_HOME` remains the workflow-scoped writable Gradle home. The strict runtime may additionally forward `GRADLE_RO_DEP_CACHE` only when the runner supplied exactly `/opt/gradle-ro-cache`, that fixed path is a real non-symlink directory, and it does not alias the writable home. Absence of the fixed mount degrades to ordinary cold dependency resolution. Any other non-empty path fails closed. No token, arbitrary inherited HOME state, GitHub environment, or caller-selected cache path is forwarded. Gradle's native read-only dependency-cache behavior therefore accelerates portable dependency lookup while misses continue to populate only the private job home.

JDK/Javac and SDK package inventory are verified before Gradle. Wrapper properties are parsed without shell evaluation. The contract names the launcher path and blob identity, properties path and blob identity, and, when a standard Gradle wrapper uses one, JAR path and blob identity as independent fields relative to the reviewed working directory. Distribution URL, checksum, every declared component identity, and `Gradle <version>` output are verified against the contract; a JAR digest can never satisfy launcher verification. Streamscape Media's checked-in launcher and IPTV Android's standard wrapper have distinct reviewed wrapper modes.

The central synthetic fixture is the only mode that obtains Gradle at runtime. Its checked-in launcher contains a fixed official Gradle `9.6.1` HTTPS URL and SHA-256, downloads beneath isolated `GRADLE_USER_HOME`, verifies the digest before extraction, and performs bounded no-follow extraction that rejects traversal, duplicate destinations, unsupported members, excessive member counts, and excessive expanded size. It then executes the fixed synthetic task through the installed binary. This does not install a system package, modify the host, use sudo, or create state outside the registered root. Consumer profiles continue to use their own checked-in wrapper or launcher contracts.

## Same-executor performance evidence

The CIW Android execute phase starts one lightweight resource sampler around the existing source-copy/toolchain/Gradle/script execution. It does not wrap Gradle in another shell/process, change task argv, change timeouts, or allocate another Actions job. Gradle and checked-in script wall times use monotonic-clock deltas around the same calls that already determine success or failure; total execute wall time is measured across the same CIW execution function.

On Linux cgroup v2, the sampler resolves only the current process' unified cgroup from `/proc/self/cgroup` beneath the fixed `/sys/fs/cgroup` root and polls `memory.current` and `pids.current`. Those kernel counters already account for the cgroup and descendants, so the emitted peak is a same-executor cgroup peak observed during the Android execution window rather than a heap-ceiling estimate. POSIX `RUSAGE_CHILDREN` provides a bounded child CPU delta when available. No process command line, environment, PID list, cgroup path, source path, or host identity is emitted.

Measurement support is explicitly optional. Missing/disappearing cgroup files or non-POSIX child CPU accounting produce JSON `null` and `resource_measurement: unavailable`; they do not convert successful product work into a telemetry failure and do not fabricate zero usage. The sampler context manager always stops under exception unwinding and returns `False`, so the original child exception remains authoritative.

## Trusted Gradle seed promotion

Promotion is an internal acceleration path, not a public validation input. The reusable Android workflow declares no `permissions` block that could elevate or narrow the caller's token; it inherits the caller's effective ceiling. The workflow additionally requires the caller event to be a protected `push`, and a bounded authority step detects the runner-provided OIDC request capability without logging or forwarding its token. PRs, manual dispatches, and ordinary work branches therefore skip promotion because their thin callers grant only `contents: read`, while an explicitly authorized protected-branch caller may grant `id-token: write`.

When authority is present, promotion is considered only after authoritative Android execution succeeds and Android copied-source cleanup/residue is already verified. The immutable issue #347 client then reads the **same job-private `GRADLE_USER_HOME`** created by `prepare-workspace`; there is no second mutable Gradle home and no cross-job state transfer.

The client accepts only the exact admitted source SHA. It obtains GitHub OIDC with fixed audience `streamscapetv-gradle-seed-v1`, talks only to the fixed internal promoter endpoint, and admits only portable dependency content beneath `caches/modules-*`. Traversal, symlinks, hardlinks/races, locks, `gc.properties`, transforms, daemon/configuration-cache state, build output, Android SDK, arbitrary HOME state, credentials, signing material, excessive files/bytes, and digest mismatch are rejected by reviewed typed code. Framing includes per-file SHA-256 identity. There is no GitHub Actions cache/artifact, PAT, deploy key, S3, OCI, or caller-selected endpoint fallback.

Promotion is acceleration-only. The workflow marks the seed step `continue-on-error`, so OIDC, network, admission, or promoter failure cannot overturn otherwise-correct Android validation. Security cleanup remains authoritative: the upload action runs marker-bound workspace cleanup under `if: always()`, and the reusable workflow invokes the ordinary immutable workspace cleanup whenever the upload action did not prove `cleanup_verified=true`. Terminal success requires one cleanup proof even though it deliberately does not require upload success.

## Private dependency composition

The Android layer never performs Git authentication or checkout. The reusable workflow invokes `checkout-private-dependency`, then passes only registered-state relative path and primitive verification flags to Android execution. Android verifies the exact detached SHA again, requires no remotes, requires a clean worktree and expected subdirectory, and binds only contract-owned environment variables.

The dependency directory is deleted by terminal registered-workspace cleanup after Android-specific residue checks. A traversal path, symlink, wrong SHA, missing Gradle path, retained remote, retained credential configuration, fork source, or unapproved dependency identifier fails closed.

## Repository source policy

The shared repository scanner remains authoritative. The Android projection does not remove a token rule, suppress an entire directory, or allow an extension class. It selects an exception only when the current repository and validation profile match a checked-in entry and the exact relative path has the exact reviewed Git blob digest. The Streamscape Media playback-lab bootstrap and lifecycle sentinels are individually exact path/blob bindings within the playback-lab exception; the guided-acceptance sentinel remains a separate named entry. Any mutation to a marker, surrounding fixture, path, profile, repository, or file identity restores the normal fail-closed secret finding.

The projected verifier classifies generated-output drift before the general worktree digest so that generated drift remains distinct while the subsequent clean-tree check remains mandatory. Tracked and untracked mutation is represented by a SHA-256 digest of the normalized porcelain rows rather than by raw file names or scan output. Forbidden files, tracked secret-shaped content, and symlink escapes use only normalized repository-relative subjects.