# Android completion reusable workflows

Issue #338 adds two canonical opt-in Android validation workflows alongside the
routine `validation.android` API. They preserve existing Android CI capabilities
without adding credentials or release artifacts to ordinary protected-full PR
validation.

## Canonical workflow paths

| Purpose | Public API | Canonical path | Heavy executor |
|---|---|---|---|
| Routine protected/full and targeted Android validation | `validation.android` | `.github/workflows/reusable-android.yml` | one semantic `mobile` executor |
| Credentialed live-service acceptance | `validation.android-live-service` | `.github/workflows/reusable-android-live-service.yml` | one semantic `mobile` executor |
| Unsigned release validation/evidence | `validation.android-release` | `.github/workflows/reusable-android-release.yml` | one semantic `mobile` executor |

Consumers repin these canonical paths to reviewed immutable Central SHAs. There
are no v1/v2/v3 workflow filename variants.

## Live-service acceptance

The live-service API accepts an exact admitted source SHA, one bounded working
directory, one strict checked-in-script plan, and an optional exact private
dependency. The only live-service credentials are generic caller-owned
`service_username` and `service_password` named secrets. Central stores no
product-specific secret names.

The workflow uses a protected general-small planner, then one mobile executor.
That executor performs one exact source checkout, prepares one marker-bound
Gradle workspace with GitHub Actions cache disabled, optionally checks out one
exact private dependency, and invokes only the checked-in script declared by
the validated plan. The product script receives the credentials as
`CIW_SERVICE_USERNAME` and `CIW_SERVICE_PASSWORD`; the execution environment is
reconstructed rather than inherited, so unrelated GitHub tokens and legacy
product credential aliases are not forwarded.

Both generic credentials are required at execution time. A missing value fails
with a stable `service_credentials_missing` code and no credential value appears
in outputs or summaries. The workflow uploads no Actions artifact. Copied source
state and the registered Gradle workspace are cleaned once at the terminal
boundary, followed by residue and exact-source clean checks.

## Unsigned release validation

The release API accepts an exact admitted source SHA, bounded working directory,
checked-in Gradle wrapper, one strict release plan, and an optional exact private
dependency. The plan contains:

- ordered checked-in pre-policy scripts;
- ordered Gradle task groups;
- ordered checked-in post-policy scripts;
- one checked-in size-budget script plus bounded APK/AAB/budget/baseline/output
  paths;
- bounded artifact patterns, kinds, required flags and maximum counts;
- a bounded artifact name and 1–30 day retention.

This preserves the existing Android release-validation order without hard-coding
product tasks in Central. A product caller can request its historical unit,
`lintDebug`, `assembleRelease`, `bundleRelease`, `lintRelease`, release-policy and
size-budget work in the exact order it already uses. All groups share one mobile
executor, one copied source, one dependency checkout and one Gradle workspace.

Artifacts are unsigned validation evidence only. Supported kinds are bounded to
APK, AAB, JSON, HTML and XML. Patterns must stay inside the copied project, may
use a wildcard only in the final basename, cannot traverse, and cannot match
symlinks or more files than the plan allows. Only paths returned by the CIW
validator are converted into the upload list. The reusable workflow contains
exactly one pinned `actions/upload-artifact` step and performs cleanup after the
upload completes.

The workflow accepts no signing key, provisioning material, Play Store
credential, registry credential, release-line mutation authority or deployment
input. It does not publish or distribute the application.

## Shared boundaries

Both completion APIs use semantic `mobile` capacity, JDK 25 and the reviewed
Android SDK capability. Both reuse the existing exact-checkout,
prepare-workspace, private-dependency and cleanup foundations. No GitHub Actions
cache is enabled.

Routine `validation.android` remains separately credential-free and
artifact-light: its public secret list still contains only the optional private
dependency token, and its workflow contains no service credentials or
`upload-artifact` step.

## Acceptance smoke

`.github/workflows/android-completion-smoke.yml` is the repository-owned
exact-head acceptance caller. It runs two independent opt-in mobile jobs because
live-service and release are distinct gates:

- the live job passes synthetic generic credentials to a product-neutral checked-
  in fixture, verifies legacy credential variables and `GITHUB_TOKEN` are absent,
  and executes a Gradle toolchain probe;
- the release job executes pre-policy, four ordered Gradle groups, post-policy,
  size-budget verification, validates four small synthetic unsigned evidence
  files, and uploads exactly one short-retention artifact.

A general-small finalizer verifies both mobile jobs succeeded and that the smoke
run retained exactly that one release artifact. No product repository name,
backend hostname, application ID, real credential or signing material appears in
the smoke contract.
