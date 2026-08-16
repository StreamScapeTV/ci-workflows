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
8. `src/ci_workflows/ciw_android.py` adapts the named `ciw android validate` command to plan, execute, cleanup, and residue phases.
9. `actions/validate-android/action.yml` is a thin composite adapter.
10. `.github/workflows/reusable-android.yml` owns protected planning, semantic mobile scheduling, exact source/dependency primitives, evidence, and unconditional cleanup.

Workflow YAML does not implement Gradle task selection, repository compatibility, authentication, cleanup traversal, test-filter parsing, source-policy exception selection, or product policy. Those decisions remain in typed code and checked-in contract data.

## Immutable private helper source

Private reusable callers do not clone the private `StreamScapeTV/ci-workflows` repository with the caller-scoped Actions token. `reusable-android.yml` composes reviewed central primitives through independently locked identities from `contracts/action-tool-lock.json`: `validate-android` uses `8c7e16003b56da9f6bd2e20b0b5b78e4bbfaceaf` with release `issue #262 immutable Media audio-output sentinel checkpoint`; `exact-checkout`, `prepare-workspace`, `render-evidence`, and `cleanup-workspace` use `70e08d4ddf8930046632a7135950e924b82e22bf` with release `issue #116 immutable private-action checkpoint`; and `checkout-private-dependency` currently resolves to that same source SHA while retaining its separately recorded release `issue #104 immutable private-action checkpoint`.

Each composite resolves its implementation and contracts relative to its own `GITHUB_ACTION_PATH`, so the called helper source is the immutable action-lock revision rather than a caller PR merge SHA or a second central checkout. The caller cannot select or override any helper revision. The workflow does not accept a central-repository PAT, generic checkout token, mutable ref, or `secrets: inherit`.

The existing optional `private_dependency_token` remains a separate product dependency credential. It is passed only to `checkout-private-dependency` when the protected Android plan selects the reviewed dependency contract; it is never used to retrieve central helper source and never reaches planning, Android execution, evidence, or cleanup actions.

## Trust and runner resolution

The planner derives trust from immutable GitHub event metadata. Only `trusted-pr` and `trusted-exact` are permitted by the Android profiles. Untrusted forks fail before mobile execution, shared cache, private dependency, live backend, signing, device, or privileged state. No public input can select a semantic profile or raw label.

The planner requests semantic `mobile` through the central runner resolver. The execution job consumes only the protected resolver's JSON output. Bare `self-hosted`, hosted labels, Apple profiles, Buildah profiles, composite label mixtures, and caller expressions are excluded.

The `mobile` profile means Android build capacity, not a physical Android device. `device-handoff` produces a data packet for another workflow and has no executable task.

## Contract-owned command model

A public request identifies one repository task profile. Contract resolution requires an exact repository/task/profile/working-directory/wrapper tuple. The resulting command sequence contains only fixed argv arrays. Gradle tasks use strict colon-qualified task syntax and are invoked through the verified wrapper. Consumer hooks must be checked-in `scripts/` files and use fixed arguments.

The only request-derived command value is an optional targeted test selector. Its grammar admits a fully qualified class or method identifier and excludes wildcards, traversal, whitespace, shell operators, hashes, Gradle properties, init scripts, and arbitrary arguments.

Repository-specific values are data, not code branches. The contract currently describes the Android application, Streamscape Media's Android build, and the central synthetic smoke fixture. Consumers remain responsible for the meaning of their tasks and product assertions.

The `synthetic-smoke` wrapper mode has one fixed internal behavior: after exact wrapper/distribution version verification, execution invokes `:verifyToolchainSmoke`. The task belongs to the issue-owned synthetic fixture and cannot be selected or replaced by a caller. Other wrapper modes receive no implicit task and continue to execute only their resolved contract command sequence.

## Runtime isolation

Before execution, the exact caller worktree must equal the admitted SHA and have no tracked or untracked changes. Source is copied without following symlinks into registered disposable state. Execution never mutates the caller checkout.

The runtime creates private mode-0700 locations for home, temporary files, Gradle state, Android user state, and logs beneath the registered Android state root. Java, Javac, SDK manager, Gradle wrapper, and Gradle tasks all receive those same paths. Inherited host `HOME` and `TMPDIR` values are not part of the execution environment. `GRADLE_OPTS` disables the daemon and enforces UTF-8. The checked-in wrapper receives `--no-daemon` on every invocation.

