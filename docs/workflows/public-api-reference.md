# Public workflow API reference

Contract version: `3.0.0`

Generated from `contracts/public-workflows.json` and its checked-in fragments. Application repository/product identity is intentionally not part of this compatibility contract.

## Workflow APIs

| API | File | Status | Trust | Check |
|---|---|---|---|---|
| `flux.assets` `2.0.0` | `.github/workflows/reusable-flux-infrastructure-assets.yml` | `planned` | `trusted-publication` | Release / Flux infrastructure assets |
| `flux.reconcile` `2.0.0` | `.github/workflows/reusable-flux-reconcile.yml` | `planned` | `flux-authorized` | Flux / Reconciliation |
| `helm.publish` `2.0.0` | `.github/workflows/reusable-helm-publish.yml` | `migration-pending` | `trusted-publication` | Release / Helm publication |
| `helm.validate` `2.0.0` | `.github/workflows/reusable-helm-validate.yml` | `migration-pending` | `read-only-validation` | CI / Helm validation |
| `maintenance.artifacts` `1.0.0` | `.github/workflows/reusable-artifact-cleanup.yml` | `planned` | `trusted-maintenance` | Maintenance / Artifact cleanup |
| `maintenance.branches` `1.0.0` | `.github/workflows/reusable-branch-hygiene.yml` | `planned` | `trusted-maintenance` | Maintenance / Branch hygiene |
| `maintenance.runner-retry` `1.0.0` | `.github/workflows/reusable-runner-infrastructure-retry.yml` | `planned` | `trusted-maintenance` | Maintenance / Runner retry |
| `oci.build` `2.0.0` | `.github/workflows/reusable-oci-build.yml` | `migration-pending` | `read-only-validation` | CI / OCI build validation |
| `oci.publish` `2.0.0` | `.github/workflows/reusable-oci-publish.yml` | `migration-pending` | `trusted-publication` | Release / OCI publication |
| `release.native-image-chart` `1.0.0` | `.github/workflows/reusable-native-image-chart.yml` | `implemented` | `trusted-publication` | Publish native amd64 image and Helm chart |
| `release.orchestrate` `2.0.0` | `.github/workflows/reusable-release.yml` | `planned` | `trusted-publication` | Release / Verified outputs |
| `release.tag-image-chart-bootstrap` `1.2.0` | `.github/workflows/reusable-tag-image-chart.yml` | `deprecated-bootstrap-exception` | `trusted-publication` | Release / Bootstrap image and chart |
| `source.resolve` `1.0.0` | `.github/workflows/reusable-resolve-source.yml` | `implemented` | `source-admission` | Shared / Source admission |
| `validation.android` `2.0.0` | `.github/workflows/reusable-android.yml` | `implemented` | `read-only-validation` | CI / Android validation |
| `validation.android-live-service` `1.0.0` | `.github/workflows/reusable-android-live-service.yml` | `implemented` | `read-only-validation` | CI / Android live-service acceptance |
| `validation.android-release` `1.0.0` | `.github/workflows/reusable-android-release.yml` | `implemented` | `read-only-validation` | CI / Android unsigned release validation |
| `validation.apple` `1.0.0` | `.github/workflows/reusable-apple.yml` | `implemented` | `read-only-validation` | CI / Apple validation |
| `validation.device` `2.0.0` | `.github/workflows/reusable-device.yml` | `implemented` | `physical-device-validation` | CI / Physical device validation |
| `validation.flutter` `1.0.0` | `.github/workflows/reusable-flutter.yml` | `implemented` | `read-only-validation` | CI / Flutter validation |
| `validation.gitops` `1.0.0` | `.github/workflows/reusable-gitops-validation.yml` | `implemented` | `read-only-validation` | CI / GitOps validation |
| `validation.node` `1.0.0` | `.github/workflows/reusable-node.yml` | `implemented` | `read-only-validation` | CI / Node validation |
| `validation.python` `1.0.0` | `.github/workflows/reusable-python.yml` | `implemented` | `read-only-validation` | CI / Python validation |
| `validation.script` `1.0.0` | `.github/workflows/reusable-script.yml` | `implemented` | `read-only-validation` | CI / Script validation |

