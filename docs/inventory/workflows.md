# Organization workflow inventory

Capture date: `2026-08-06`

This contract classifies **88 live workflow files across 11 repositories**. Capture commits are evidence anchors; the live drift check compares workflow paths and available Git blob identities without checking out or executing consumer source.

## Summary

| Repository | Captured source | Workflows | Active | Retire | Publication | Device | Flux-authorized |
|---|---|---:|---:|---:|---:|---:|---:|
| StreamScapeTV/agent-state | `main@bf4b6122db1f248058a27b4b449cd1ecb9688a85` | 10 | 8 | 2 | 2 | 0 | 0 |
| StreamScapeTV/ci-workflows | `main@668e1cdbb4c62eb08811959932ba2ef0d697e196` | 3 | 3 | 0 | 1 | 0 | 0 |
| StreamScapeTV/directus-front | `main@b7b21be7129859e45fdc57befcf85b86b82e7575` | 5 | 5 | 0 | 0 | 0 | 0 |
| StreamScapeTV/finance-hub | `main@a549e610dee8f7f80b1a38ae4b709b8d2bd53461` | 4 | 3 | 1 | 0 | 0 | 0 |
| StreamScapeTV/flux | `main@8e611816f11c4ebaf126217646e030b9c2df18b9` | 18 | 18 | 0 | 2 | 0 | 2 |
| StreamScapeTV/iptv-android | `develop@898b95fdbfec894299428ccfb8eb491be89030da` | 7 | 7 | 0 | 0 | 1 | 0 |
| StreamScapeTV/iptv-apple | `develop@258ca9f70f432b5364ebeea9f55e3a42e1cc3281` | 13 | 9 | 4 | 0 | 0 | 0 |
| StreamScapeTV/iptv-backend | `main@982275628f41c4fac1d652b05ca8ff1cee7eb151` | 6 | 5 | 1 | 1 | 0 | 0 |
| StreamScapeTV/organization-rules | `main@c6d255885f9567f275b53f4fdccaa4927267d2b1` | 0 | 0 | 0 | 0 | 0 | 0 |
| StreamScapeTV/streamscape-media | `develop@7cf106c5fea32583cd12fea56bda6bb402ed49f6` | 18 | 14 | 4 | 2 | 4 | 0 |
| StreamScapeTV/StreamScapeWeb | `main@3cd28e7ae45b6503464ece2e0df70b4cb2031b81` | 4 | 4 | 0 | 0 | 0 | 0 |

## Classification totals

| Dimension | Code | Meaning | Count |
|---|---|---|---:|
| Disposition | `public` | central public reusable workflow | 6 |
| Disposition | `internal` | central internal reusable workflow | 0 |
| Disposition | `function` | central named function or composite action | 0 |
| Disposition | `thin` | thin repository caller | 70 |
| Disposition | `owned` | repository-owned product command/contract/policy/data | 0 |
| Disposition | `retire` | temporary/repair workflow to retire | 12 |
| Trust | `read` | read-only-validation | 42 |
| Trust | `agent-state` | agent-state-transport | 18 |
| Trust | `publish` | trusted-publication | 8 |
| Trust | `maintenance` | trusted-maintenance | 13 |
| Trust | `device` | physical-device-validation | 5 |
| Trust | `flux` | trusted-flux-reconciliation | 2 |
| Migration | `agent-state-lifecycle` | Central Agent State lifecycle transport from issue #32. | 11 |
| Migration | `agent-state-ownership` | Central Agent State ownership transport from issue #32; retain repository issue-ledger policy where applicable. | 7 |
| Migration | `release` | Tag-only central release orchestration with immutable product read-back. | 3 |
| Migration | `artifact-cleanup` | Central organization artifact cleanup. | 1 |
| Migration | `infra-retry` | Central bounded runner-infrastructure retry. | 1 |
| Migration | `branch-hygiene` | Central exact merged-branch hygiene. | 4 |
| Migration | `device` | Explicitly authorized reusable physical-device/live validation. | 5 |
| Migration | `flutter` | Reusable Flutter quality and unsigned build validation. | 4 |
| Migration | `node` | Reusable Node/Next static-export validation. | 2 |
| Migration | `android` | Reusable Android/Gradle validation; no OCI/Jib/signing. | 5 |
| Migration | `apple` | Reusable Apple simulator/macOS or bounded visual evidence orchestration; no signing. | 6 |
| Migration | `flux-assets` | Reusable Flux runner-image/chart build, publish, read-back, canary, and manifest orchestration. | 8 |
| Migration | `flux-reconcile` | Central trusted Flux wrapper executing exact Flux-owned policy and allowlist source. | 1 |
| Migration | `gitops` | Reusable source-only YAML/Helm/Kustomize validation; retain Flux/product policy in owner. | 6 |
| Migration | `conformance` | Central workflow/action/runner/security conformance; retain repository-specific assertions. | 6 |
| Migration | `python` | Reusable exact-source Python/PostgreSQL validation and product-contract checks. | 2 |
| Migration | `media` | Reusable Media Android/Apple/native validation or inventory-confirmed native release verification. | 3 |
| Migration | `policy-validation` | Central source-only validation of organization-rules; policy remains owned there. | 0 |
| Migration | `retire` | Remove after bounded recovery/fixture/one-shot evidence is preserved. | 13 |
| Migration | `products` | Central OCI/Helm build, validation, publication, and read-back. | 0 |
| Migration | `other` | Resolve through the linked adoption issue without product-name branching. | 0 |