JDK/Javac and SDK package inventory are verified before Gradle. Wrapper properties are parsed without shell evaluation. The contract names the launcher path and blob identity, properties path and blob identity, and, when a standard Gradle wrapper uses one, JAR path and blob identity as independent fields relative to the reviewed working directory. Distribution URL, checksum, every declared component identity, and `Gradle <version>` output are verified against the contract; a JAR digest can never satisfy launcher verification. Streamscape Media's checked-in launcher and IPTV Android's standard wrapper have distinct reviewed wrapper modes.

The central synthetic fixture is the only mode that obtains Gradle at runtime. Its checked-in launcher contains a fixed official Gradle `9.6.1` HTTPS URL and SHA-256, downloads beneath isolated `GRADLE_USER_HOME`, verifies the digest before extraction, and performs bounded no-follow extraction that rejects traversal, duplicate destinations, unsupported members, excessive member counts, and excessive expanded size. It then executes the fixed synthetic task through the installed binary. This does not install a system package, modify the host, use sudo, or create state outside the registered root. Consumer profiles continue to use their own checked-in wrapper or launcher contracts.

## Private dependency composition

The Android layer never performs Git authentication or checkout. The reusable workflow invokes `checkout-private-dependency`, then passes only registered-state relative path and primitive verification flags to Android execution. Android verifies the exact detached SHA again, requires no remotes, requires a clean worktree and expected subdirectory, and binds only contract-owned environment variables.

The dependency directory is deleted by the terminal registered-workspace cleanup after Android-specific residue checks. A traversal path, symlink, wrong SHA, missing Gradle path, retained remote, retained credential configuration, fork source, or unapproved dependency identifier fails closed.

## Repository source policy

The shared repository scanner remains authoritative. The Android projection does not remove a token rule, suppress an entire directory, or allow an extension class. It selects an exception only when the current repository and validation profile match a checked-in entry and the exact relative path has the exact reviewed Git blob digest. The Streamscape Media playback-lab bootstrap and lifecycle sentinels are individually exact path/blob bindings within the playback-lab exception; the guided-acceptance and audio-output sentinels remain separate named entries. Any mutation to a marker, surrounding fixture, path, profile, repository, or file identity restores the normal fail-closed secret finding.

The projected verifier classifies generated-output drift before the general worktree digest so that generated drift remains distinct while the subsequent clean-tree check remains mandatory. Tracked and untracked mutation is represented by a SHA-256 digest of the normalized porcelain rows rather than by raw file names or scan output. Forbidden files, tracked secret-shaped content, and symlink escapes use only normalized repository-relative subjects.

The public facade maps only stable shared rule IDs. Unknown or malformed source-policy state becomes `policy_contract_failed`; artifact rules remain `artifact_policy_failed`; actual worktree mutation alone becomes `dirty_tree`. CIW failure summaries and stderr contain no file content, token text, absolute path, credential-bearing URL, or unbounded scanner transcript.

## Mutation and output controls

Protected files and directories are hashed before and after execution. Room schema directories receive deterministic tree hashes. Git status after execution may contain only paths under the checked-in generated cleanup names; all other tracked or untracked mutation fails.

`assemble-debug` requires the declared debug APK path and rejects names suggesting release or signing. Any AAB found in disposable source fails. Output is never uploaded and is removed. `room-schema` requires unchanged committed schema history. `performance` represents only deterministic checked-in JVM tests, never release or device performance.

Diagnostic exceptions are named contract records, not arbitrary artifact paths. The initial exception permits only bounded logs/XML/Room report forms, forbids packages and key material, and remains absent by default. Shared artifact action pins and exception registration are central contracts rather than Android-owned mutable workflow logic.

## Cleanup invariants

Android cleanup receives only the marker-resolved temporary state root. It removes copied source, Android runtime state, logs, Gradle/Android caches, the synthetic Gradle archive and installation, and generated output with descriptor-style no-follow traversal. The independent terminal workspace cleanup removes the separately registered private dependency. Neither path follows a symlink or accepts a caller deletion path.

The reusable workflow invokes Android cleanup, residue verification, and foundation workspace cleanup independently under `if: always()`. It then verifies the original admitted caller worktree remains exact and clean. Terminal status requires all phases to pass, so cleanup cannot hide an earlier execution failure and an execution success cannot hide cleanup residue.

## Deterministic evidence

Planning output is canonical JSON plus fixed scalar identities. Execution evidence is derived from the exact source SHA, task profile, and stage count and includes no credentials, host paths, device serials, private URLs, SQL, signing material, or arbitrary command text. Logs are bounded to a terminal tail and redact token/password/authorization/secret/keystore values and credential-bearing URLs. Policy failure evidence is limited further to one stable rule plus a normalized relative path or digest.
