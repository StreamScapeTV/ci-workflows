# Runner image build and release

`ci-workflows` owns one shared build/publish path for the organization runner images. Image composition stays under `runner-images/<image>/`; Flux owns ARC/K3s deployment, runner labels and resources, Docker sidecars, persistent/shared caching, and the image tag selected for live runners.

## Fixed image family

The shared contract accepts only these image IDs:

- `general` -> `git.faruqi.dev/mimranfaruqi/github-actions-runner-general`
- `mobile` -> `git.faruqi.dev/mimranfaruqi/github-actions-runner-mobile`
- `buildah` -> `git.faruqi.dev/mimranfaruqi/github-actions-runner-buildah`
- `docker` -> `git.faruqi.dev/mimranfaruqi/github-actions-runner-docker`
- `flux-control` -> `git.faruqi.dev/mimranfaruqi/github-actions-runner-flux-control`

Each image provides `runner-images/<image>/Dockerfile`, `runner-images/<image>/smoke.sh`, and `/usr/local/bin/runner-image-smoke` in the built image. The shared builder uses the organization Buildah high-capacity runner, builds the image, executes that image-owned smoke command, and optionally publishes the same local image to the fixed repository.

Callers cannot choose a registry, container engine, runner labels, arbitrary shell command, Kubernetes target, cache backend, or storage path. The workflow does not use GitHub Actions cache and does not create or manage PVs or cache services.

## Repository release

`.github/workflows/runner-images-release.yml` is the thin repository-level release caller. A Git tag event publishes all five images from the exact tagged `ci-workflows` commit using that human-readable Git tag as the OCI tag. Manual dispatch accepts an already-existing Git tag, verifies the tag resolves to the checked-out commit, and rebuilds the same five-image release set.

The Git tag and commit are the release source of truth. `latest` is rejected. Registry-side tag immutability, provenance manifests, digest ledgers, canary state, and rollback state are not acceptance requirements for this workflow. Publication does perform a simple authenticated registry inspection after push so an immediately unreadable tag fails the release job.

The release caller expects repository secrets `RUNNER_REGISTRY_USERNAME` and `RUNNER_REGISTRY_TOKEN`. They are passed only to the internal publish leaf. Authentication state is stored under `RUNNER_TEMP`, removed unconditionally, and never written into the source checkout.

## Pull-request image validation

A runner-image implementation can call `.github/workflows/internal-runner-image.yml` with `publish: false`, the image ID, and the exact pull-request head SHA. This uses the same Buildah build and image-owned smoke path without registry credentials or publication.
