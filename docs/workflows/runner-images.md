# Runner image build and release

`ci-workflows` owns one shared build/publish path for the organization runner images. Image composition stays under `runner-images/<image>/`; Flux owns ARC/K3s deployment, runner labels and resources, Docker sidecars, persistent/shared caching, and the image tag selected for live runners.

## Fixed image family

The shared contract accepts only these image IDs:

- `general` -> `git.faruqi.dev/mimranfaruqi/github-actions-runner-general`
- `mobile` -> `git.faruqi.dev/mimranfaruqi/github-actions-runner-mobile`
- `buildah` -> `git.faruqi.dev/mimranfaruqi/github-actions-runner-buildah`
- `docker` -> `git.faruqi.dev/mimranfaruqi/github-actions-runner-docker`
- `flux-control` -> `git.faruqi.dev/mimranfaruqi/github-actions-runner-flux-control`

Each image provides `runner-images/<image>/Dockerfile`, `runner-images/<image>/smoke.sh`, and `/usr/local/bin/runner-image-smoke` in the built image. The typed resolver owns the fixed source paths and registry destinations. `actions/runner-image` is the single Buildah build/smoke/publish implementation used by both the internal reusable leaf and the repository release workflow.

Callers cannot choose a registry, container engine, runner labels, arbitrary shell command, Kubernetes target, cache backend, or storage path. The shared path uses the organization Buildah high-capacity runner, builds the image, executes the image-owned smoke command, and optionally publishes that same local image. It does not use GitHub Actions cache and does not create or manage PVs or cache services.

## Reusable non-publishing path

`.github/workflows/internal-runner-image.yml` is a shallow `workflow_call` leaf for central/infrastructure callers. It checks out one exact `ci-workflows` SHA, delegates build/smoke/optional publication to the composite action, verifies credential cleanup, and verifies the source checkout remains clean. It does not call another reusable workflow.

A runner-image implementation can call this leaf with `publish: false`, its fixed image ID, and the exact pull-request head SHA. Registry credentials are unnecessary in that mode.

## Repository release

`.github/workflows/runner-images-release.yml` is the thin repository-level release caller. A Git tag event publishes all five images from the exact tagged `ci-workflows` commit using that human-readable Git tag as the OCI tag. Manual dispatch accepts an already-existing Git tag, verifies the tag resolves to the checked-out commit, and rebuilds the same five-image release set.

The release workflow calls the same `actions/runner-image` composite action directly rather than nesting reusable workflows. The Git tag and commit are the release source of truth. `latest` is rejected. Registry-side tag immutability, provenance manifests, digest ledgers, canary state, and rollback state are not acceptance requirements for this workflow. Publication performs a simple authenticated registry inspection after push so an immediately unreadable tag fails the release job.

The release caller expects repository secrets `RUNNER_REGISTRY_USERNAME` and `RUNNER_REGISTRY_TOKEN`. They are passed only into the build/publish action. Authentication state is stored under `RUNNER_TEMP`, removed in the action's unconditional cleanup, and independently checked absent by the calling workflow.