## API details

### `flux.assets`

- Events: `tag-push`, `workflow_call`, `workflow_dispatch-verify-only`
- Inputs: `admitted_sha` (required), `release_manifest_path` (required), `release_version` (required), `operation` (required), `policy_path` (required), `request_id` (required)
- Secrets: `registry_username`, `registry_token`
- Outputs: `result`, `immutable_references_json`, `release_manifest_sha256`, `request_id`
- Repository-owned hooks: `release_manifest_path`, `policy_path`

### `flux.reconcile`

- Events: `workflow_dispatch`, `repository_dispatch`, `workflow_call`
- Inputs: `admitted_sha` (required), `target_id` (required), `operation` (required), `policy_path` (required), `allowlist_path` (required), `request_id` (required), `dry_run` (default `True`)
- Secrets: `flux_kubeconfig`, `flux_sops_age_key`
- Outputs: `result`, `reconciliation_state`, `request_id`
- Repository-owned hooks: `policy_path`, `allowlist_path`

### `helm.publish`

- Events: `tag-push`, `workflow_call`, `workflow_dispatch-verify-only`
- Inputs: `admitted_sha` (required), `chart_name` (required), `chart_path` (required), `release_version` (required), `values_path`, `policy_path`, `image_digest`, `immutable_references_json`
- Secrets: `registry_username`, `registry_token`
- Outputs: `result`, `chart_digest`, `immutable_references_json`
- Repository-owned hooks: `chart_path`, `values_path`, `policy_path`

### `helm.validate`

- Events: `pull_request`, `push`, `workflow_dispatch`, `workflow_call`
- Inputs: `admitted_sha` (required), `chart_name` (required), `chart_path` (required), `release_version`, `values_path`, `policy_path`, `artifact_exception_id`
- Secrets: none
- Outputs: `result`, `chart_digest`, `artifact_exception_used`
- Repository-owned hooks: `chart_path`, `values_path`, `policy_path`

### `maintenance.artifacts`

- Events: `schedule`, `workflow_dispatch`, `workflow_call`
- Inputs: `repository_scope`, `dry_run` (default `True`), `request_id` (required)
- Secrets: `organization_maintenance_token`
- Outputs: `result`, `mutation_count`, `request_id`
- Repository-owned hooks: none

### `maintenance.branches`

- Events: `pull_request-closed`, `workflow_dispatch`, `workflow_call`
- Inputs: `project_id` (required), `pr_number`, `expected_head_sha` (required), `dry_run` (default `True`), `request_id` (required)
- Secrets: `organization_maintenance_token`
- Outputs: `result`, `mutation_count`, `request_id`
- Repository-owned hooks: none

### `maintenance.runner-retry`

- Events: `workflow_dispatch`, `workflow_call`
- Inputs: `project_id` (required), `run_id` (required), `expected_head_sha` (required), `dry_run` (default `True`), `request_id` (required)
- Secrets: `organization_maintenance_token`
- Outputs: `result`, `retry_run_id`, `request_id`
- Repository-owned hooks: none

### `oci.build`

- Events: `pull_request`, `push`, `workflow_dispatch`, `workflow_call`
- Inputs: `admitted_sha` (required), `image_name` (required), `dockerfile_path` (default `Dockerfile`), `build_context` (default `.`), `release_version`, `platform_set`, `artifact_exception_id`
- Secrets: none
- Outputs: `result`, `image_digest`, `platform_digests_json`, `resolved_inputs_json`, `artifact_exception_used`
- Repository-owned hooks: `dockerfile_path`, `build_context`

### `oci.publish`

- Events: `tag-push`, `workflow_call`, `workflow_dispatch-verify-only`
- Inputs: `admitted_sha` (required), `image_name` (required), `dockerfile_path` (default `Dockerfile`), `build_context` (default `.`), `release_version` (required), `platform_set`
- Secrets: `registry_username`, `registry_token`
- Outputs: `result`, `image_digest`, `platform_digests_json`, `immutable_references_json`
- Repository-owned hooks: `dockerfile_path`, `build_context`

### `release.native-image-chart`

