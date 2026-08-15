# Final Candidate Lifecycle Inventory

- Audited: `2026-08-14`
- Repositories: `13`
- Current workflow files: `97`
- Intermediate checkpoint prefix: `[skip push ci] `

This report classifies the current live workflow-file set. A workflow can have multiple trigger classes when one file serves more than one authorized event family.

## Prefix contract

| Event family | Required behavior |
|---|---|
| `ordinary_unprotected_feature_push` | `product-validation-forbidden` |
| `pull_request` | `must-run-even-when-head-subject-is-prefixed` |
| `protected_integration_release` | `policy-controlled-and-not-suppressed-by-prefix` |
| `manual_publication_deployment_device_live` | `explicit-authority-only` |

## StreamScapeTV/StreamScapeWeb

- Protected integration branch: `main`
- Workflow files: `2`

| Workflow path | Trigger class(es) | Finding | Remediation owner |
|---|---|---|---|
| `.github/workflows/baseline.yml` | `final-pull-request-validation` | — | — |
| `.github/workflows/branch-feedback.yml` | `noncompliant-unprotected-feature-branch-product-validation`<br>`maintenance-control` | ordinary non-main push currently invokes product validation unless checkpoint-prefixed | https://github.com/StreamScapeTV/StreamScapeWeb/issues/261 |

## StreamScapeTV/agent-state

- Protected integration branch: `main`
- Workflow files: `10`

| Workflow path | Trigger class(es) | Finding | Remediation owner |
|---|---|---|---|
| `.github/workflows/agent-state-claim.yml` | `retired-stale` | — | — |
| `.github/workflows/agent-state-lifecycle.yml` | `retired-stale` | — | — |
| `.github/workflows/agent-state-ownership.yml` | `retired-stale` | — | — |
| `.github/workflows/branch-hygiene.yml` | `maintenance-control` | — | — |
| `.github/workflows/organization-artifact-cleanup.yml` | `maintenance-control` | — | — |
| `.github/workflows/organization-runner-infrastructure-retry.yml` | `maintenance-control` | — | — |
| `.github/workflows/recover-release-1-0-65.yml` | `retired-stale` | — | — |
| `.github/workflows/release.yml` | `authorized-publication-deployment-device-live-evidence` | — | — |
| `.github/workflows/runner-infrastructure-retry-fixture.yml` | `retired-stale` | — | — |
| `.github/workflows/test.yml` | `final-pull-request-validation`<br>`protected-integration-release-validation` | canonical runner routing/runtime remediation | https://github.com/StreamScapeTV/agent-state/issues/195 |

## StreamScapeTV/agent-state-dashboard

- Protected integration branch: `main`
- Workflow files: `1`

| Workflow path | Trigger class(es) | Finding | Remediation owner |
|---|---|---|---|
| `.github/workflows/validation.yml` | `final-pull-request-validation`<br>`protected-integration-release-validation` | — | — |

## StreamScapeTV/agent-state-supabase

- Protected integration branch: `main`
- Workflow files: `2`

| Workflow path | Trigger class(es) | Finding | Remediation owner |
|---|---|---|---|
| `.github/workflows/bootstrap-validation.yml` | `final-pull-request-validation` | — | — |
| `.github/workflows/postgres-reconstruction.yml` | `final-pull-request-validation` | — | — |

## StreamScapeTV/ci-workflows

- Protected integration branch: `main`
- Workflow files: `28`

