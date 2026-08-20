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

## Pull-request validation

`.github/workflows/runner-images-validation.yml` creates one independent `[ubuntu-latest]` matrix job for each of the six images. It calls the shared action with `publish: false`, so validation has no package-write permission.

## Release

`.github/workflows/runner-images-release.yml` triggers for every repository Git tag and may also replay an existing Git tag through manual dispatch. The release job grants only `contents: read` and `packages: write`, checks out the exact tagged commit, and calls the same shared action with `publish: true`.

The exact Git tag is the versioned image tag. `latest` is only the mutable convenience alias. A pre-existing version tag with a different `org.opencontainers.image.revision` is rejected before publication.

Live ARC/Kubernetes deployment remains Flux-owned and is not changed by this workflow.
