# Hosted runner-image build and public GHCR publication

Issue: #405  
Policy verification date: 2026-08-20

Runner-image build, image-owned smoke validation, and publication use standard GitHub-hosted Linux capacity after `ci-workflows` became public. Organization ARC capacity is not a fallback for this workload.

## Platform policy snapshot

GitHub's current public documentation states:

- standard public-repository `ubuntu-latest` x64 runners provide 4 vCPU, 16 GB RAM, and 14 GB SSD;
- standard GitHub-hosted Actions usage is free for public repositories;
- public GitHub Packages are free;
- Container Registry storage and bandwidth are currently free, with GitHub stating that it will give at least one month of notice before changing that Container Registry policy;
- public Container Registry packages support anonymous pulls;
- Container Registry layers are limited to 10 GB and layer uploads have a 10-minute timeout.

References:

- <https://docs.github.com/en/actions/reference/runners/github-hosted-runners>
- <https://docs.github.com/en/billing/concepts/product-billing/github-actions>
- <https://docs.github.com/en/billing/concepts/product-billing/github-packages>
- <https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry>

These are current platform terms and limits, not repository guarantees. A future GitHub policy change requires a separate reviewed decision rather than an implicit capacity or registry fallback.

## Build boundary

Each runner image is one independent matrix job on `ubuntu-latest`. The shared `actions/runner-image` action requires the runtime `RUNNER_ENVIRONMENT` value to be `github-hosted`, Linux, and x64 before invoking Docker. This protects the policy even if workflow scheduling is accidentally changed later.

The action:

1. resolves the fixed image ID, context, Dockerfile, smoke command, and release references through the existing runner-image contract;
2. prepares only the image-owned optional build input directory;
3. builds the image once with Docker for `linux/amd64` and no build cache;
4. stamps the exact `ci-workflows` source SHA into `org.opencontainers.image.revision` and the repository URL into `org.opencontainers.image.source`;
5. records Docker image size, largest uncompressed Docker layer, workspace free bytes, and Docker-root free bytes;
6. rejects a largest uncompressed layer above 9.5 GB, retaining safety headroom below GHCR's documented 10 GB layer limit;
7. runs `/usr/local/bin/runner-image-smoke` from that exact local image as its configured image user;
8. optionally publishes that same already-smoked local image; and
9. removes the local image, optional build inputs, and Docker authentication state under terminal cleanup.

A build that does not fit the standard hosted VM, reaches the layer headroom boundary, or cannot publish within GHCR platform limits fails with bounded evidence. It never silently moves to Buildah/ARC or a larger organization runner.

## GHCR-only publication

Runner images use fixed public repositories under:

`ghcr.io/streamscapetv/github-actions-runner-<image>`

`git.faruqi.dev` is not part of the runner-image release path after #405. There is no private-registry mirror, private-registry credential, registry-to-registry copy, or multi-gigabyte Actions artifact handoff.

A tagged runner-image release grants only `contents: read` and `packages: write`. The publish step authenticates to `ghcr.io` with the workflow's built-in `GITHUB_TOKEN`; no PAT or private registry secret is accepted by the runner-image action.

The versioned release tag remains immutable source authority. `latest` remains a mutable deployment convenience alias. Before writing a pre-existing version tag, Central reads its OCI source-revision label and fails if it names a different source SHA. Replays with the same source must also reproduce the existing manifest digest or fail as an immutable-content conflict.

After publishing the version and `latest` aliases, Central requires both to resolve to the same manifest digest. It then logs out, removes the isolated Docker credential directory, and resolves both manifests again without credentials. Release success therefore proves public/anonymous GHCR read-back instead of assuming package visibility from repository visibility.

## Artifacts and cache

Runner-image validation and release retain zero routine GitHub Actions artifacts. Image content moves directly from the hosted Docker daemon to GHCR in the same job; a 4 GB image is not serialized into Actions artifact storage.

No GitHub Actions dependency cache is introduced. Fresh hosted VMs intentionally provide isolated image builds. Product/ARC dependency caching remains outside this runner-image publication path.
