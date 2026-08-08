# Android validation architecture

## Authority and layers

The Android gate follows the repository's named-function architecture:

1. `contracts/android-validation.json` is the reviewed behavior and compatibility authority.
2. `src/ci_workflows/android_types.py` defines immutable requests, plans, commands, wrappers, results, and stable errors.
3. `src/ci_workflows/android_contract.py` validates the complete contract, derives source trust, parses bounded inputs, and resolves one deterministic plan.
4. `src/ci_workflows/android_execution.py` owns direct process execution, exact toolchain and wrapper verification, source copying, mutation checks, output checks, redaction, and no-follow cleanup.
5. `src/ci_workflows/android.py` applies repository policy before and after one plan.
6. `src/ci_workflows/ciw_android.py` adapts the named `ciw android validate` command to plan, execute, cleanup, and residue phases.
7. `actions/validate-android/action.yml` is a thin composite adapter.
8. `.github/workflows/reusable-android.yml` owns protected planning, semantic mobile scheduling, exact source/dependency primitives, evidence, and unconditional cleanup.

Workflow YAML does not implement Gradle task selection, repository compatibility, authentication, cleanup traversal, test-filter parsing, or product policy. Those decisions remain in typed code and checked-in contract data.

## Trust and runner resolution

The planner derives trust from immutable GitHub event metadata. Only `trusted-pr` and `trusted-exact` are permitted by the Android profiles. Untrusted forks fail before mobile execution, shared cache, private dependency, live backend, signing, device, or privileged state. No public input can select a semantic profile or raw label.

The planner requests semantic `mobile` through the central runner resolver. The execution job consumes only the protected resolver's JSON output. Bare `self-hosted`, hosted labels, Apple profiles, Buildah profiles, composite label mixtures, and caller expressions are excluded.

The `mobile` profile means Android build capacity, not a physical Android device. `device-handoff` produces a data packet for another workflow and has no executable task.

## Contract-owned command model

A public request identifies one repository task profile. Contract resolution requires an exact repository/task/profile/working-directory/wrapper tuple. The resulting command sequence contains only fixed argv arrays. Gradle tasks use strict colon-qualified task syntax and are invoked through the verified wrapper. Consumer hooks must be checked-in `scripts/` files and use fixed arguments.

The only request-derived command value is an optional targeted test selector. Its grammar admits a fully qualified class or method identifier and excludes wildcards, traversal, whitespace, shell operators, hashes, Gradle properties, init scripts, and arbitrary arguments.

Repository-specific values are data, not code branches. The contract currently describes the Android application, Streamscape Media's Android build, and the central synthetic smoke fixture. Consumers remain responsible for the meaning of their tasks and product assertions.

## Runtime isolation

Before execution, the exact caller worktree must equal the admitted SHA and have no tracked or untracked changes. Source is copied without following symlinks into registered disposable state. Execution never mutates the caller checkout.

The runtime exposes only the required host paths and creates private mode-0700 locations for home, temporary files, Gradle state, Android user state, and logs. `GRADLE_OPTS` disables the daemon and enforces UTF-8. The checked-in wrapper receives `--no-daemon` on every invocation. The gate does not provision JDK, SDK, Gradle, container engines, emulators, signing tools, or publication tooling.

JDK/Javac and SDK package inventory are verified before Gradle. Wrapper properties are parsed without shell evaluation. Distribution URL, checksum, Git blob identities, and `Gradle <version>` output are verified against the contract. Streamscape Media's checked-in launcher and IPTV Android's standard wrapper have distinct reviewed wrapper modes.

## Private dependency composition

The Android layer never performs Git authentication or checkout. The reusable workflow invokes `checkout-private-dependency`, then passes only registered-state relative path and primitive verification flags to Android execution. Android verifies the exact detached SHA again, requires no remotes, requires a clean worktree and expected subdirectory, and binds only contract-owned environment variables.

The dependency directory is deleted by the terminal registered-workspace cleanup after Android-specific residue checks. A traversal path, symlink, wrong SHA, missing Gradle path, retained remote, retained credential configuration, fork source, or unapproved dependency identifier fails closed.

## Mutation and output controls

Protected files and directories are hashed before and after execution. Room schema directories receive deterministic tree hashes. Git status after execution may contain only paths under the checked-in generated cleanup names; all other tracked or untracked mutation fails.

`assemble-debug` requires the declared debug APK path and rejects names suggesting release or signing. Any AAB found in disposable source fails. Output is never uploaded and is removed. `room-schema` requires unchanged committed schema history. `performance` represents only deterministic checked-in JVM tests, never release or device performance.

Diagnostic exceptions are named contract records, not arbitrary artifact paths. The initial exception permits only bounded logs/XML/Room report forms, forbids packages and key material, and remains absent by default. Shared artifact action pins and exception registration are central contracts rather than Android-owned mutable workflow logic.

## Cleanup invariants

Android cleanup receives only the marker-resolved temporary state root. It removes copied source, Android runtime state, logs, Gradle/Android caches, and generated output with descriptor-style no-follow traversal. The independent terminal workspace cleanup removes the separately registered private dependency. Neither path follows a symlink or accepts a caller deletion path.

The reusable workflow invokes Android cleanup, residue verification, and foundation workspace cleanup independently under `if: always()`. It then verifies the original admitted caller worktree remains exact and clean. Terminal status requires all phases to pass, so cleanup cannot hide an earlier execution failure and an execution success cannot hide cleanup residue.

## Deterministic evidence

Planning output is canonical JSON plus fixed scalar identities. Execution evidence is derived from the exact source SHA, task profile, and stage count and includes no credentials, host paths, device serials, private URLs, SQL, signing material, or arbitrary command text. Logs are bounded to a terminal tail and redact token/password/authorization/secret/keystore values and credential-bearing URLs.
