# Runner image build and release

`ci-workflows` owns one shared build/smoke/publication implementation for the organization runner images. Image composition stays under `runner-images/<image>/`; Flux owns ARC/K3s deployment, runner labels and resources, persistent/shared caching, and the immutable image reference selected for live runners. This workflow does not modify Flux deployment state.

## Fixed image family

The shared contract accepts only these image IDs and public GHCR repositories:

- `general` -> `ghcr.io/streamscapetv/github-actions-runner-general`
- `mobile` -> `ghcr.io/streamscapetv/github-actions-runner-mobile`
- `buildah` -> `ghcr.io/streamscapetv/github-actions-runner-buildah`
- `service` -> `ghcr.io/streamscapetv/github-actions-runner-service`
- `docker` -> `ghcr.io/streamscapetv/github-actions-runner-docker`
- `flux-control` -> `ghcr.io/streamscapetv/github-actions-runner-flux-control`

Each image provides `runner-images/<image>/Dockerfile`, `runner-images/<image>/smoke.sh`, and `/usr/local/bin/runner-image-smoke` in the built image. The typed resolver owns the fixed source paths and GHCR destinations. `actions/runner-image` is the single Docker build/smoke/publish implementation used by both the internal reusable leaf and the repository validation/release workflows.

An image may additionally own `runner-images/<image>/prepare_inputs.py` when its Dockerfile needs checksum-verified build-context inputs. The shared action runs that fixed product-local preparer only when it exists, requires it to create a real `.ciw-build-inputs` directory before the build, and removes that generated directory in terminal cleanup. Images without a preparer go directly from planning to build; Central does not own per-product download URLs or tool definitions.

Callers cannot choose a registry, runner label, arbitrary shell command, Kubernetes target, cache backend, or storage path. Runner-image build and smoke use a fresh standard GitHub-hosted `ubuntu-latest` VM. The action verifies `RUNNER_ENVIRONMENT=github-hosted`, Linux/x64, Docker, and Buildx before it builds. It does not fall back to organization ARC capacity when hosted capacity or disk is insufficient.

The action builds each image exactly once with Docker, adds `org.opencontainers.image.source=https://github.com/StreamScapeTV/ci-workflows` and the exact `org.opencontainers.image.revision`, measures image size, largest uncompressed layer, workspace free space, and Docker-root free space, then runs the image-owned smoke command against that exact local image. No Actions cache or runner-image archive artifact is used.

## Hosted feasibility and GHCR limits

Every validation/release matrix entry records its hosted feasibility measurements in the job summary. Layer measurement fails closed before release when a layer reaches GitHub Container Registry's 10 GB limit; Central reserves additional headroom and reports a bounded blocker rather than changing builders or registries.

GitHub currently documents a 10-minute timeout for an individual Container Registry layer upload. Central does not reinterpret that as a ten-minute timeout for the entire multi-layer push. Docker commands remain bounded by the workflow/job timeout while GitHub enforces its registry-side layer constraint.

## Reusable internal leaf

`.github/workflows/internal-runner-image.yml` is a shallow `workflow_call` leaf for central/infrastructure callers. It checks out one exact `ci-workflows` SHA, runs on `ubuntu-latest`, delegates build/smoke/optional GHCR publication to the composite action, and verifies the source checkout remains clean. It does not call another reusable workflow and exposes no registry credential input.

Because GitHub permissions cannot be made conditional on a boolean workflow input, this optional-publication internal leaf grants `contents: read` and `packages: write`. Non-publishing pull-request validation does not use this leaf: `.github/workflows/runner-images-validation.yml` calls the local action directly with only `contents: read` and `publish: false`.

## Pull-request validation

`.github/workflows/runner-images-validation.yml` validates all six current images independently. The matrix creates one fresh `ubuntu-latest` job per image, checks out the exact pull-request head, builds once, runs the image-owned smoke against the same local image, records hosted feasibility metrics, and verifies the source tree remains exact and clean.

Validation has no `packages: write`, registry token, private-registry secret, cache, or artifact upload. A failure for one image does not silently move that image to ARC/Buildah capacity.

## Repository release

`.github/workflows/runner-images-release.yml` is the thin repository-level release caller. **Every repository Git tag** matches the workflow's `push.tags: ["*"]` trigger; no `runner-images-` prefix is required. Manual dispatch accepts an already-existing Git tag. In both cases the resolve job checks out the exact tag, verifies its commit identity, and emits the complete six-image release family.

For example, repository tag `1.0` publishes each fixed image as:

- `ghcr.io/streamscapetv/github-actions-runner-<image>:1.0`
- `ghcr.io/streamscapetv/github-actions-runner-<image>:latest`

The exact Git tag remains the immutable/versioned release authority. `latest` is only a mutable convenience alias and is not accepted as the repository release tag itself.

Each release matrix entry runs on a fresh `ubuntu-latest` VM with only `contents: read` and `packages: write`. It checks out the exact tagged commit, builds the image once, smokes that exact local image, and only then authenticates to `ghcr.io` using the repository `GITHUB_TOKEN`. There is no PAT or private-registry credential surface.

For a new version tag, the already-smoked local image is tagged and pushed as the version and `latest`; the publisher does not rebuild. If the immutable version already exists, replay succeeds only when both the recorded source revision and image config digest match the newly smoke-tested local image. The version tag is never overwritten; `latest` is moved by copying the exact existing manifest with Buildx `--prefer-index=false`.

After publication, Central independently resolves the versioned and `latest` manifest digests and requires equality. It then drops GHCR authentication, removes the authenticated Docker config, and repeats both manifest reads anonymously. Release succeeds only when the public references remain anonymously readable and match the expected digest.

Runner-image releases retain zero routine GitHub Actions artifacts. Public GHCR is the only runner-image publication target in this workflow. Live Flux runner references are updated separately through reviewed Flux deployment work.