| Workflow path | Trigger class(es) | Finding | Remediation owner |
|---|---|---|---|
| `.github/workflows/android-validation-smoke.yml` | `maintenance-control` | — | — |
| `.github/workflows/apple-certification-smoke.yml` | `maintenance-control` | — | — |
| `.github/workflows/apple-physical-device-lock-smoke.yml` | `maintenance-control` | — | — |
| `.github/workflows/apple-validation-smoke.yml` | `maintenance-control` | — | — |
| `.github/workflows/device-lock-contract-smoke.yml` | `maintenance-control` | — | — |
| `.github/workflows/device-validation-contract-smoke.yml` | `maintenance-control` | — | — |
| `.github/workflows/flutter-apple-validation-smoke.yml` | `maintenance-control` | — | — |
| `.github/workflows/flutter-validation-smoke.yml` | `maintenance-control` | — | — |
| `.github/workflows/gitops-validation-smoke.yml` | `maintenance-control` | — | — |
| `.github/workflows/helm-validation-smoke.yml` | `maintenance-control` | — | — |
| `.github/workflows/issue-dependency-sync.yml` | `maintenance-control` | — | — |
| `.github/workflows/oci-build-smoke.yml` | `maintenance-control` | — | — |
| `.github/workflows/oci-publish-smoke.yml` | `maintenance-control` | — | — |
| `.github/workflows/reusable-android.yml` | `maintenance-control` | — | — |
| `.github/workflows/reusable-apple.yml` | `maintenance-control` | — | — |
| `.github/workflows/reusable-device.yml` | `authorized-publication-deployment-device-live-evidence` | — | — |
| `.github/workflows/reusable-flutter.yml` | `maintenance-control` | — | — |
| `.github/workflows/reusable-gitops-validation.yml` | `maintenance-control` | — | — |
| `.github/workflows/reusable-helm-publish.yml` | `authorized-publication-deployment-device-live-evidence` | — | — |
| `.github/workflows/reusable-helm-validate.yml` | `maintenance-control` | — | — |
| `.github/workflows/reusable-node.yml` | `maintenance-control` | — | — |
| `.github/workflows/reusable-oci-build.yml` | `maintenance-control` | — | — |
| `.github/workflows/reusable-oci-publish.yml` | `authorized-publication-deployment-device-live-evidence` | — | — |
| `.github/workflows/reusable-python.yml` | `maintenance-control` | — | — |
| `.github/workflows/reusable-resolve-source.yml` | `maintenance-control` | — | — |
| `.github/workflows/reusable-tag-image-chart.yml` | `authorized-publication-deployment-device-live-evidence` | — | — |
| `.github/workflows/runner-images-validation.yml` | `final-pull-request-validation`<br>`maintenance-control` | — | — |
| `.github/workflows/self-check.yml` | `final-pull-request-validation`<br>`protected-integration-release-validation` | — | — |

## StreamScapeTV/directus-front

- Protected integration branch: `main`
- Workflow files: `6`

| Workflow path | Trigger class(es) | Finding | Remediation owner |
|---|---|---|---|
| `.github/workflows/agent-state-claim.yml` | `retired-stale` | — | — |
| `.github/workflows/agent-state-ownership.yml` | `retired-stale` | — | — |
| `.github/workflows/delete-merged-head-branch.yml` | `maintenance-control` | — | — |
| `.github/workflows/mobile-release.yml` | `authorized-publication-deployment-device-live-evidence` | — | — |
| `.github/workflows/quality-gate.yml` | `maintenance-control` | — | — |
| `.github/workflows/quality.yml` | `final-pull-request-validation` | runner capability and deterministic prerequisite remediation | https://github.com/StreamScapeTV/directus-front/issues/146 |

## StreamScapeTV/finance-hub

- Protected integration branch: `main`
- Workflow files: `4`

| Workflow path | Trigger class(es) | Finding | Remediation owner |
|---|---|---|---|
| `.github/workflows/agent-state-claim.yml` | `retired-stale` | — | — |
| `.github/workflows/ci.yml` | `final-pull-request-validation`<br>`protected-integration-release-validation`<br>`noncompliant-unprotected-feature-branch-product-validation` | unrestricted push currently invokes product validation | https://github.com/StreamScapeTV/finance-hub/issues/221 |
| `.github/workflows/extended-ci.yml` | `final-pull-request-validation`<br>`maintenance-control` | — | — |
| `.github/workflows/one-time-current-main-baseline.yml` | `retired-stale` | — | — |

## StreamScapeTV/flux

- Protected integration branch: `main`
- Workflow files: `36`

