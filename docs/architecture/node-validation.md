# Node validation architecture

`validation.node` composes the merged source, runner, workspace, policy, evidence, cleanup, and `ciw` foundations:

`consumer trigger → source.resolve → reusable-node → ciw node validate → checked-in command profile`

## Authority boundaries

`contracts/node-validation.json` is the Node validation authority. It owns the four bounded profiles, exact runtime sources, npm-only restore behavior, repository/profile compatibility records, command argv and stage order, browser-public environment allowlists, static-output rules, stable failure codes, cleanup duties, cache default, and artifact policy.

Consumer repositories remain authoritative for application code, package manifests and lockfiles, scripts, framework configuration, assertions, domain behavior, browser/live-server proof, and deployment. Central orchestration records reviewed shapes but never branches on product names in Python or workflow YAML.

## Typed command boundary

Issue #10 adds exactly one named command:

```text
ciw node validate --phase plan|execute --source-root source
```

The `plan` phase validates immutable GitHub identity and bounded action inputs, resolves one `NodeValidationPlan`, and proves the fixed semantic `portable` runner. It executes no consumer code. The `execute` phase loads the same checked-in contract and runs only contract-owned argv.

`NodeValidationError.code` projects through the existing shared `CIWResult` / `CIWError` boundary. Expected failures expose only stable codes; unexpected exceptions project to the existing redacted `ciw_unexpected_failure` code.

## Exact source and trust

The workflow receives an exact SHA already admitted by the source contract. It checks out exact central orchestration and exact caller source without persisted credentials, then verifies both identities. Fork source may use only non-privileged compatible profiles. No profile introduces publication, deployment, registry, Kubernetes, Flux, database, device, signing, store, or Agent State authority.

## Runtime authority

The official setup action is locked as:

`actions/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38` (`v6.5.0`).

The selected compatibility record resolves one exact Node `MAJOR.MINOR.PATCH` from exactly one source:

- exact `.nvmrc`;
- exact `.node-version`; or
- an exact reviewed API value.

Ranges, aliases, `node`/`stable`/`lts` prefixes, comments, multiple values, whitespace ambiguity, and contradictory sources fail closed. The installed `node --version` must equal the resolved version. Relevant `package.json` Node/npm engine constraints must accept the verified runtime.

## Npm-only dependency restore

V1 supports npm only. Alternate package-manager metadata or commands for Yarn, pnpm, Bun, or Corepack-selected alternatives fail closed. `npm install` is forbidden. `npm-ci` requires a tracked `package-lock.json` with lockfile version 3 and invokes only:

```text
npm ci --no-audit --no-fund
```

Home, npm cache, npm config, XDG roots, and temporary state are created under registered workflow state. Setup-node package-manager caching and central cache transport remain disabled. Manifest and lockfile hashes are captured before restore and compared after every command stage.

The `node-source-audit` profile performs no dependency restore.

## Command profiles

Checked-in stage shapes are:

- `quality-test`;
- `quality-test-build`;
- `contract-test-build`;
- `source-audit`.

Every stage has a fixed argv list in the contract. No caller command, shell body, callback, module, function, argument vector, package-manager command, or inherited environment file exists. Exit status is preserved because commands run directly without a shell pipeline. A caller-owned hook or verifier must be a tracked regular non-symlink file beneath the contracted source root and is selected only by the compatibility record.

## Browser-public environment

A compatibility record may allow only the reviewed subset of `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_API_BASE_URL`, and `NEXT_PUBLIC_PROJECT`. Input is canonical JSON and is rejected for unknown names, controls, multiline values, token-like content, GitHub/secret expressions, excess size, or use by a profile without the corresponding allowlist.

The process receives only a bounded central environment plus selected public values. Values are not written to `.env*`, printed, summarized, cached, evidenced, or retained. `NEXT_TELEMETRY_DISABLED=1` is central internal state.

## Static output

Build profiles select one normalized output directory below their working directory. Traversal, symlinks, missing/empty output, excessive file count or bytes, server directories, Pages Functions, Worker bundles, OpenNext state, Wrangler configuration, or deployment bundles fail closed. At least one static HTML file is required.

Where a consumer verifier is required, the exact tracked verifier runs with fixed contract arguments. Generic structural verification still applies afterward. The output digest is deterministic over sorted relative paths and file digests. Output is never uploaded, published, or deployed.

## Consumer boundaries

- StreamScapeWeb retains browser-only static export, its checked-in Pages-output verifier, environment validation, and Cloudflare Pages Git deployment outside GitHub Actions.
- Agent State frontend receives only a future consumer-owned locked Node adoption path; backend, PostgreSQL, lifecycle, release, Flux, Agent State decisions, and Supabase remain separate.
- Agent State Dashboard uses the existing `frontend-contract-static` / `contract-test-build` boundary with exact `.nvmrc` Node 22.18.0, locked `npm ci`, direct `npm test`, the checked-in `npm run validate` script for test/typecheck/build, generic `out/` structural verification, and no browser-public environment values. NGINX, the local server process, Helm, image publication, runtime secrets, and deployment remain dashboard-owned and outside `validation.node`.
- Finance keeps domain and audit semantics in `tool/ci_quality_gate.sh`; manual Cypress/live-server evidence remains Finance-owned.

## Mutation detection and cleanup

Consumer source is copied into registered state after symlink rejection. Commands execute only in the copy. Tracked diffs, staged diffs, unexpected untracked paths, manifest drift, lockfile drift, source-SHA changes, or original source residue fail closed.

Always-cleanup removes copied work, `node_modules`, `.next`, `out`, the declared output, npm state, coverage, reports, generated inventories, Cypress screenshots/videos/cache, logs, `.eslintcache`, `.tsbuildinfo`, Python bytecode produced by audit hooks, and every registered path. Removal does not follow symlinks. Workspace cleanup then applies the merged marker-bound descriptor-anchored no-follow implementation under `if: always()`.

The final workflow verifies exact source equality, clean tracked and untracked state, successful cleanup, and zero retained Actions artifacts.

## Deliberate exclusions

Issue #10 does not implement Python changes beyond the shared command registry extension, Android, Flutter, Apple, devices, GitOps, OCI/Helm, release, publication, deployment, Flux reconciliation, canonical Supabase, or Agent State decision behavior. It does not modify `.github/workflows/self-check.yml`, runner infrastructure, organization-rules, or consumer repositories.
