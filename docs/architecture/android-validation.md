# Android validation architecture

## Authority and layers

The Android gate follows the repository's named-function architecture:

1. `contracts/android-validation.json` is the reviewed behavior and compatibility authority.
2. `contracts/android-source-policy.json` is the narrow Android projection for stable policy failure mapping and exact synthetic-marker exceptions.
3. `src/ci_workflows/android_types.py` defines immutable requests, plans, commands, wrappers, results, stable errors, and bounded policy diagnostics.
4. `src/ci_workflows/android_contract.py` validates the complete validation contract, derives source trust, parses bounded inputs, and resolves one deterministic plan.
5. `src/ci_workflows/android_policy.py` preserves shared repository policy while applying only exact repository/profile/path/rule/blob exceptions and safe failure subjects.
6. `src/ci_workflows/android_execution.py` owns direct process execution, exact toolchain/wrapper verification, source copying, mutation checks, output checks, redaction, and no-follow cleanup.
7. `src/ci_workflows/android.py` applies projected repository policy before and after one plan and maps findings to stable Android codes.
8. `src/ci_workflows/ciw_android.py` adapts `ciw android validate` to plan, execute, cleanup, and residue phases and forwards only the fixed runner-owned read-only Gradle dependency seed path when present.
9. `src/ci_workflows/android_resource_metrics.py` samples bounded same-executor wall/CPU/cgroup evidence without changing child execution.
10. `actions/validate-android/action.yml` is a thin composite adapter.
11. `actions/upload-gradle-seed/action.yml` is the bounded OIDC + portable Gradle-module promotion adapter; it owns no product task selection and no caller-selected endpoint.
12. `.github/workflows/reusable-android.yml` is the explicit read-only validation entry point.
13. `.github/workflows/reusable-android-seed-warm.yml` is the explicit OIDC-capable protected-push validation/warming entry point.

The two public workflows share the same Android planning/execution primitives and input shape. A consumer selects exactly one of them for an event. They are not two stages of one validation and do not duplicate a Gradle task graph.

Workflow YAML does not implement Gradle task selection, repository compatibility, authentication policy, cleanup traversal, test-filter parsing, source-policy exception selection, product policy, cache candidate filtering/framing, or resource-monitoring infrastructure. Those decisions remain in typed code and checked-in contract data.

## Immutable private helper source

Private reusable callers do not clone the private `StreamScapeTV/ci-workflows` repository with caller-scoped credentials. The Android reusables compose reviewed central primitives through independently locked identities from `contracts/action-tool-lock.json`:

- `validate-android` uses `a01e29210603dc8b4cb9e31b9b0c926c2ab5cf37` with release `issues #344/#346 Android telemetry and Gradle read-only seed checkpoint`.
- `upload-gradle-seed` uses `7a0977db839468aac24448831a9a0ffd97b3067b` with release `issue #347 trusted Gradle seed client` and appears only in the seed-warm reusable.
- `exact-checkout`, `prepare-workspace`, `render-evidence`, and `cleanup-workspace` use `70e08d4ddf8930046632a7135950e924b82e22bf` with release `issue #116 immutable private-action checkpoint`.
- `checkout-private-dependency` uses `70e08d4ddf8930046632a7135950e924b82e22bf` while retaining release `issue #104 immutable private-action checkpoint`.

Each composite resolves implementation/contracts relative to its immutable `GITHUB_ACTION_PATH`. The caller cannot select or override helper revisions and neither workflow accepts a Central PAT, generic checkout token, mutable helper ref, cache endpoint/path, or `secrets: inherit`.

The optional `private_dependency_token` remains a separate product dependency credential and reaches only `checkout-private-dependency`.

## Trust, permissions, and runner resolution

The Android planner derives source trust from immutable GitHub event metadata. Only reviewed trusted PR/exact source modes are admitted by Android profiles; untrusted fork source fails before mobile execution, private dependency use, or shared cache interaction.

The execution job requests semantic `mobile` capacity (`[linux, amd64, mobile]`). Callers cannot select arbitrary hosts/labels.

GitHub reusable workflows cannot elevate a caller token. Central therefore represents the permission split as two public APIs rather than one workflow with optional OIDC:

- `validation.android` / `reusable-android.yml` declares exactly `contents: read`.
- `validation.android-seed-warm` / `reusable-android-seed-warm.yml` declares exactly `contents: read` + `id-token: write`.

The seed-warm API is contract-limited to push/workflow-call usage; the product caller further restricts it to its protected integration-branch push. PR/manual/work-branch callers use `validation.android` and cannot acquire OIDC. No public `promote_gradle_seed` input exists.

## Contract-owned command model

A public request identifies one repository task profile. Contract resolution requires an exact repository/task/profile/working-directory/wrapper tuple. The resulting command sequence contains fixed argv arrays. Gradle tasks use strict colon-qualified task syntax and execute through the verified wrapper. Consumer hooks are checked-in `scripts/` files with fixed arguments.

The only request-derived command value is an optional targeted test selector with a bounded fully-qualified class/method grammar. Wildcards, traversal, whitespace, shell operators, Gradle properties, init scripts, and arbitrary arguments are excluded.

Repository-specific values are data, not code branches. Consumers own the meaning of product tasks/assertions.

## Runtime isolation and read-only dependency reuse

