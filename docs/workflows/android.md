# Android validation workflow

`validation.android` version `2.0.0` is the public Android/Gradle validation API implemented by `.github/workflows/reusable-android.yml`. Its stable check name is **CI / Android validation**.

## Build model

One reusable call creates one mobile executor and one Gradle workspace. `protected-full` combines unit tests, lint, assemble, and applicable Gradle-backed KSP/Room/schema tasks into one Gradle invocation so configuration, dependency resolution, and compiled state are reused inside the job. There is no standalone protected-full compile job and no second cache-warming build.

The workflow exposes only Android technology inputs: admitted source SHA, validation scope, working directory, Gradle wrapper path, one bounded validation plan, and optional exact private-dependency coordinates. It has no cache path, cache endpoint, promotion flag, runner selector, signing, release, Docker, Helm, or device input.

## Shared Gradle dependency cache

Every job has a private writable `GRADLE_USER_HOME`. On mobile runners, Central additionally forwards the runner-provided `GRADLE_RO_DEP_CACHE` when it points to `/opt/gradle-ro-cache`. Gradle can therefore reuse dependencies already present in the shared cache while writing misses only to the job-private Gradle home.

If the shared cache is absent, Gradle resolves dependencies normally from the configured repositories. Cache availability is an acceleration, not a correctness requirement.

Routine Android validation requires no GitHub OIDC permission and contains no seed uploader. Cache updates after successful misses are runner/Flux infrastructure work owned by `StreamScapeTV/iptv-android#800` and `StreamScapeTV/flux#327`; they must not require a second Gradle build.

Workspace preparation still uses Central `cache_mode: disabled` because Central does not create a GitHub Actions cache or a shared writable Gradle home. The shared read-only dependency cache is runner infrastructure and is independent from the private job workspace.

## Performance telemetry

Android execution records bounded wall times for the full execute function, Gradle, and any checked-in script phase. On supported Linux runners it also records sampled cgroup peak memory/process count and child CPU time. `test_summary` reports `gradle_dependency_cache_mode` as `read-only-seed` or `cold`, allowing cold/warm measurements without another monitoring job.

## Central helper checkpoints

The reusable workflow uses the checked-in Central helper checkpoints recorded in `contracts/action-tool-lock.json`:

- `StreamScapeTV/ci-workflows/actions/validate-android@a01e29210603dc8b4cb9e31b9b0c926c2ab5cf37` — `issues #344/#346 Android telemetry and Gradle read-only seed checkpoint`.
- `StreamScapeTV/ci-workflows/actions/exact-checkout@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`.
- `StreamScapeTV/ci-workflows/actions/prepare-workspace@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`.
- `StreamScapeTV/ci-workflows/actions/render-evidence@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`.
- `StreamScapeTV/ci-workflows/actions/cleanup-workspace@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #116 immutable private-action checkpoint`.
- `StreamScapeTV/ci-workflows/actions/checkout-private-dependency@70e08d4ddf8930046632a7135950e924b82e22bf` — `issue #104 immutable private-action checkpoint`.

`upload-gradle-seed` is not part of routine Android validation.

## Private dependency and cleanup

When a private dependency is requested, it is checked out once at the exact planned revision and only its verified path is passed into Android execution. The dependency token is confined to that checkout step.

Android copied-source cleanup and residue verification run after execution, then the registered workspace cleanup removes the private writable Gradle home, dependency state, temporary files, and evidence. A final source check verifies the admitted checkout remained exact and clean.

Routine Android validation retains zero GitHub Actions artifacts and does not use `actions/cache`.

## Repository-owned smoke

`.github/workflows/android-validation-smoke.yml` exercises one real synthetic `protected-full` Gradle invocation on mobile capacity and verifies the expected Android/JDK toolchain plus cleanup. Application performance proof belongs in the Android consumer repository after the shared cache is populated.