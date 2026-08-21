# Runner image build and release

`ci-workflows` owns one shared runner-image action: `actions/runner-image`. The workflows only choose the fixed image ID and whether publication is enabled.

## Images and registry

The fixed image family is:

- `general`
- `mobile`
- `buildah`
- `service`
- `docker`
- `flux-control`

Each image publishes only to:

`ghcr.io/streamscapetv/github-actions-runner-<image>`

`git.faruqi.dev` and private runner-registry credentials are not part of this path.

## Build and smoke

Runner-image validation and release use standard GitHub-hosted `[ubuntu-latest]` jobs. The shared action uses the Buildah and Skopeo tools already provided by that runner image.

For each image the action:

1. resolves the fixed source directory and GHCR reference;
2. builds the image once with Buildah;
3. runs the image-owned `/usr/local/bin/runner-image-smoke` from that same local image;
4. optionally logs in to GHCR with the repository `GITHUB_TOKEN` and pushes the version plus `latest`;
5. verifies the version and `latest` digests match and are anonymously readable; and
6. performs terminal cleanup under `if: always()`.

There is no Docker/Buildx implementation, no private-registry mirror, and no GitHub Actions image artifact handoff.

### Mobile Android/Flutter native-build contract

The Mobile image is self-contained for the checked-in Flutter Android APK contract. Alongside the pinned Android platforms, build tools and NDK, image construction installs the fixed stable Android SDK package `cmake;3.22.1`. The owner-authorized Android SDK license fingerprints needed by that package are checked into `runner-images/mobile/toolchain.lock.json` and materialized only while the image is being constructed; consumer jobs do not run `sdkmanager --licenses`, interactive acceptance, or CMake/SDK installation.

The resulting `${ANDROID_SDK_ROOT}/cmake/3.22.1` package contains both CMake and its Ninja backend. The CMake package and Android license state are read-only to the non-root runner user. The lightweight Docker build-phase smoke verifies the fixed toolchain without restoring a project. The shared runner-image action then executes the finished image's full smoke, which creates a fresh Flutter Android project, performs normal isolated Pub/Gradle dependency restore, and runs `flutter build apk --debug` without disabling Flutter native assets. The smoke fails if the APK is missing, if CMake/Ninja changes, or if the build attempts runtime CMake installation or reports an unaccepted CMake license.

The Flutter smoke keeps `TMPDIR`, `PUB_CACHE`, and `GRADLE_USER_HOME` under its disposable runner work directory. This mirrors the job-private temporary-state boundary used by real runners while ensuring the standalone Buildah smoke does not depend on a runner-managed temp mount and leaves no project dependency or temporary state in the image.

## Pull-request validation

`.github/workflows/runner-images-validation.yml` creates one independent `[ubuntu-latest]` matrix job for each of the six images. It calls the shared action with `publish: false`, so validation has no package-write permission.

## Release

`.github/workflows/runner-images-release.yml` triggers for every repository Git tag and may also replay an existing Git tag through manual dispatch. The release job grants only `contents: read` and `packages: write`, checks out the exact tagged commit, and calls the same shared action with `publish: true`.

The exact Git tag is the versioned image tag. `latest` is only the mutable convenience alias. A pre-existing version tag with a different `org.opencontainers.image.revision` is rejected before publication.

Live ARC/Kubernetes deployment remains Flux-owned and is not changed by this workflow.