## Repository workflow ledger

### StreamScapeTV/agent-state

- Capture: `main@bf4b6122db1f248058a27b4b449cd1ecb9688a85`
- Evidence basis: exact file reads, repository instructions, and maintenance implementation commits

| Workflow | Name | Status | Disposition | Migration | Trust | Blob |
|---|---|---|---|---|---|---|
| `.github/workflows/agent-state-claim.yml` | Agent State lifecycle caller | `current` | `thin` | `agent-state-lifecycle` | `agent-state` | `415225b806e3b334753c43599c65d741f845d4ae` |
| `.github/workflows/agent-state-lifecycle.yml` | Reusable Agent State lifecycle | `current-local-reusable` | `public` | `agent-state-lifecycle` | `agent-state` | `1b77021c11a4b29257d470f6ac60b6ec0c44c8b3` |
| `.github/workflows/agent-state-ownership.yml` | Reusable Agent State ownership | `current-local-reusable` | `public` | `agent-state-ownership` | `agent-state` | `a6a0f48ade6f31eb99ec627d45387481fd4da52b` |
| `.github/workflows/branch-hygiene.yml` | Branch Hygiene | `current-maintenance` | `public` | `branch-hygiene` | `maintenance` | — |
| `.github/workflows/organization-artifact-cleanup.yml` | Organization Actions Artifact Cleanup | `current-maintenance` | `public` | `artifact-cleanup` | `maintenance` | — |
| `.github/workflows/organization-runner-infrastructure-retry.yml` | Organization Runner Infrastructure Retry | `current-maintenance` | `public` | `infra-retry` | `maintenance` | — |
| `.github/workflows/recover-release-1-0-65.yml` | Recover release 1.0.65 | `historical-recovery` | `retire` | `retire` | `publish` | `25a1a062b110d26fd4e6eaa90d6e7c219442ff0f` |
| `.github/workflows/release.yml` | Agent State release | `current-but-trigger-must-change` | `thin` | `release` | `publish` | `f1cbb94d5491de0dc031047940a57c391533f95e` |
| `.github/workflows/runner-infrastructure-retry-fixture.yml` | Runner infrastructure retry fixture | `fixture` | `retire` | `retire` | `maintenance` | `46bcb1d1bae63f8ef82c3335f19871ccfc52086d` |
| `.github/workflows/test.yml` | Agent State Tests | `current` | `thin` | `python` | `read` | `1be79265c6ba21c8d023fd54f1bc22733d7f2ebf` |

### StreamScapeTV/ci-workflows

- Capture: `main@668e1cdbb4c62eb08811959932ba2ef0d697e196`
- Evidence basis: exact issue #37 parameterized Agent State command checkpoint

| Workflow | Name | Status | Disposition | Migration | Trust | Blob |
|---|---|---|---|---|---|---|
| `.github/workflows/agent-state-command.yml` | Agent State command | `current-manual-control` | `thin` | `agent-state-lifecycle` | `agent-state` | `507e6458d72a39c1a8fb806e9a35dbcaae12fde0` |
| `.github/workflows/reusable-tag-image-chart.yml` | Reusable exact-tag image and Helm release | `current-bootstrap-exception` | `public` | `release` | `publish` | `289bc0844a36923e3bb856c21dfed0329a4c6a95` |
| `.github/workflows/self-check.yml` | Central workflow self-check | `current` | `thin` | `conformance` | `read` | `2a937e534b4e7ea02acf01b62b4da92e1bc1d06e` |