| Workflow path | Trigger class(es) | Finding | Remediation owner |
|---|---|---|---|
| `.github/workflows/agent-state-helm-activation-smoke.yaml` | `maintenance-control` | — | — |
| `.github/workflows/arc-observer-rbac-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/database-chart-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/directus-web-release-contract.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/flux-control-plane-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/github-actions-runner-activation-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/github-actions-runner-arc-reconcile-source-contract-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/github-actions-runner-arc-recovery-contract-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/github-actions-runner-arc-recovery.yaml` | `maintenance-control` | — | — |
| `.github/workflows/github-actions-runner-buildah-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/github-actions-runner-buildah-image.yaml` | `authorized-publication-deployment-device-live-evidence` | — | — |
| `.github/workflows/github-actions-runner-buildah-smoke.yaml` | `authorized-publication-deployment-device-live-evidence` | — | — |
| `.github/workflows/github-actions-runner-capacity-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/github-actions-runner-capacity-evidence-contract-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/github-actions-runner-capacity-evidence.yaml` | `authorized-publication-deployment-device-live-evidence` | — | — |
| `.github/workflows/github-actions-runner-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/github-actions-runner-class-profiles-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/github-actions-runner-flux-control-redundancy-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/github-actions-runner-image.yaml` | `authorized-publication-deployment-device-live-evidence` | — | — |
| `.github/workflows/github-actions-runner-k3s-smoke.yaml` | `authorized-publication-deployment-device-live-evidence` | — | — |
| `.github/workflows/github-actions-runner-label-hard-cutover-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/github-actions-runner-retired-namespace-diagnostic-contract-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/github-actions-runner-retired-namespace-diagnostic.yaml` | `maintenance-control` | — | — |
| `.github/workflows/github-actions-runner-scale-set-identity-diagnostic-contract-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/github-actions-runner-scale-set-identity-diagnostic.yaml` | `maintenance-control` | — | — |
| `.github/workflows/github-actions-runner-storage-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/iptv-backend-helm-release-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/issue-ledger-contract.yaml` | `maintenance-control` | — | — |
| `.github/workflows/monitoring-operational-alerts-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/monitoring-storage-alerts-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/monitoring-subcharts-diagnostic.yaml` | `maintenance-control` | — | — |
| `.github/workflows/reconcile-allowlisted-release.yaml` | `authorized-publication-deployment-device-live-evidence` | — | — |
| `.github/workflows/resource-policy-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/storage-rightsizing-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/traefik-ingress-ci.yaml` | `final-pull-request-validation` | — | — |
| `.github/workflows/vaultwarden-backup-ci.yaml` | `final-pull-request-validation` | — | — |

## StreamScapeTV/iptv-android

- Protected integration branch: `develop`
- Workflow files: `2`

| Workflow path | Trigger class(es) | Finding | Remediation owner |
|---|---|---|---|
| `.github/workflows/android-ci.yml` | `final-pull-request-validation`<br>`protected-integration-release-validation` | — | — |
| `.github/workflows/room-schema-json-integrity.yml` | `final-pull-request-validation`<br>`protected-integration-release-validation` | — | — |

## StreamScapeTV/iptv-apple

- Protected integration branch: `develop`
- Workflow files: `2`

| Workflow path | Trigger class(es) | Finding | Remediation owner |
|---|---|---|---|
| `.github/workflows/central-apple-ci.yml` | `final-pull-request-validation`<br>`protected-integration-release-validation` | — | — |
| `.github/workflows/guarded-branch-cleanup.yml` | `maintenance-control` | — | — |

## StreamScapeTV/iptv-backend

- Protected integration branch: `main`
- Workflow files: `2`

| Workflow path | Trigger class(es) | Finding | Remediation owner |
|---|---|---|---|
| `.github/workflows/backend-ci.yml` | `final-pull-request-validation`<br>`protected-integration-release-validation` | pull_request currently admits source but skips product validation jobs | https://github.com/StreamScapeTV/iptv-backend/issues/428 |
| `.github/workflows/release.yml` | `authorized-publication-deployment-device-live-evidence` | — | — |

## StreamScapeTV/organization-rules

- Protected integration branch: `main`
- Workflow files: `0`

| Workflow path | Trigger class(es) | Finding | Remediation owner |
|---|---|---|---|
| — | — | none | — |

## StreamScapeTV/streamscape-media

- Protected integration branch: `develop`
- Workflow files: `2`

| Workflow path | Trigger class(es) | Finding | Remediation owner |
|---|---|---|---|
| `.github/workflows/central-apple-validation.yml` | `final-pull-request-validation`<br>`maintenance-control` | — | — |
| `.github/workflows/central-source-admission.yml` | `protected-integration-release-validation`<br>`maintenance-control` | — | — |

## Native skip policy

GitHub-native workflow-skip markers are forbidden for organization checkpoints because they can suppress the pull-request workflow itself. The exact machine-validated marker catalog lives in `contracts/final-candidate-lifecycle.json` and its fixtures.

## Enforcement

- Central self-check validates this contract, the exact repository/workflow count, allowed trigger classes, remediation ownership for noncompliance, the checkpoint prefix, and native-skip fixtures.
- Consumer fixes remain separate repository issues/branches/PRs; this central contract records findings but does not mutate consumer repositories.