Before execution, the exact caller worktree must equal the admitted SHA and be clean. Source is copied without following symlinks into registered disposable state; execution never mutates the caller checkout.

The runtime creates private mode-0700 home/temp/Gradle/Android/log locations beneath registered state. `GRADLE_USER_HOME` is always workflow-scoped and writable only by that job.

The strict runtime may additionally forward `GRADLE_RO_DEP_CACHE` only when the runner supplied exactly `/opt/gradle-ro-cache`, that path is a real non-symlink directory, and it does not alias the writable home. Absence of the fixed mount degrades to ordinary cold dependency resolution; any other non-empty path fails closed. Gradle's native read-only dependency-cache behavior accelerates portable dependency lookup while misses populate only the private job home.

JDK/Javac and SDK package inventory are verified before Gradle. Wrapper launcher/properties/JAR identities are independently checked against the Android contract; distribution URL/checksum and `Gradle <version>` output are verified. Streamscape Media and IPTV Android retain their reviewed distinct wrapper modes.

## One-executor protected-full model

Both Android entry points run one heavy mobile job and one private workspace per call. `protected-full` flattens unit + lint + assemble + Gradle-backed KSP/Room/schema tasks into one `run_gradle_tasks` invocation. A checked-in schema script, when selected, executes afterward in the same copied source/workspace.

The protected product push calls the seed-warm reusable **instead of** the routine reusable, so warming introduces no second executor, checkout, Gradle home, or product task graph.

## Same-executor performance evidence

The CIW execute phase samples bounded wall/CPU/cgroup metrics around the existing execution path. It creates no monitoring service/job and does not change task argv/timeouts. `test_summary` can distinguish complete execute wall time, Gradle/script wall time, child CPU time, sampled cgroup peak memory/process count, and `gradle_dependency_cache_mode` (`read-only-seed` or `cold`). Unsupported resource metrics are explicit JSON `null`, never fabricated zero.

Evidence contains no process command lines, environment values, credentials, arbitrary host paths, or raw cgroup/process listings.

## Trusted Gradle seed promotion

Promotion exists only in `validation.android-seed-warm`. The workflow explicitly requests the `validation-oidc` permission profile (`contents: read` + `id-token: write`) and still requires the actual event to be a protected `push`. A bounded authority step requires GitHub's OIDC request URL/token to exist without logging or forwarding them.

Promotion occurs only after authoritative Android execution succeeds and Android copied-source cleanup/residue is verified. The immutable #347 client then reads portable `caches/modules-*` from the **same job-private `GRADLE_USER_HOME` populated by that validation**. There is no second mutable Gradle home and no cross-job artifact/cache transfer.

The client fixes audience `streamscapetv-gradle-seed-v1`, internal promoter endpoint, exact source SHA, candidate-path policy, file/hash bounds, and per-file SHA-256 framing. Traversal, symlinks, hardlinks/races, locks, `gc.properties`, transforms, daemon/configuration-cache state, build output, Android SDK, arbitrary HOME state, credentials, signing material, excessive files/bytes, and digest mismatch are rejected. There is no GitHub Actions cache/artifact, PAT, deploy key, S3, OCI, or caller-selected endpoint fallback.

Promotion is acceleration-only. The uploader runs `continue-on-error`; OIDC/network/promoter failure cannot overturn otherwise-correct Android validation. Cleanup remains authoritative: uploader cleanup is marker-bound, and ordinary immutable workspace cleanup runs whenever upload did not prove `cleanup_verified=true`. Terminal success requires one verified cleanup path.

## Private dependency composition

Neither Android workflow performs ad-hoc Git authentication. `checkout-private-dependency` checks out exact dependency SHA into registered state, detaches HEAD, erases remotes/credential config, and exposes only the verified dependency subdirectory. The Android adapter revalidates repository/id/SHA/subdirectory/credential-erasure state before product execution.

## Repository source policy

The shared repository scanner remains authoritative. The Android projection selects exceptions only by exact repository/profile/path/rule/blob identity. Any mutation to a reviewed sentinel/path/blob restores the normal fail-closed finding.

Generated-output drift remains distinct from general dirty-tree failure; tracked/untracked mutation evidence uses bounded normalized digests rather than raw file content.

## Mutation, cleanup, and output controls

Protected paths are hashed before/after execution. Room schema directories use deterministic tree hashes. Unapproved tracked/untracked mutation fails.

Android copied-source cleanup runs once, followed by an independent residue proof. Routine validation then runs ordinary marker-bound workspace cleanup. Seed-warm validation permits the immutable uploader to perform marker-bound cleanup and falls back to ordinary cleanup if `cleanup_verified=true` was not produced. Runner-owned read-only seed state is outside job cleanup.

The workflow then verifies the original admitted checkout remains exact/clean. Terminal success requires plan, checkout, workspace, optional dependency, authoritative execution, Android cleanup/residue, one verified workspace cleanup path, and source cleanliness. Upload success itself is deliberately non-authoritative.

Routine validation retains zero GitHub Actions artifacts; the seed-warm path also introduces no Actions cache/artifact transport.

## Deterministic evidence

Planning output is canonical JSON plus fixed scalar identities. Successful execution evidence contains bounded technology facts only. Failure evidence uses stable error codes and bounded/redacted terminal diagnostics; credentials, host paths, private URLs, signing material, arbitrary command text, environment values, and raw process listings are excluded.