### StreamScapeTV/directus-front

- Capture: `main@b7b21be7129859e45fdc57befcf85b86b82e7575`
- Evidence basis: exact file reads and migration issue

| Workflow | Name | Status | Disposition | Migration | Trust | Blob |
|---|---|---|---|---|---|---|
| `.github/workflows/agent-state-claim.yml` | Agent State lifecycle | `current` | `thin` | `agent-state-lifecycle` | `agent-state` | `7cabbb4a72e1fe8fb2160f70a6585fa137e60957` |
| `.github/workflows/agent-state-ownership.yml` | Agent State ownership | `current` | `thin` | `agent-state-ownership` | `agent-state` | `aadb2ce722cb525151a24ff0298c2daa4d0b0c02` |
| `.github/workflows/delete-merged-head-branch.yml` | Delete merged head branch | `current-maintenance` | `thin` | `branch-hygiene` | `maintenance` | `21ced1799150c7914257dcd007ee2486d393fc9b` |
| `.github/workflows/quality-gate.yml` | Reusable Quality Gate | `current-local-reusable` | `thin` | `flutter` | `read` | `2d55dc10f28cd4eb9d49b55ddccc958858fbba61` |
| `.github/workflows/quality.yml` | Quality | `current` | `thin` | `flutter` | `read` | `337e44054d214fdb2cb70a69f4adbec26cff73a7` |

### StreamScapeTV/finance-hub

- Capture: `main@a549e610dee8f7f80b1a38ae4b709b8d2bd53461`
- Evidence basis: exact file reads and migration issue

| Workflow | Name | Status | Disposition | Migration | Trust | Blob |
|---|---|---|---|---|---|---|
| `.github/workflows/agent-state-claim.yml` | Agent State lifecycle | `current` | `thin` | `agent-state-lifecycle` | `agent-state` | `604efbf6db5de99ef368a6cf0e2907e038a2ca4b` |
| `.github/workflows/ci.yml` | Finance Hub CI | `current` | `thin` | `flutter` | `read` | `7085591b17ef80c619e50ef9b2a9f22e4c7f4ce5` |
| `.github/workflows/extended-ci.yml` | Finance Hub extended CI | `current-opt-in` | `thin` | `flutter` | `read` | `d0e86c5dc1d9b60d3cd8c404bb70a7266139ef93` |
| `.github/workflows/one-time-current-main-baseline.yml` | One-time current main baseline | `one-time` | `retire` | `retire` | `read` | `73aed72d961caf6538c402f42ca4176db2886543` |

### StreamScapeTV/flux

- Capture: `main@8e611816f11c4ebaf126217646e030b9c2df18b9`
- Evidence basis: exact file reads and authoritative Flux migration/runner commits

