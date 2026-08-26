# Shared foundation primitives

Generated from `contracts/foundation-primitives.json`. Do not edit directly.

Architecture: `consumer workflow -> public action -> named tested function`.

All six foundation composite actions dispatch through the checked-in `ciw` registry while preserving the function, input, output, side-effect, trust, and cleanup contracts below.

## Named modules

### `ci_workflows.workspace`

Trust class: `shared-foundation`.

| Function | Inputs | Outputs | Side effects | Cleanup duty |
|---|---|---|---|---|
| `prepare_workspace` | `WorkspaceContext`, `profile`, `cache_mode`, `source_sha`, `lock_digest`, `trust_mode`, `contract_root` | `WorkspaceState` | `creates one marker-bound workflow state root`, `creates only contract-selected isolated directories` | cleanup_workspace |
| `resolve_state_root` | `runner_temp`, `state_id`, `declared_root`, `contract_root` | `Path` | `derives the only permitted state root beneath protected RUNNER_TEMP`, `rejects substituted CI_WORKFLOW_ROOT values` | none |
| `register_state_path` | `state_root`, `name`, `relative`, `kind`, `contract_root`, `create` | `Path` | `appends one bounded dynamic path to the state registry` | remove_registered_path or cleanup_workspace |
| `remove_registered_path` | `state_root`, `name`, `contract_root` | `boolean existed` | `removes one validated registered path` | self-verifying |
| `cleanup_workspace` | `state_root`, `expected_state_id`, `contract_root` | `CleanupReport` | `removes validated registered state on Linux or macOS`, `fails on unsafe targets, symlink escape, or residue` | terminal cleanup function |

### `ci_workflows.tooling`

Trust class: `shared-foundation`.

| Function | Inputs | Outputs | Side effects | Cleanup duty |
|---|---|---|---|---|
| `verify_tool` | `tool_id`, `contract_root` | `ToolEvidence` | `executes a contract-selected version command` | none |
| `verify_tool_set` | `tool_set`, `contract_root` | `ToolSetEvidence` | `verifies contract-selected tools` | none |
| `verify_runtime_capability` | `capability_profile`, `declared_os`, `declared_architecture`, `contract_root` | `RuntimeCapabilityEvidence` | `verifies actual OS and architecture against one semantic contract profile`, `compares GitHub-declared OS and architecture when present without emitting host identity or executable paths` | none |
| `verify_checksum` | `path`, `algorithm`, `expected` | `verified checksum` | none | none |
| `verify_digest` | `path`, `expected_digest` | `verified digest` | none | none |
| `install_locked_asset` | `asset_id`, `destination_root`, `contract_root` | `InstalledAsset` | `downloads one contract-selected HTTPS asset`, `verifies size and SHA-256 before atomic install` | registered tool path through cleanup_workspace |

### `ci_workflows.dependencies`

Trust class: `exact-source`.

| Function | Inputs | Outputs | Side effects | Cleanup duty |
|---|---|---|---|---|
| `checkout_private_dependency` | `state_root`, `repository`, `admitted_sha`, `dependency_id`, `expected_subpath`, `fetch_depth`, `token`, `contract_root` | `DependencyResult` | `calls merged #7 exact checkout`, `erases remotes and credential-bearing Git config`, `verifies detached exact HEAD and expected subpath` | registered dependency path; immediate cleanup on partial failure and cleanup_workspace on terminal paths |

### `ci_workflows.policy`

Trust class: `shared-foundation`.

| Function | Inputs | Outputs | Side effects | Cleanup duty |
|---|---|---|---|---|
| `scan_tracked_repository` | `root`, `repository`, `contract_root` | `tracked count`, `scanned count` | `reads tracked non-binary files within configured size bounds` | none |
| `verify_clean_tree` | `root` | none | `reads complete tracked and untracked Git status` | none |
| `verify_generated_outputs` | `root`, `contract_root` | `generated output count` | `verifies configured generated outputs have no diff` | none |
| `validate_artifacts` | `artifacts`, `exception_id`, `trust_mode`, `contract_root` | `validated exception id` | none | none |
| `validate_cache_request` | `mode`, `repository`, `source_sha`, `lock_digest`, `platform`, `profile`, `trust_mode`, `contract_root` | `CacheDecision` | none | cache transport remains caller-owned and disabled by default |
| `verify_repository_policy` | `root`, `repository`, `phase`, `artifact_manifest_json`, `artifact_exception_id`, `trust_mode`, `contract_root` | `PolicyReport` | `runs the central clean-tree, tracked-secret, forbidden-file, generated-output, and artifact gate` | none |

