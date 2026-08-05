# StreamScapeTV CI Workflows

This private repository owns reusable GitHub Actions orchestration for supported
StreamScapeTV repositories. Consumer repositories keep only triggers, minimum
permissions, bounded product inputs, explicit secrets, and immutable references.

## Exact-tag image and Helm publication

`.github/workflows/reusable-tag-image-chart.yml` is a `workflow_call`-only release
primitive. A consumer tag push invokes it to:

1. check out the exact tagged caller commit;
2. use the exact tag as the release version;
3. build and publish one daemonless `linux/amd64` + `linux/arm64` OCI image;
4. independently read back the image index, platforms, and OCI labels;
5. package the chart with the exact tag as `version` and `appVersion`;
6. publish and independently pull/read back the Helm OCI package;
7. retain zero Actions artifacts and clean all publication state.

It does **not** publish `latest`, create a GitHub Release, run from a branch or
manual event, update production values, deploy, restart workloads, or access a
cluster.

### Thin consumer caller

Production callers must replace `<FULL_CI_WORKFLOWS_COMMIT_SHA>` with the full
immutable commit that contains the reviewed reusable workflow.

```yaml
name: Publish tagged Backend image and chart

on:
  push:
    tags:
      - "*"

permissions:
  actions: read
  contents: read

jobs:
  release:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-tag-image-chart.yml@<FULL_CI_WORKFLOWS_COMMIT_SHA>
    with:
      image_name: iptv-backend
      chart_name: iptv-backend
      chart_path: charts/iptv-backend
      dockerfile_path: Dockerfile
      build_context: .
    secrets:
      registry_username: ${{ secrets.FORGEJO_REGISTRY_USERNAME }}
      registry_token: ${{ secrets.FORGEJO_REGISTRY_TOKEN }}
```

The caller passes only the two named secrets shown above; broad secret inheritance
is prohibited.

### Release any approved commit

The tagged commit must contain the thin caller pinned to a valid central release.

```bash
git tag 1.2.3 <commit>
git push origin 1.2.3
```

Accepted tag names are `MAJOR.MINOR.PATCH` with an optional OCI-safe prerelease
suffix such as `1.2.3-rc.1`. A `v` prefix, build metadata, slash, mutable alias,
or leading zero in a numeric core component is rejected.

Publication is separate from deployment. Flux/Helm selection can independently
choose any immutable published image digest or chart version.