| Workflow | Name | Status | Disposition | Migration | Trust | Blob |
|---|---|---|---|---|---|---|
| `.github/workflows/agent-state-claim.yml` | Agent State lifecycle | `current` | `thin` | `agent-state-lifecycle` | `agent-state` | `595d4bff2ae596fcacf9aaaa33774e656122e033` |
| `.github/workflows/arc-observer-rbac-ci.yaml` | Validate read-only ARC observer RBAC | `current` | `thin` | `gitops` | `read` | `b71d7614b32071dfad3a6b25e222770095ada2ef` |
| `.github/workflows/database-chart-ci.yaml` | Validate database chart backup and restore matrix | `current` | `thin` | `gitops` | `read` | `1e61fa2cc3e013f3a4035491c9fd242d593ce2ff` |
| `.github/workflows/directus-web-release-contract.yaml` | Validate Directus Web immutable asset contract | `current` | `thin` | `gitops` | `read` | `eeb060c7dd8d9e770bda11ca5c0daa6e6507b2a7` |
| `.github/workflows/github-actions-runner-activation-ci.yaml` | Verify private ARC activation prerequisites | `current` | `thin` | `flux-assets` | `read` | `94a602c64cd4eb0ed377e997ed782a0a68ca45b0` |
| `.github/workflows/github-actions-runner-buildah-ci.yaml` | Validate Buildah ARC runner class | `current` | `thin` | `flux-assets` | `read` | `af0d4405c488a6dee18dc054ca9f48eb2fe5942f` |
| `.github/workflows/github-actions-runner-buildah-image.yaml` | Publish daemonless Buildah runner image | `current-publication` | `thin` | `flux-assets` | `publish` | `62cd26cb097ad595d876df3d483e378fee376d1c` |
| `.github/workflows/github-actions-runner-buildah-smoke.yaml` | Prove one Buildah ARC OCI lifecycle | `current-manual-canary` | `thin` | `flux-assets` | `flux` | `09020aa25b1e898a76904475da12661d91447588` |
| `.github/workflows/github-actions-runner-capacity-ci.yaml` | Validate memory-bound ARC namespace capacity | `current` | `thin` | `flux-assets` | `read` | `113c64b841a9a7ba20f8887d57bae43957590142` |
| `.github/workflows/github-actions-runner-ci.yaml` | Validate GitHub Actions runner chart and cluster source | `current` | `thin` | `flux-assets` | `read` | `2bce55581b1c8f0413f9d4a6763b13969b20e4f3` |
| `.github/workflows/github-actions-runner-class-profiles-ci.yaml` | Validate memory-bound ARC class profiles | `current` | `thin` | `flux-assets` | `read` | `d3c28d96798e1b38b94dd5891d050d0971b8bedb` |
| `.github/workflows/github-actions-runner-image.yaml` | Publish GitHub Actions runner image and ARC charts | `current-publication` | `thin` | `flux-assets` | `publish` | `5a7241412cb910edc07f810992455fd3efa1f393` |
| `.github/workflows/issue-ledger-contract.yaml` | Validate Flux issue-ledger contract | `current-manual` | `thin` | `conformance` | `read` | `2bc608d462b3adc4ff53d11031d63623d4d2002f` |
| `.github/workflows/monitoring-storage-alerts-ci.yaml` | Validate monitoring storage alerts | `current` | `thin` | `gitops` | `read` | `13a9442f2566d1b4ed6cd1212185772373c9dbf3` |
| `.github/workflows/reconcile-allowlisted-release.yaml` | Reconcile allowlisted Flux release | `current-privileged` | `thin` | `flux-reconcile` | `flux` | `33ccdde6de34cb1330294fe864405b72d8cdd13d` |
| `.github/workflows/resource-policy-ci.yaml` | Validate cluster resource and priority policy | `current-mixed-source-live` | `thin` | `conformance` | `read` | `94c5f690e8cc67ddb9848df9d92a1b1e31397fc7` |
| `.github/workflows/storage-rightsizing-ci.yaml` | Validate Longhorn volume rightsizing | `current` | `thin` | `gitops` | `read` | `ae153aad6940f81202a492a0e40986ab1954a9fc` |
| `.github/workflows/vaultwarden-backup-ci.yaml` | Validate Vaultwarden backup toolchain | `current` | `thin` | `gitops` | `read` | `9d8c6bd504da4f31d333c5854fabb8f28d5a44e2` |

### StreamScapeTV/iptv-android

- Capture: `develop@898b95fdbfec894299428ccfb8eb491be89030da`
- Evidence basis: live indexed workflow tree

| Workflow | Name | Status | Disposition | Migration | Trust | Blob |
|---|---|---|---|---|---|---|
| `.github/workflows/agent-state-claim.yml` | Agent State lifecycle | `current` | `thin` | `agent-state-lifecycle` | `agent-state` | — |
| `.github/workflows/android-ci.yml` | Android CI | `current` | `thin` | `android` | `read` | — |
| `.github/workflows/android-live-backend-acceptance.yml` | Android live backend acceptance | `current-opt-in` | `thin` | `device` | `device` | — |
| `.github/workflows/android-release-validation.yml` | Android release validation | `current-nonpublishing` | `thin` | `android` | `read` | — |
| `.github/workflows/governance-ci.yml` | Governance CI | `current` | `thin` | `android` | `read` | — |
| `.github/workflows/pr-agent-state-policy.yml` | PR Agent State policy | `current` | `thin` | `agent-state-ownership` | `agent-state` | — |
| `.github/workflows/room-schema-json-integrity.yml` | Room schema JSON integrity | `current` | `thin` | `android` | `read` | — |

### StreamScapeTV/iptv-apple