### `ci_workflows.evidence`

Trust class: `shared-foundation`.

| Function | Inputs | Outputs | Side effects | Cleanup duty |
|---|---|---|---|---|
| `redact_text` | `value`, `contract_root` | `redacted text` | none | none |
| `build_evidence` | `source_sha`, `workflow_release`, `runner_profile`, `toolchain`, `command_profile`, `result`, `cleanup_state`, `cleanup_removed_paths`, `contract_root` | `EvidenceResult` | none | none |
| `write_evidence` | `state_root`, `evidence` | `Path` | `atomically writes canonical evidence beneath the registered evidence root` | cleanup_workspace |

## Composite actions

| Action | Named function boundary | Inputs | Outputs | Cleanup duty |
|---|---|---|---|---|
| `actions/prepare-workspace` | `ci_workflows.workspace.prepare_workspace` | `profile`, `cache_mode`, `source_sha`, `lock_digest`, `trust_mode` | `state_id`, `profile`, `cache_mode`, `cache_key`, `registered_path_count` | must pair with actions/cleanup-workspace under if: always() |
| `actions/verify-toolchain` | `ci_workflows.tooling.verify_tool_set or install_locked_asset` | `operation`, `tool_set`, `asset_id`, `capability_profile` | `tool_set`, `toolchain_json`, `toolchain_id`, `capability_profile`, `platform`, `capability_id`, `capability_verified`, `asset_id`, `asset_relative_path`, `asset_sha256`, `verified` | installed assets are registered under the workflow state root |
| `actions/checkout-private-dependency` | `ci_workflows.dependencies.checkout_private_dependency` | `repository`, `admitted_sha`, `dependency_id`, `expected_subpath`, `fetch_depth`, `token` | `dependency_id`, `repository`, `head_sha`, `relative_path`, `expected_subpath`, `remotes_erased`, `credentials_erased`, `verified` | registered dependency path; cleanup-workspace required under if: always() |
| `actions/verify-repository-policy` | `ci_workflows.policy.verify_repository_policy` | `phase`, `artifacts_json`, `artifact_exception_id`, `trust_mode` | `phase`, `tracked_files`, `scanned_files`, `generated_outputs`, `artifact_count`, `artifact_exception_id`, `policy_evidence_id`, `verified` | none; the action mutates no caller source |
| `actions/render-evidence` | `ci_workflows.evidence.build_evidence and write_evidence` | `source_sha`, `workflow_release`, `runner_profile`, `toolchain_json`, `command_profile`, `result`, `cleanup_state`, `cleanup_removed_paths` | `evidence_id`, `evidence_json`, `redacted` | evidence file is registered state and must be removed by cleanup-workspace |
| `actions/cleanup-workspace` | `ci_workflows.workspace.cleanup_workspace` | none | `state_id`, `removed_paths`, `removed_sensitive_paths`, `partial_setup`, `platform`, `cleanup_verified` | terminal action; invoke under if: always() |

## Integration constraints

- Prepare the workspace before tool installation, dependency checkout, evidence writing, or cleanup.
- Invoke cleanup-workspace under if: always(); never pass a caller-selected deletion path.
- Pass only a full SHA admitted by source.resolve into checkout-private-dependency.
- Caching is disabled unless an allowed mode, trust mode, exact source SHA, lock digest, platform, and profile are supplied.
- First-party Central actions use repository-local paths or the active @main library channel; tool versions and downloadable asset digests remain functional authority in contracts/tool-lock.json.
- Artifact handling is feature-scoped: private-source detailed output must not become public Actions artifacts, while public or non-private workflows may declare artifacts when functionality requires them.
- Product commands remain checked-in consumer scripts selected outside these primitives.
- #31 may converge package and command names but must preserve these public functions and side-effect boundaries or record a reviewed compatibility change.

## Bootstrap basis

Issue #8 was implemented under explicit repository-owner bootstrap authorization during the Agent State-to-Supabase transition. No legacy Agent State receipt, issue-comment transport, manual workflow dispatch, or `agentctl` result is claimed.