- Events: `tag-push`, `workflow_call`, `workflow_dispatch-existing-tag`
- Inputs: `release_mode` (default `tag-push`), `release_version`, `release_source_sha`, `image_name` (required), `chart_name` (required), `chart_path` (required), `dockerfile_path` (default `Dockerfile`), `build_context` (default `.`)
- Secrets: `registry_username`, `registry_token`
- Outputs: `version`, `source_sha`, `image_reference`, `image_digest`, `chart_reference`, `chart_digest`, `chart_package_sha256`
- Repository-owned hooks: `chart_path`, `dockerfile_path`, `build_context`

### `release.orchestrate`

- Events: `tag-push`, `workflow_call`, `workflow_dispatch-verify-only`
- Inputs: `admitted_sha` (required), `release_manifest_path` (required), `release_tag` (required), `release_version` (required), `request_id` (required), `target_id`
- Secrets: `registry_username`, `registry_token`, `flux_handoff_token`
- Outputs: `result`, `immutable_references_json`, `release_manifest_sha256`, `handoff_state`, `request_id`
- Repository-owned hooks: `release_manifest_path`

### `release.tag-image-chart-bootstrap`

- Events: `tag-push`, `workflow_call`, `workflow_dispatch-existing-tag`
- Inputs: `release_mode` (default `tag-push`), `release_version`, `release_source_sha`, `image_recovery_authority` (default ``), `image_name` (required), `chart_name` (required), `chart_path` (required), `dockerfile_path` (default `Dockerfile`), `build_context` (default `.`)
- Secrets: `registry_username`, `registry_token`
- Outputs: `version`, `source_sha`, `image_reference`, `image_digest`, `chart_reference`, `chart_digest`, `chart_package_sha256`
- Repository-owned hooks: `chart_path`, `dockerfile_path`, `build_context`

### `source.resolve`

- Events: `pull_request`, `push`, `workflow_dispatch`, `workflow_call`, `tag-push`, `workflow_run`, `issue_comment`, `pull_request_target`
- Inputs: `source_mode` (required), `requested_sha`, `expected_branch`, `release_contract`, `history_depth` (default `1`)
- Secrets: none
- Outputs: `caller_repository`, `caller_default_branch`, `caller_integration_branch`, `trust_mode`, `source_repository`, `source_sha`, `requested_sha`, `resolved_sha`, `pr_number`, `pr_head_repository`, `pr_head_sha`, `pr_base_branch`, `pr_base_sha`, `pr_merge_sha`, `tag_name`, `tag_object_sha`, `tag_commit_sha`, `requires_freshness`, `history_depth`, `request_id`, `evidence_id`
- Repository-owned hooks: none

### `validation.android`

- Events: `pull_request`, `push`, `workflow_dispatch`, `workflow_call`
- Inputs: `admitted_sha` (required), `validation_scope` (required), `working_directory` (default `.`), `gradle_wrapper_path` (default `gradlew`), `validation_plan_json` (required), `private_dependency_repository` (default ``), `private_dependency_sha` (default ``), `private_dependency_subdirectory` (default `.`), `private_dependency_id` (default ``)
- Secrets: `private_dependency_token`
- Outputs: `result`, `test_summary`, `cleanup_result`
- Repository-owned hooks: `validation_plan_json`

### `validation.android-live-service`

- Events: `pull_request`, `push`, `workflow_dispatch`, `workflow_call`
- Inputs: `admitted_sha` (required), `working_directory` (default `.`), `validation_plan_json` (required), `private_dependency_repository` (default ``), `private_dependency_sha` (default ``), `private_dependency_subdirectory` (default `.`), `private_dependency_id` (default ``)
- Secrets: `service_username`, `service_password`, `private_dependency_token`
- Outputs: `result`, `test_summary`, `cleanup_result`
- Repository-owned hooks: `validation_plan_json`

### `validation.android-release`

- Events: `pull_request`, `push`, `workflow_dispatch`, `workflow_call`
- Inputs: `admitted_sha` (required), `working_directory` (default `.`), `gradle_wrapper_path` (default `gradlew`), `validation_plan_json` (required), `private_dependency_repository` (default ``), `private_dependency_sha` (default ``), `private_dependency_subdirectory` (default `.`), `private_dependency_id` (default ``)
- Secrets: `private_dependency_token`
- Outputs: `result`, `test_summary`, `cleanup_result`, `artifact_manifest_json`
- Repository-owned hooks: `validation_plan_json`

