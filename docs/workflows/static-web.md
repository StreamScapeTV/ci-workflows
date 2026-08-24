# Static-web validation workflow

Public API: `validation.static-web` `1.0.0`  
Workflow: `.github/workflows/reusable-static-web.yml`  
Stable check: `CI / Static-web validation`  
Named command: `ciw static-web validate`

The reusable workflow builds and verifies one caller-owned static website from an exact admitted source SHA. Central owns the bounded execution lifecycle, output inspection, semantic general runner selection and cleanup; the caller keeps framework configuration, package restore, build/export behavior and product assertions in checked-in source. The composite adapter is intentionally thin and dispatches through the shared `scripts/ci/ciw.py` registry rather than invoking a feature module as an independent entrypoint.

## Consumer shape

A product caller stays small and owns its event filters and concurrency:

```yaml
jobs:
  static-web:
    uses: StreamScapeTV/ci-workflows/.github/workflows/reusable-static-web.yml@main
    with:
      admitted_sha: ${{ needs.source.outputs.source_sha }}
      working_directory: web
      validation_plan_json: >-
        {"build_script_path":"scripts/build-static.sh","static_output_directory":"dist","expected_files":["index.html"]}
```

`build_script_path` names one executable file committed beneath `working_directory`. Central invokes it directly rather than evaluating command text. The script may run the product-owned package manager/framework commands needed for that repository. The output path is supplied to the script through `CIW_STATIC_OUTPUT_DIRECTORY` and `CIW_STATIC_OUTPUT_RELATIVE`.

## Public inputs

- `admitted_sha`: exact lowercase 40-character source SHA already admitted by the caller's source stage.
- `working_directory`: relative project directory inside the exact checkout; defaults to `.`.
- `validation_plan_json`: bounded JSON object describing one checked-in build/export script and its expected static output.

The plan accepts:

| Field | Required | Meaning |
|---|---:|---|
| `build_script_path` | yes | Relative executable checked-in build/export script beneath `working_directory`. |
| `static_output_directory` | yes | Relative run-created output directory beneath `working_directory`. It must not exist before the build. |
| `build_arguments` | no | Up to 32 direct arguments for the checked-in build script; no shell evaluation occurs. |
| `expected_files` | no | Up to 256 relative regular-file paths that must exist inside the static output. |
| `verification_script_path` | no | Relative executable checked-in verifier. When present, it runs with the output directory as its current directory. |
| `verification_arguments` | no | Up to 32 direct arguments for the checked-in verifier. |
| `build_timeout_seconds` | no | Positive timeout up to 3600 seconds; default 1200. |
| `verification_timeout_seconds` | no | Positive timeout up to 3600 seconds; default 600. |

Paths are relative, newline-free, cannot traverse with `..`, and cannot select symlinked checked-in scripts. The API does not accept shell command text, callbacks, arbitrary environment variables, secret names, runner labels, container engines, cache controls, registry hosts, deployment targets, artifact uploads, matrices or framework configuration.

## Build and verification lifecycle

The workflow performs one exact caller checkout and one marker-bound general workspace. The adapter then:

1. validates the bounded plan and checked-in executable paths;
2. rejects a pre-existing declared output directory so cleanup cannot delete caller-owned source;
3. executes the build script by direct argv with a small allowlisted public runtime environment;
4. validates that the resulting output is a non-empty regular-file tree with no symlink entries using the existing web primitives;
5. verifies every declared `expected_files` entry;
6. optionally runs the checked-in verifier through the existing immutable-output verification primitive, which fails if verification mutates the output; and
7. removes the run-created output before terminal workflow cleanup and exact-source cleanliness verification.

The adapter provides `CIW_ADMITTED_SHA`, `CIW_STATIC_OUTPUT_DIRECTORY`, and `CIW_STATIC_OUTPUT_RELATIVE` to the checked-in build/verifier environment. It does not pass arbitrary caller environment or secret values to product scripts.

A normal build failure returns `static_web_build_failed`; timeout and validation failures use stable non-secret codes. Captured build diagnostics are bounded, replace the repository path with `<project>`, and redact common credential assignments before reaching stderr. Cleanup runs even after build or verification failure. If validation and cleanup both fail, the primary validation code is retained and cleanup is reported separately.

## Public outputs

- `result`: success only when static-web validation, workflow-state cleanup and exact-source cleanliness all succeed;
- `build_result`: `success`, `failure`, `timeout`, or `not-run` as applicable;
- `output_verified`: whether the non-empty output and expected files passed verification;
- `output_digest`: deterministic SHA-256 content-manifest digest for the verified output;
- `output_file_count`: verified regular-file count;
- `test_summary`: bounded canonical JSON summary;
- `cleanup_result`: success only when both run-created output cleanup and marker-bound workspace cleanup succeed; and
- `failure_code`: stable non-secret adapter failure code when available.

The output tree is never uploaded, published, deployed or retained as a GitHub Actions artifact by this workflow.

## Runner, cache and cleanup boundary

Static-web validation uses ordinary semantic general-small capacity, emitted centrally as `[linux, amd64, general, small]`. Consumers cannot choose runner infrastructure. The workflow explicitly disables GitHub Actions cache; dependency caching and persistent runner storage remain Flux/K3s infrastructure concerns.

Terminal cleanup uses the shared marker-bound workspace cleanup action under `if: always()`, followed by an exact admitted-SHA and complete tracked/untracked source-clean check. The workflow introduces no Cloudflare deployment, OIDC, provenance, attestation, signing, publication or release behavior.
