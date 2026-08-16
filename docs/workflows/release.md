# Simple Git-tag product release

`.github/workflows/reusable-release.yml` is the low-ceremony release composition for the IPTV backend image + Helm chart release.

## Authority boundary

The product repository owns its Git tag, release version, image name, Dockerfile/build context, Helm product metadata, optional values/policy selection, and whether a normal GitHub Release should also be created. The existing caller Git tag is the release source of truth: Central checks out `refs/tags/<release_tag>` without persistent credentials and requires that it resolves to the exact `admitted_sha` supplied by the caller.

Central does not create a second immutable-tag authority, release manifest, provenance record, recovery tuple, canary, rollback plan, or deployment handoff for this core workflow. Issue #19 has one explicit harness exception for the retired mandatory remote-read-back rule; exact source/tag admission, bounded publication, cleanup, and normal product ownership remain enforced.

The public inputs deliberately reuse the existing shared catalog: `release_tag`, `release_version`, `admitted_sha`, `image_name`, `product_id`, `dockerfile_path`, `build_context`, `values_profile`, `policy_path`, and `operation`. Use `operation: publish` for image + chart publication only, or `operation: publish-with-github-release` to also create/update the normal GitHub Release.

## Image publication

`image_name` is the caller-owned image name below the private `git.faruqi.dev/mimranfaruqi` namespace. The image job:

- runs on the trusted Buildah high-capacity runner;
- checks out the exact admitted source;
- validates the Dockerfile/build-context paths remain inside the caller checkout;
- builds Linux amd64 + arm64 into one Buildah manifest;
- authenticates with the two named registry credentials using `--password-stdin`;
- pushes `git.faruqi.dev/mimranfaruqi/<image_name>:<release_version>` with `buildah manifest push --all`;
- removes the manifest, local images, auth file, and temporary state in a separate always-run cleanup step;
- returns the ordinary versioned image reference.

There is no mandatory remote digest/read-back proof. Products that need stronger publication verification may use the older advanced OCI APIs separately.

## Helm publication

`product_id` is the caller-owned Helm product identity from `.streamscape/helm-product.json`. To keep public reusable-workflow depth at one, `release.orchestrate` does not nest the #18 reusable workflow. Instead its chart job uses the same reviewed issue #18 `publish-helm` action/runtime checkpoint directly, with exact caller checkout, isolated workspace state, cleanup, residue, and clean-source verification. The caller-owned manifest remains the chart/layout/registry authority.

## GitHub Release

Use `operation: publish-with-github-release` to create or update the normal GitHub Release associated with `release_tag`. The release-orchestration permission profile already requires the caller to grant `actions: read` and `contents: write`; individual image/chart jobs lower themselves to `contents: read`, while only the GitHub Release job consumes `contents: write`. The bounded implementation uses `curl` + `jq`, generated release notes on creation, update-on-replay semantics, and a separate always-run response-file cleanup step.

## Results

The reusable workflow returns `result`, `tag_name`, `version`, `source_sha`, `image_reference`, and `chart_digest`. These are ordinary product-release results, not a release manifest or provenance ledger.

## Non-goals

This workflow does not deploy, reconcile Flux/Kubernetes, upload routine Actions artifacts, use Actions cache, or own product-specific release checks. It also does not remove the deprecated `reusable-tag-image-chart.yml`; that historical bootstrap remains available while the IPTV backend migrates to `release.orchestrate`.