- Capture: `develop@258ca9f70f432b5364ebeea9f55e3a42e1cc3281`
- Evidence basis: live indexed workflow tree

| Workflow | Name | Status | Disposition | Migration | Trust | Blob |
|---|---|---|---|---|---|---|
| `.github/workflows/agent-state-claim.yml` | Agent State lifecycle | `current` | `thin` | `agent-state-lifecycle` | `agent-state` | — |
| `.github/workflows/agent-state-ownership.yml` | Agent State ownership | `current` | `thin` | `agent-state-ownership` | `agent-state` | — |
| `.github/workflows/buildah-ci-policy.yml` | Buildah CI policy | `current-generic-policy` | `thin` | `conformance` | `read` | — |
| `.github/workflows/develop-ci.yml` | Develop CI | `current` | `thin` | `apple` | `read` | — |
| `.github/workflows/guarded-branch-cleanup.yml` | Guarded branch cleanup | `current-maintenance` | `thin` | `branch-hygiene` | `maintenance` | — |
| `.github/workflows/restoration-final-dispatch.yml` | Restoration final dispatch | `recovery` | `retire` | `retire` | `read` | — |
| `.github/workflows/restoration-final-linux.yml` | Restoration final Linux | `recovery` | `retire` | `retire` | `read` | — |
| `.github/workflows/visual-attachment-recovery-v2.yml` | Visual attachment recovery v2 | `recovery` | `retire` | `retire` | `read` | — |
| `.github/workflows/visual-attachment-recovery.yml` | Visual attachment recovery | `superseded` | `retire` | `retire` | `read` | — |
| `.github/workflows/visual-baseline-proof.yml` | Visual baseline proof | `current-bounded-evidence` | `thin` | `apple` | `read` | — |
| `.github/workflows/visual-macos-review-proof.yml` | Visual macOS review proof | `current-bounded-evidence` | `thin` | `apple` | `read` | — |
| `.github/workflows/visual-review-proof.yml` | Visual review proof | `current-bounded-evidence` | `thin` | `apple` | `read` | — |
| `.github/workflows/visual-tvos-review-proof.yml` | Visual tvOS review proof | `current-bounded-evidence` | `thin` | `apple` | `read` | — |

### StreamScapeTV/iptv-backend

- Capture: `main@982275628f41c4fac1d652b05ca8ff1cee7eb151`
- Evidence basis: live indexed tree and exact workflow reads; later product-only commits did not change workflow blobs

| Workflow | Name | Status | Disposition | Migration | Trust | Blob |
|---|---|---|---|---|---|---|
| `.github/workflows/agent-state-claim.yml` | Agent State Claim Bridge | `current` | `thin` | `agent-state-lifecycle` | `agent-state` | `18ccfce8723b37f27f056db262a517aeb52ea2d8` |
| `.github/workflows/agent-state-ownership.yml` | Agent State ownership | `current` | `thin` | `agent-state-ownership` | `agent-state` | `8f10a7f7d546214e79543d4e0ad2f04a9bdba6f1` |
| `.github/workflows/backend-ci.yml` | Backend CI | `current` | `thin` | `python` | `read` | `fd853d50eeeec233e9c9a33d72c748c8a2667b21` |
| `.github/workflows/issue121-dispatch-final-validation.yml` | Issue 121 exact validation control | `temporary-repair` | `retire` | `retire` | `maintenance` | `f7ade46a39d19047423313926c0a83b28e4f478c` |
| `.github/workflows/release.yml` | Publish private release | `current-but-trigger-must-change` | `thin` | `release` | `publish` | `45f6c3513ab5b3fb72df00e8d734a88e2c81b730` |
| `.github/workflows/workflow-policy.yml` | Workflow policy | `current` | `thin` | `conformance` | `read` | `225d16f70542041cc2e074c4ba7abc01612484eb` |

### StreamScapeTV/organization-rules

- Capture: `main@c6d255885f9567f275b53f4fdccaa4927267d2b1`
- Evidence basis: exact current policy reduction commit; repository intentionally contains no GitHub Actions workflows

| Workflow | Name | Status | Disposition | Migration | Trust | Blob |
|---|---|---|---|---|---|---|

### StreamScapeTV/streamscape-media

- Capture: `develop@7cf106c5fea32583cd12fea56bda6bb402ed49f6`
- Evidence basis: live indexed workflow tree; index path set confirmed against current develop history

