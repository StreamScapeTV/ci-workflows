# Native CMake validation

Public API: `validation.native` (`.github/workflows/reusable-native.yml`).

This reusable provides ordinary product-neutral CMake validation on Central-owned ordinary Linux capacity. It performs one exact caller-source checkout, prepares one marker-bound `native` workspace, and runs configure, build, and a bounded CMake test target against one isolated build tree. The build tree lives under Central workspace state rather than inside the caller checkout, so source cleanliness can be verified after cleanup.

## Caller-owned inputs

The public surface is intentionally small: `admitted_sha`, optional `working_directory`, and required `validation_plan_json`. The plan is a bounded JSON object whose allowed keys are `definitions`, `configure_options`, `generator`, `build_target`, `build_configuration`, `build_options`, `test_target`, `test_options`, and `jobs`. Unknown keys and invalid types fail closed before CMake execution; the plan is capped at 16 KiB, option collections at 128 entries, and parallelism at 1 through 64.

The default test target is `test`, which is the conventional CMake/CTest target. A repository may select another bounded CMake target when its project exposes tests differently. Central does not inject product-specific commands or assertions. Product names, arbitrary shell commands, runner labels, container engines, cache identities, credentials, publication settings, or deployment authority are not part of this API.

## Workspace and cleanup

Runner selection is Central-owned; consumers never supply a runner label or toolchain location. The selected ordinary native validation capacity must provide CMake, a C/C++ compiler, and its supported build backend. The reusable verifies runner-provided CMake before caller source execution and does not install host packages. GitHub Actions cache is disabled.

The execution sequence is deliberately single-workspace:

1. verify runner-provided CMake;
2. check out the exact admitted source once;
3. prepare the marker-bound `native` workspace once;
4. configure the CMake project into `$CI_WORKFLOW_ROOT/tmp/native-cmake-build`;
5. build the same tree;
6. execute the bounded test target against the same tree;
7. run workspace cleanup under `if: always()`;
8. verify the exact checkout SHA and a clean Git tree under `if: always()`.

No routine artifacts are uploaded. The reusable has `contents: read` only and carries no registry, OIDC, signing, attestation, provenance, deployment, Kubernetes, or physical-device authority.

## Example

During active Central development, repository consumers call the public reusable at `@main` as an ordinary shared-library reference. Full commit SHAs and future compatibility tags remain supported reference forms, but they are not a per-product bootstrap or registration requirement.

```yaml
jobs:
  native:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-native.yml@main
    with:
      admitted_sha: ${{ needs.source.outputs.source_sha }}
      working_directory: native
      validation_plan_json: >-
        {"definitions":{"BUILD_TESTING":"ON"},"build_target":"all","test_target":"test","jobs":2}
```

Source admission remains caller-owned and must supply the exact admitted SHA. The consumer owns its triggers, paths, ref-scoped concurrency, and bounded native project configuration around this call.
