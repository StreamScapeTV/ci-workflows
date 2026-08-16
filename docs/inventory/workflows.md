# Organization workflow inventory

Capture date: `2026-08-09`

This navigation snapshot classifies **87 workflow files across 11 repositories**. It is generic organization-maintenance evidence, not a consumer/product compatibility allowlist or reusable-API admission source.

The complete per-workflow ledger remains machine-readable in `contracts/workflow-inventory.json`. Capture commits are evidence anchors; the live drift check compares workflow paths and available Git blob identities without checking out or executing consumer source.

## Summary

| Repository | Captured source | Workflows | Active | Retire | Publication | Device | Flux-authorized |
|---|---|---:|---:|---:|---:|---:|---:|
| StreamScapeTV/agent-state | `main@bf4b6122db1f248058a27b4b449cd1ecb9688a85` | 10 | 5 | 5 | 2 | 0 | 0 |
| StreamScapeTV/ci-workflows | `main@a92863f5e0ac2988b0216f934c0aa3d780a05dfd` | 2 | 2 | 0 | 1 | 0 | 0 |
| StreamScapeTV/directus-front | `main@b7b21be7129859e45fdc57befcf85b86b82e7575` | 5 | 3 | 2 | 0 | 0 | 0 |
| StreamScapeTV/finance-hub | `main@a549e610dee8f7f80b1a38ae4b709b8d2bd53461` | 4 | 2 | 2 | 0 | 0 | 0 |
| StreamScapeTV/flux | `main@8e611816f11c4ebaf126217646e030b9c2df18b9` | 18 | 17 | 1 | 2 | 0 | 2 |
| StreamScapeTV/iptv-android | `develop@898b95fdbfec894299428ccfb8eb491be89030da` | 7 | 5 | 2 | 0 | 1 | 0 |
| StreamScapeTV/iptv-apple | `develop@258ca9f70f432b5364ebeea9f55e3a42e1cc3281` | 13 | 7 | 6 | 0 | 0 | 0 |
| StreamScapeTV/iptv-backend | `main@982275628f41c4fac1d652b05ca8ff1cee7eb151` | 6 | 3 | 3 | 1 | 0 | 0 |
| StreamScapeTV/organization-rules | `main@ee4ec47cae055c77b574f029182357d0b4bf0350` | 0 | 0 | 0 | 0 | 0 | 0 |
| StreamScapeTV/streamscape-media | `develop@7cf106c5fea32583cd12fea56bda6bb402ed49f6` | 18 | 12 | 6 | 2 | 4 | 0 |
| StreamScapeTV/StreamScapeWeb | `main@3cd28e7ae45b6503464ece2e0df70b4cb2031b81` | 4 | 2 | 2 | 0 | 0 | 0 |

## Classification totals

| Dimension | Code | Meaning | Count |
|---|---|---|---:|
| Disposition | `public` | central public reusable workflow | 4 |
| Disposition | `internal` | central internal reusable workflow | 0 |
| Disposition | `function` | central named function or composite action | 0 |
| Disposition | `thin` | thin repository caller | 54 |
| Disposition | `owned` | repository-owned product command/contract/policy/data | 0 |
| Disposition | `retire` | temporary/repair workflow to retire | 29 |
| Trust | `read` | read-only-validation | 42 |
| Trust | `publish` | trusted-publication | 8 |
| Trust | `maintenance` | trusted-maintenance | 13 |
| Trust | `device` | physical-device-validation | 5 |
| Trust | `flux` | trusted-flux-reconciliation | 2 |
| Trust | `legacy-agent-state` | retired-forbidden-operating-path | 17 |
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
| Migration | `retire` | Remove after bounded recovery/fixture/one-shot evidence is preserved. | 30 |
| Migration | `products` | Central OCI/Helm build, validation, publication, and read-back. | 0 |
| Migration | `other` | Resolve through the linked adoption issue without product-name branching. | 0 |

## Drift and update contract

- `python3 scripts/ci/inventory_contract.py validate` validates this generic workflow-navigation snapshot and generated-report agreement.
- `python3 scripts/ci/inventory_contract.py render` regenerates this report deterministically.
- `python3 scripts/ci/inventory_live_check.py` compares configured organization workflow trees using a read-only contents token.
- Live comparison never checks out or executes consumer source and needs no product, Agent State mutation, registry, signing, SOPS, Kubernetes, or device credential.
- A workflow add, removal, rename, or changed recorded blob requires an inventory update in the same reviewed change.
- This snapshot never decides whether a repository or product may call a reusable workflow; ordinary compatibility is capability/trust/input based.

Ownership decisions remain documented in `docs/architecture/ownership-boundaries.md`. Navigation-only repository/workflow discovery does not participate in public API validation.
