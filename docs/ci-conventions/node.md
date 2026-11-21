# Convention: Node — workflow `ci-node.yml` (with the `release` operation)

A repository chosen for the Node workflow needs **no `.ci` script** for lint, test and build if it
commits `package.json` and `package-lock.json` at the repository root. The workflow runs on
`ubuntu-latest` with the Node version of the tracked `.nvmrc` (a plain version such as `26.7.0` or `v22`; Node 22 when there is none), installs with `npm ci --no-audit --no-fund` and runs the script of the stage:

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

## Where the output goes

The tool output of every stage is written only to the S3 stage log (`logs/<repo>/<request_id>/<stage>/<seq>.log`, `final.log`) and never to the Actions log of this repository. The Actions step shows only status lines: `== stage <name> started s3=<prefix> ==` and `== stage <name> finished result=ok|failed|skipped exit=N s3=<prefix> ==`. Tool `::error::` lines stay inside the S3 log. A log stream that cannot start or finish fails the job (the log would be lost).

## One job, parallel stages

All requested stages run in ONE job (`node / stages`) and do not wait for each other. Node: `npm ci` runs once, then `lint`, `test` and `build` run as parallel processes of the same job (each with its own S3 log stream). A stage without a convention is `skipped`. A failed install fails every stage that needed it. A failed `lint` is a warning (reported, findings in the S3 log) and never fails a `test` or `build` request; `test` or `build` failing fails the request; when `lint` itself is the requested operation a failure fails it. With transition scripts (`.ci/*.sh`) the stages run one after the other, build first, and a failed stage never stops the next.
`release` stays its own job after the stages job and needs it to have succeeded (build and test green; lint is ignored).

Order inside the job: when both `lint` and `build` are requested the build starts first and `lint` starts only after the build has finished, because `next build` rewrites `.next/types` and the TypeScript build info that a typecheck (`tsc --noEmit`) reads; the `test` stage stays parallel with both. A failed build does not stop the lint. Python has no such shared output and runs its stages in parallel.