| Workflow | Name | Status | Disposition | Migration | Trust | Blob |
|---|---|---|---|---|---|---|
| `.github/workflows/agent-ci.yml` | Agent CI | `duplicate-current` | `thin` | `retire` | `read` | — |
| `.github/workflows/agent-state-claim.yml` | Agent State lifecycle | `current` | `thin` | `agent-state-lifecycle` | `agent-state` | — |
| `.github/workflows/agent-state-ownership.yml` | Agent State ownership | `current` | `thin` | `agent-state-ownership` | `agent-state` | — |
| `.github/workflows/agent2-apply-433.yml` | Agent 2 apply 433 | `temporary-issue-specific` | `retire` | `retire` | `maintenance` | — |
| `.github/workflows/agent2-run-433.yml` | Agent 2 run 433 | `temporary-issue-specific` | `retire` | `retire` | `maintenance` | — |
| `.github/workflows/agent2-validate-ios-444.yml` | Agent 2 validate iOS 444 | `temporary-issue-specific` | `retire` | `retire` | `maintenance` | — |
| `.github/workflows/android-ci.yml` | Android CI | `current` | `thin` | `android` | `read` | — |
| `.github/workflows/ci-label-policy.yml` | CI label policy | `current-policy` | `thin` | `conformance` | `maintenance` | — |
| `.github/workflows/cleanup-merged-issue-branch.yml` | Cleanup merged issue branch | `current-maintenance` | `thin` | `branch-hygiene` | `maintenance` | — |
| `.github/workflows/develop-ci.yml` | Develop CI | `current` | `thin` | `media` | `read` | — |
| `.github/workflows/ios-ci.yml` | iOS CI | `current` | `thin` | `apple` | `read` | — |
| `.github/workflows/ios-device-ci.yml` | iOS device CI | `current-opt-in` | `thin` | `device` | `device` | — |
| `.github/workflows/ios-native-aggregate-device-ci.yml` | iOS native aggregate device CI | `current-opt-in` | `thin` | `device` | `device` | — |
| `.github/workflows/ios-video-output-device-ci.yml` | iOS video output device CI | `current-opt-in` | `thin` | `device` | `device` | — |
| `.github/workflows/release-verify.yml` | Release verify | `current-native-release` | `thin` | `media` | `publish` | — |
| `.github/workflows/release.yml` | Release | `current-native-release` | `thin` | `media` | `publish` | — |
| `.github/workflows/sync-local-develop-ref.yml` | Sync local develop ref | `temporary-maintenance` | `retire` | `retire` | `maintenance` | — |
| `.github/workflows/tvos-avfoundation-device-ci.yml` | tvOS AVFoundation device CI | `current-opt-in` | `thin` | `device` | `device` | — |

### StreamScapeTV/StreamScapeWeb

- Capture: `main@3cd28e7ae45b6503464ece2e0df70b4cb2031b81`
- Evidence basis: adoption issue and exact repository history; code index unavailable

| Workflow | Name | Status | Disposition | Migration | Trust | Blob |
|---|---|---|---|---|---|---|
| `.github/workflows/agent-state-claim.yml` | Agent State lifecycle | `current` | `thin` | `agent-state-lifecycle` | `agent-state` | — |
| `.github/workflows/agent-state-ownership.yml` | Agent State ownership | `current` | `thin` | `agent-state-ownership` | `agent-state` | — |
| `.github/workflows/baseline.yml` | Baseline | `current` | `thin` | `node` | `read` | — |
| `.github/workflows/branch-feedback.yml` | Branch feedback | `current-narrow-or-superseded` | `thin` | `node` | `read` | — |

## Drift and update contract

- `python3 scripts/ci/inventory_contract.py validate` validates repository, workflow, product, authority, and generated-report agreement.
- `python3 scripts/ci/inventory_contract.py render` regenerates this report deterministically.
- `python3 scripts/ci/inventory_live_check.py` compares current organization workflow trees using a read-only contents token.
- Live comparison never checks out or executes consumer source and needs no product, Agent State mutation, registry, signing, SOPS, Kubernetes, or device credential.
- A workflow add, removal, rename, or changed recorded blob requires an inventory update in the same reviewed change.

Ownership decisions are documented in `docs/architecture/ownership-boundaries.md`; product and explicit non-product decisions are recorded in `contracts/products.json`.
