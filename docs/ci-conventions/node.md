# Convention: Node — workflow `ci-node.yml` (with the `release` operation)

A repository chosen for the Node workflow needs **no `.ci` script** for lint, test and build if it
commits `package.json` and `package-lock.json` at the repository root. The workflow runs on
`ubuntu-latest` with Node 22, installs with `npm ci --no-audit --no-fund` and runs the script of the stage:

| Stage (operation) | package.json script | When there is none |
| --- | --- | --- |
| `lint` | `lint`, else `typecheck` | skipped (green, reason in the log) |
| `test` | `test` | skipped |
| `build` | `build` | skipped |
| `release` | none: publishes what `.ci/release.json` declares | fails with the missing file named |

No cache is kept (owner decision: only Gradle, Xcode DerivedData, SwiftPM and Maven are cached).
Transition: a tracked executable `.ci/lint.sh`, `.ci/build.sh` or `.ci/test.sh` wins over the convention.

## Operation `release`

`release` runs lint, test, build and then release in the same run, each as its own job, and each only
if the previous one succeeded. The release stage:

1. reads the root `VERSION` (one tracked SemVer line) and requires the release authority: the request
   is a tag equal to `VERSION` (`is_tag=true`, `ref=<VERSION>`), or `ref=main` with `is_tag=false`;
2. builds every declared image for `linux/amd64` (no registry credential in this step), then lints,
   renders and packages every declared chart with `version` and `appVersion` stamped from `VERSION`;
3. logs in to the private registry (the credential is a step-scoped environment value passed on stdin),
   requires every image tag and chart version to be unused (versions are immutable, an existing one fails
   the run), pushes, reads every image digest and chart package back and compares them.

Names, tags and paths equal the previous path, so deployments need no change:
`git.faruqi.dev/mimranfaruqi/<image>:<VERSION>` and
`oci://git.faruqi.dev/mimranfaruqi/helm-charts/<chart>:<VERSION>`.

### The declaration `.ci/release.json`

A tracked regular JSON file (data, not a script), one to four artifacts, each with exactly these fields:

```json
{
  "schemaVersion": 1,
  "artifacts": [
    {"image": "example-app", "chart": "example-app", "chart_path": "charts/example-app",
     "dockerfile": "Dockerfile", "context": "."},
    {"image": "example-app-support", "chart": "example-app-support", "chart_path": "charts/example-app-support",
     "dockerfile": "docker/support/Dockerfile", "context": "."}
  ]
}
```

`image` and `chart` are lowercase slugs; `chart` must equal `name:` in `chart_path/Chart.yaml`; every path
must stay inside the repository (no symlinks). The source `Chart.yaml` keeps placeholder versions.
Charts with Helm dependencies (`Chart.lock`) are not supported yet and fail closed.

Secrets of the release stage (names): `FORGEJO_REGISTRY_USERNAME`, `FORGEJO_REGISTRY_TOKEN` (registry
write, publish step only), optional `DOCKERHUB_USERNAME` / `DOCKERHUB_READ_TOKEN` (authenticated base
image pulls), plus the common `SOURCE_APP_*`, `TS_OAUTH_*` and `S3_*` secrets.
