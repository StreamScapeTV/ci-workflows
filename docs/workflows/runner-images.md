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

An image may additionally own `runner-images/<image>/prepare_inputs.py` when its Dockerfile needs checksum-verified build-context inputs. The shared action runs that fixed product-local preparer only when it exists, requires it to create a real `.ciw-build-inputs` directory before the build, and removes that generated directory in terminal cleanup. Images without a preparer go directly from planning to build; Central does not own per-product download URLs or tool definitions.

Callers cannot choose a registry, container engine, runner labels, arbitrary shell command, Kubernetes target, cache backend, or storage path. The shared path uses the organization Buildah high-capacity runner, builds the image, executes the image-owned smoke command, and optionally publishes that same local image. It does not use GitHub Actions cache and does not create or manage PVs or cache services.

## Reusable non-publishing path

`.github/workflows/internal-runner-image.yml` is a shallow `workflow_call` leaf for central/infrastructure callers. It checks out one exact `ci-workflows` SHA, delegates build/smoke/optional publication to the composite action, verifies credential cleanup, and verifies the source checkout remains clean. It does not call another reusable workflow.

A runner-image implementation can call this leaf with `publish: false`, its fixed image ID, and the exact pull-request head SHA. Registry credentials are unnecessary in that mode.

## Repository release

`.github/workflows/runner-images-release.yml` is the thin repository-level release caller. **Every repository Git tag** matches the workflow's `push.tags: ["*"]` trigger; no `runner-images-` prefix is required. The exact repository Git tag is passed through unchanged as the OCI tag for all five images built from that tagged `ci-workflows` commit.

For example:

- repository tag `1.0.1` publishes `git.faruqi.dev/mimranfaruqi/github-actions-runner-general:1.0.1` and the corresponding `:1.0.1` tag for the other fixed images;
- repository tag `runner-images-2026.08.19-hotfix` publishes the corresponding `:runner-images-2026.08.19-hotfix` OCI tags.

Manual dispatch accepts an already-existing Git tag, verifies the tag resolves to the checked-out commit, and rebuilds the same five-image release set using that exact tag again.

The release workflow calls the same `actions/runner-image` composite action directly rather than nesting reusable workflows. The Git tag and commit are the release source of truth. `latest` is rejected by the current publication policy; releases publish only the exact approved Git tag. Registry-side tag immutability, provenance manifests, digest ledgers, canary state, and rollback state are not acceptance requirements for this workflow. Publication performs a simple authenticated registry inspection after push so an immediately unreadable tag fails the release job.

The release caller expects repository secrets `RUNNER_REGISTRY_USERNAME` and `RUNNER_REGISTRY_TOKEN`. They are passed only into the build/publish action. Authentication state is stored under `RUNNER_TEMP`, removed in the action's unconditional cleanup, and independently checked absent by the calling workflow.
