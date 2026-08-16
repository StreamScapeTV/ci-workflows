# Simple Git-tag product release

`.github/workflows/reusable-release.yml` is the low-ceremony release composition for products that need an image, a Helm chart, or both.

## Authority boundary

The product repository owns its Git tag, release version, Dockerfile/build context, Helm product metadata, optional values/policy selection, and the decision to create a GitHub Release. The existing caller Git tag is the release source of truth: Central checks out `refs/tags/<release_tag>` without persistent credentials and requires that it resolves to the exact `admitted_sha` supplied by the caller.

Central does not create a second immutable-tag authority, release manifest, provenance record, recovery tuple, canary, rollback plan, or deployment handoff for this core workflow.

## Image publication

Set `image_repository` to a private `git.faruqi.dev/...` repository to enable image publication. The image job:

- runs on the trusted Buildah high-capacity runner;
- checks out the exact admitted source;
- validates the Dockerfile/build-context paths remain inside the caller checkout;
- builds Linux amd64 + arm64 into one Buildah manifest;
- authenticates with the two named registry credentials using `--password-stdin`;
- pushes the version tag with `buildah manifest push --all`;
- removes the manifest, local images, auth file, and temporary state;
- returns the ordinary versioned image reference.

There is no mandatory remote digest/read-back proof. Products that need stronger publication verification may use the older advanced OCI APIs separately.

## Helm publication

Set `helm_product_id` to enable chart publication. The release workflow delegates directly to `.github/workflows/reusable-helm-publish.yml` from issue #18, passing the exact admitted source and release version plus optional values/policy selection. The caller-owned `.streamscape/helm-product.json` remains the chart/layout/registry authority.

## GitHub Release

Set `create_github_release: true` to create or update the normal GitHub Release associated with `release_tag`. Only that job receives `contents: write`; image and chart publication remain `contents: read`. Creation uses generated release notes. Replays update the existing release instead of creating a duplicate.

## Non-goals

This workflow does not deploy, reconcile Flux/Kubernetes, upload routine Actions artifacts, use Actions cache, or own product-specific release checks. It also does not remove the deprecated `reusable-tag-image-chart.yml`; that historical bootstrap remains available while product callers migrate to `release.orchestrate`.