### `validation.apple`

- Events: `pull_request`, `push`, `workflow_dispatch`, `workflow_call`
- Inputs: `admitted_sha` (required), `validation_profile` (required), `version_file`, `working_directory` (default `.`), `command_profile` (required), `script_path`, `platform` (required), `scheme`, `destination_profile`, `artifact_exception_id`
- Secrets: none
- Outputs: `result`, `test_summary`, `artifact_exception_used`
- Repository-owned hooks: `command_profile`, `script_path`

### `validation.device`

- Events: `workflow_dispatch`, `workflow_call`
- Inputs: `admitted_sha` (required), `device_family` (required), `device_capability` (required), `host_capacity` (required), `prepare_script_path` (required), `test_script_path` (required), `evidence_script_path` (required), `cleanup_script_path` (required), `arguments_json` (default `[]`), `environment_json` (default `{}`), `max_duration_minutes` (default `60`), `evidence_exception_id`, `request_id` (required)
- Secrets: `device_authorization_receipt`
- Outputs: `result`, `device_evidence_id`, `artifact_exception_used`, `request_id`
- Repository-owned hooks: `prepare_script_path`, `test_script_path`, `evidence_script_path`, `cleanup_script_path`

### `validation.flutter`

- Events: `pull_request`, `push`, `workflow_dispatch`, `workflow_call`
- Inputs: `admitted_sha` (required), `validation_profile` (required), `version_file`, `working_directory` (default `.`), `command_profile` (required), `script_path`, `platform`, `artifact_exception_id`
- Secrets: none
- Outputs: `result`, `test_summary`, `artifact_exception_used`
- Repository-owned hooks: `command_profile`, `script_path`

### `validation.gitops`

- Events: `pull_request`, `push`, `workflow_dispatch`, `workflow_call`
- Inputs: `admitted_sha` (required), `validation_profile` (required), `consumer_contract` (required), `change_base_sha` (default ``), `policy_script_profile` (default ``), `artifact_exception_id`
- Secrets: none
- Outputs: `result`, `test_summary`, `render_digest`, `cleanup_result`, `evidence_id`
- Repository-owned hooks: `policy_script_profile`

### `validation.node`

- Events: `pull_request`, `push`, `workflow_dispatch`, `workflow_call`
- Inputs: `admitted_sha` (required), `validation_profile` (required), `version_file`, `node_version`, `working_directory` (default `.`), `install_profile` (required), `command_profile` (required), `script_path`, `static_output_directory`, `output_verifier_path`, `public_environment` (default `{}`), `artifact_exception_id`
- Secrets: none
- Outputs: `result`, `node_version`, `npm_version`, `install_result`, `test_summary`, `build_result`, `output_verified`, `output_digest`, `clean_tree`, `cleanup_result`, `artifact_exception_used`, `evidence_id`
- Repository-owned hooks: `command_profile`, `script_path`, `static_output_directory`, `output_verifier_path`

### `validation.python`

- Events: `pull_request`, `push`, `workflow_dispatch`, `workflow_call`
- Inputs: `admitted_sha` (required), `validation_profile` (required), `version_file`, `working_directory` (default `.`), `command_profile` (required), `script_path`, `artifact_exception_id`
- Secrets: none
- Outputs: `result`, `test_summary`, `artifact_exception_used`
- Repository-owned hooks: `command_profile`, `script_path`

### `validation.script`

- Events: `pull_request`, `push`, `workflow_dispatch`, `workflow_call`
- Inputs: `admitted_sha` (required), `validation_profile` (required), `working_directory` (default `.`), `script_path` (required)
- Secrets: none
- Outputs: `result`
- Repository-owned hooks: `script_path`, `working_directory`

## Compatibility

Application repositories/products are not admission fields. Compatibility is determined from API surface, trust, permissions, technology inputs/outputs, and acknowledged breaking changes. `migration-pending` records reviewed next-version contracts whose reusable YAML wiring is completed by the follow-on integration issue.
