# Runner compatibility report

Generated from `contracts/runner-profiles.json` and `contracts/workflow-inventory.json`.

Every one of the **88** inventoried workflow/job families across **11** repositories has a semantic profile mapping or an explicit exception.

| Repository | Workflow | Migration | Approved profile(s) or exception |
|---|---|---|---|
| `StreamScapeTV/agent-state` | `.github/workflows/agent-state-claim.yml` | `agent-state-lifecycle` | `agent-state-control` |
| `StreamScapeTV/agent-state` | `.github/workflows/agent-state-lifecycle.yml` | `agent-state-lifecycle` | `agent-state-control` |
| `StreamScapeTV/agent-state` | `.github/workflows/agent-state-ownership.yml` | `agent-state-ownership` | `agent-state-control` |
| `StreamScapeTV/agent-state` | `.github/workflows/branch-hygiene.yml` | `branch-hygiene` | `portable` |
| `StreamScapeTV/agent-state` | `.github/workflows/organization-artifact-cleanup.yml` | `artifact-cleanup` | `portable` |
| `StreamScapeTV/agent-state` | `.github/workflows/organization-runner-infrastructure-retry.yml` | `infra-retry` | `portable` |
| `StreamScapeTV/agent-state` | `.github/workflows/recover-release-1-0-65.yml` | `retire` | Exception: `retired-temporary-or-recovery-workflow` |
| `StreamScapeTV/agent-state` | `.github/workflows/release.yml` | `release` | `portable`, `buildah-small`, `buildah-medium`, `buildah-high` |
| `StreamScapeTV/agent-state` | `.github/workflows/runner-infrastructure-retry-fixture.yml` | `retire` | Exception: `retired-temporary-or-recovery-workflow` |
| `StreamScapeTV/agent-state` | `.github/workflows/test.yml` | `python` | `portable`, `buildah-medium`, `buildah-high` |
| `StreamScapeTV/ci-workflows` | `.github/workflows/agent-state-command.yml` | `agent-state-lifecycle` | `agent-state-control` |
| `StreamScapeTV/ci-workflows` | `.github/workflows/reusable-tag-image-chart.yml` | `release` | `portable`, `buildah-small`, `buildah-medium`, `buildah-high` |
| `StreamScapeTV/ci-workflows` | `.github/workflows/self-check.yml` | `conformance` | `portable` |
| `StreamScapeTV/directus-front` | `.github/workflows/agent-state-claim.yml` | `agent-state-lifecycle` | `agent-state-control` |
| `StreamScapeTV/directus-front` | `.github/workflows/agent-state-ownership.yml` | `agent-state-ownership` | `agent-state-control` |
| `StreamScapeTV/directus-front` | `.github/workflows/delete-merged-head-branch.yml` | `branch-hygiene` | `portable` |
| `StreamScapeTV/directus-front` | `.github/workflows/quality-gate.yml` | `flutter` | `mobile`, `apple` |
| `StreamScapeTV/directus-front` | `.github/workflows/quality.yml` | `flutter` | `mobile`, `apple` |
| `StreamScapeTV/finance-hub` | `.github/workflows/agent-state-claim.yml` | `agent-state-lifecycle` | `agent-state-control` |
| `StreamScapeTV/finance-hub` | `.github/workflows/ci.yml` | `flutter` | `mobile`, `apple` |
| `StreamScapeTV/finance-hub` | `.github/workflows/extended-ci.yml` | `flutter` | `mobile`, `apple` |
| `StreamScapeTV/finance-hub` | `.github/workflows/one-time-current-main-baseline.yml` | `retire` | Exception: `retired-temporary-or-recovery-workflow` |
| `StreamScapeTV/flux` | `.github/workflows/agent-state-claim.yml` | `agent-state-lifecycle` | `agent-state-control` |
| `StreamScapeTV/flux` | `.github/workflows/arc-observer-rbac-ci.yaml` | `gitops` | `portable` |
| `StreamScapeTV/flux` | `.github/workflows/database-chart-ci.yaml` | `gitops` | `portable` |
| `StreamScapeTV/flux` | `.github/workflows/directus-web-release-contract.yaml` | `gitops` | `portable` |
| `StreamScapeTV/flux` | `.github/workflows/github-actions-runner-activation-ci.yaml` | `flux-assets` | `portable`, `buildah-tiny`, `buildah-small`, `buildah-medium`, `buildah-high`, `flux-control` |
| `StreamScapeTV/flux` | `.github/workflows/github-actions-runner-buildah-ci.yaml` | `flux-assets` | `portable`, `buildah-tiny`, `buildah-small`, `buildah-medium`, `buildah-high`, `flux-control` |
| `StreamScapeTV/flux` | `.github/workflows/github-actions-runner-buildah-image.yaml` | `flux-assets` | `portable`, `buildah-tiny`, `buildah-small`, `buildah-medium`, `buildah-high`, `flux-control` |
| `StreamScapeTV/flux` | `.github/workflows/github-actions-runner-buildah-smoke.yaml` | `flux-assets` | `portable`, `buildah-tiny`, `buildah-small`, `buildah-medium`, `buildah-high`, `flux-control` |
| `StreamScapeTV/flux` | `.github/workflows/github-actions-runner-capacity-ci.yaml` | `flux-assets` | `portable`, `buildah-tiny`, `buildah-small`, `buildah-medium`, `buildah-high`, `flux-control` |
| `StreamScapeTV/flux` | `.github/workflows/github-actions-runner-ci.yaml` | `flux-assets` | `portable`, `buildah-tiny`, `buildah-small`, `buildah-medium`, `buildah-high`, `flux-control` |
| `StreamScapeTV/flux` | `.github/workflows/github-actions-runner-class-profiles-ci.yaml` | `flux-assets` | `portable`, `buildah-tiny`, `buildah-small`, `buildah-medium`, `buildah-high`, `flux-control` |
| `StreamScapeTV/flux` | `.github/workflows/github-actions-runner-image.yaml` | `flux-assets` | `portable`, `buildah-tiny`, `buildah-small`, `buildah-medium`, `buildah-high`, `flux-control` |
| `StreamScapeTV/flux` | `.github/workflows/issue-ledger-contract.yaml` | `conformance` | `portable` |
| `StreamScapeTV/flux` | `.github/workflows/monitoring-storage-alerts-ci.yaml` | `gitops` | `portable` |
| `StreamScapeTV/flux` | `.github/workflows/reconcile-allowlisted-release.yaml` | `flux-reconcile` | `flux-control` |
| `StreamScapeTV/flux` | `.github/workflows/resource-policy-ci.yaml` | `conformance` | `portable` |
| `StreamScapeTV/flux` | `.github/workflows/storage-rightsizing-ci.yaml` | `gitops` | `portable` |
| `StreamScapeTV/flux` | `.github/workflows/vaultwarden-backup-ci.yaml` | `gitops` | `portable` |
| `StreamScapeTV/iptv-android` | `.github/workflows/agent-state-claim.yml` | `agent-state-lifecycle` | `agent-state-control` |
| `StreamScapeTV/iptv-android` | `.github/workflows/android-ci.yml` | `android` | `mobile` |
| `StreamScapeTV/iptv-android` | `.github/workflows/android-live-backend-acceptance.yml` | `device` | `physical-device` |
| `StreamScapeTV/iptv-android` | `.github/workflows/android-release-validation.yml` | `android` | `mobile` |
| `StreamScapeTV/iptv-android` | `.github/workflows/governance-ci.yml` | `android` | `mobile` |
| `StreamScapeTV/iptv-android` | `.github/workflows/pr-agent-state-policy.yml` | `agent-state-ownership` | `agent-state-control` |
| `StreamScapeTV/iptv-android` | `.github/workflows/room-schema-json-integrity.yml` | `android` | `mobile` |
| `StreamScapeTV/iptv-apple` | `.github/workflows/agent-state-claim.yml` | `agent-state-lifecycle` | `agent-state-control` |
| `StreamScapeTV/iptv-apple` | `.github/workflows/agent-state-ownership.yml` | `agent-state-ownership` | `agent-state-control` |
| `StreamScapeTV/iptv-apple` | `.github/workflows/buildah-ci-policy.yml` | `conformance` | `portable` |
| `StreamScapeTV/iptv-apple` | `.github/workflows/develop-ci.yml` | `apple` | `apple` |
| `StreamScapeTV/iptv-apple` | `.github/workflows/guarded-branch-cleanup.yml` | `branch-hygiene` | `portable` |
| `StreamScapeTV/iptv-apple` | `.github/workflows/restoration-final-dispatch.yml` | `retire` | Exception: `retired-temporary-or-recovery-workflow` |
| `StreamScapeTV/iptv-apple` | `.github/workflows/restoration-final-linux.yml` | `retire` | Exception: `retired-temporary-or-recovery-workflow` |
| `StreamScapeTV/iptv-apple` | `.github/workflows/visual-attachment-recovery-v2.yml` | `retire` | Exception: `retired-temporary-or-recovery-workflow` |
| `StreamScapeTV/iptv-apple` | `.github/workflows/visual-attachment-recovery.yml` | `retire` | Exception: `retired-temporary-or-recovery-workflow` |
| `StreamScapeTV/iptv-apple` | `.github/workflows/visual-baseline-proof.yml` | `apple` | `apple` |
| `StreamScapeTV/iptv-apple` | `.github/workflows/visual-macos-review-proof.yml` | `apple` | `apple` |
| `StreamScapeTV/iptv-apple` | `.github/workflows/visual-review-proof.yml` | `apple` | `apple` |
| `StreamScapeTV/iptv-apple` | `.github/workflows/visual-tvos-review-proof.yml` | `apple` | `apple` |
| `StreamScapeTV/iptv-backend` | `.github/workflows/agent-state-claim.yml` | `agent-state-lifecycle` | `agent-state-control` |
| `StreamScapeTV/iptv-backend` | `.github/workflows/agent-state-ownership.yml` | `agent-state-ownership` | `agent-state-control` |
| `StreamScapeTV/iptv-backend` | `.github/workflows/backend-ci.yml` | `python` | `portable`, `buildah-medium`, `buildah-high` |
| `StreamScapeTV/iptv-backend` | `.github/workflows/issue121-dispatch-final-validation.yml` | `retire` | Exception: `retired-temporary-or-recovery-workflow` |
| `StreamScapeTV/iptv-backend` | `.github/workflows/release.yml` | `release` | `portable`, `buildah-small`, `buildah-medium`, `buildah-high` |
| `StreamScapeTV/iptv-backend` | `.github/workflows/workflow-policy.yml` | `conformance` | `portable` |
| `StreamScapeTV/streamscape-media` | `.github/workflows/agent-ci.yml` | `retire` | Exception: `retired-temporary-or-recovery-workflow` |
| `StreamScapeTV/streamscape-media` | `.github/workflows/agent-state-claim.yml` | `agent-state-lifecycle` | `agent-state-control` |
| `StreamScapeTV/streamscape-media` | `.github/workflows/agent-state-ownership.yml` | `agent-state-ownership` | `agent-state-control` |
| `StreamScapeTV/streamscape-media` | `.github/workflows/agent2-apply-433.yml` | `retire` | Exception: `retired-temporary-or-recovery-workflow` |
| `StreamScapeTV/streamscape-media` | `.github/workflows/agent2-run-433.yml` | `retire` | Exception: `retired-temporary-or-recovery-workflow` |
| `StreamScapeTV/streamscape-media` | `.github/workflows/agent2-validate-ios-444.yml` | `retire` | Exception: `retired-temporary-or-recovery-workflow` |
| `StreamScapeTV/streamscape-media` | `.github/workflows/android-ci.yml` | `android` | `mobile` |
| `StreamScapeTV/streamscape-media` | `.github/workflows/ci-label-policy.yml` | `conformance` | `portable` |
| `StreamScapeTV/streamscape-media` | `.github/workflows/cleanup-merged-issue-branch.yml` | `branch-hygiene` | `portable` |
| `StreamScapeTV/streamscape-media` | `.github/workflows/develop-ci.yml` | `media` | `mobile`, `apple`, `physical-device` |
| `StreamScapeTV/streamscape-media` | `.github/workflows/ios-ci.yml` | `apple` | `apple` |
| `StreamScapeTV/streamscape-media` | `.github/workflows/ios-device-ci.yml` | `device` | `physical-device` |
| `StreamScapeTV/streamscape-media` | `.github/workflows/ios-native-aggregate-device-ci.yml` | `device` | `physical-device` |
| `StreamScapeTV/streamscape-media` | `.github/workflows/ios-video-output-device-ci.yml` | `device` | `physical-device` |
| `StreamScapeTV/streamscape-media` | `.github/workflows/release-verify.yml` | `media` | `mobile`, `apple`, `physical-device` |
| `StreamScapeTV/streamscape-media` | `.github/workflows/release.yml` | `media` | `mobile`, `apple`, `physical-device` |
| `StreamScapeTV/streamscape-media` | `.github/workflows/sync-local-develop-ref.yml` | `retire` | Exception: `retired-temporary-or-recovery-workflow` |
| `StreamScapeTV/streamscape-media` | `.github/workflows/tvos-avfoundation-device-ci.yml` | `device` | `physical-device` |
| `StreamScapeTV/StreamScapeWeb` | `.github/workflows/agent-state-claim.yml` | `agent-state-lifecycle` | `agent-state-control` |
| `StreamScapeTV/StreamScapeWeb` | `.github/workflows/agent-state-ownership.yml` | `agent-state-ownership` | `agent-state-control` |
| `StreamScapeTV/StreamScapeWeb` | `.github/workflows/baseline.yml` | `node` | `portable` |
| `StreamScapeTV/StreamScapeWeb` | `.github/workflows/branch-feedback.yml` | `node` | `portable` |

## Interpretation

This report classifies current inventory; it does not authorize consumer edits or direct infrastructure selectors. Reusable-workflow callers supply bounded intent and the central planner resolves the current internal selector. `retire` entries remain owner cleanup; `other` entries require a linked adoption decision.
