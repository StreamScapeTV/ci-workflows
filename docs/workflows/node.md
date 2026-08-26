# Node validation workflow

Public API: `validation.node` `1.0.0`  
Workflow: `.github/workflows/reusable-node.yml`  
Stable check: `CI / Node validation`

The reusable workflow validates one exact admitted source SHA through checked-in Node command profiles. It is read-only, npm-only, cache-disabled by default, product-neutral, and exposes no functional artifact-upload surface in v1.

## Profiles

| Profile | Restore | Commands | Output |
|---|---|---|---|
| `locked-node` | exact `npm ci --no-audit --no-fund` | reviewed quality, type, and test stages | none |
| `next-static-export` | exact npm lockfile v3 | reviewed quality, test, build, and checked-in verifier | browser-only static output |
| `frontend-contract-static` | exact npm lockfile v3 | reviewed contract tests and static build | bounded generic static output |
| `node-source-audit` | none | one checked-in audit hook with fixed arguments | none |

All profiles use semantic `portable`. Callers cannot supply concrete labels, hosts, engines, caches, registries, package-manager commands, matrices, or infrastructure identities.

## Execution backend

`execution_backend` is optional and defaults to `organization`, which preserves the existing `general-small` organization selector exactly. A caller may explicitly request `github-hosted`; Central then maps the same Node workload plan to the fixed standard `ubuntu-latest` selector. The caller never supplies `ubuntu-latest` or any other concrete runner label.

Backend choice changes scheduling only. Node version authority, npm-only restore, command profile, source trust, workspace isolation, cleanup, and outputs remain identical. Unknown backend values and unsupported backend/profile combinations fail closed. Repository visibility does not automatically select a backend.

The small trusted planning job remains on organization general capacity and emits the exact Central-owned `runs_on_json` used by the validation job. The substantial Node execution is what moves to GitHub-hosted capacity when `execution_backend: github-hosted` is requested.

`contracts/runner-execution-backends.json` records the bounded backend names, default, and fixed hosted selector. `src/ci_workflows/execution_backends.py` enforces the same small mapping; `contracts/runner-profiles.json` remains the separate authority for the organization semantic selector.

## Public inputs

- `execution_backend`: optional `organization` or `github-hosted`; default `organization`.
- `admitted_sha`: exact lowercase source SHA already admitted by `source.resolve`.
- `validation_profile`: one of the four reviewed profiles.
- exactly one of `version_file` or exact `node_version`, as permitted by the selected fixture.
- `working_directory`, `install_profile`, `command_profile`, `script_path`, `static_output_directory`, and `output_verifier_path`: values that must exactly match checked-in compatibility data.
- `public_environment`: canonical JSON containing only reviewed browser-public keys allowed by the selected fixture.
- `artifact_exception_id`: reserved compatibility input; v1 requires it to be empty because Node validation declares no Actions artifact output.

The API never accepts arbitrary command text, shell, arguments, callbacks, modules, functions, environment-file paths, refs, runner labels, hosts, engines, registries, secrets, database URLs, Cloudflare deployment, Workers, Wrangler, signing, devices, Flux targets, Kubernetes targets, publication, or arbitrary artifact uploads.

## Public outputs

- `result`;
- exact `node_version` and `npm_version`;
- `install_result`, `test_summary`, and `build_result`;
- `output_verified` and optional deterministic `output_digest`;
- `clean_tree`, `cleanup_result`, and `artifact_exception_used`;
- deterministic bounded `evidence_id`.

No command output, public browser value, host path, package cache, source content, deployment value, token, or secret is exposed.

## Shared Central helper reuse

Consumers do not need a second token or permission to clone `StreamScapeTV/ci-workflows`. `validation.node` consumes the current first-party Central library through `actions/validate-node@main`, `actions/exact-checkout@main`, `actions/prepare-workspace@main`, `actions/render-evidence@main`, and `actions/cleanup-workspace@main`. These actions are not independently versioned components and there is no per-action SHA/checkpoint registry or helper-version propagation mechanism.

The composites resolve Central scripts and libraries relative to `GITHUB_ACTION_PATH`, so the public workflow has no Central repository checkout, no `.ciw` clone, no caller-visible Central source selector, and no new workflow secret. Whole-repository SHAs and stable repository tags remain supported snapshots when a caller deliberately needs one; ordinary development follows the active `@main` library channel.

`exact-checkout@main` still checks out only the admitted caller repository/source SHA. Its optional token defaults to the caller-scoped `github.token` and is never persisted. Central helper access therefore remains separate from caller-source checkout and from product dependency credentials.

## Runtime and restore

The planner resolves exactly one canonical `MAJOR.MINOR.PATCH` from an exact `.nvmrc`, exact `.node-version`, or a reviewed exact API value. Ranges, aliases, prefixes, comments, multiple values, malformed files, and contradictory sources fail closed. `package.json` Node/npm engine bounds must accept the resolved runtime.

The workflow uses the ordinary upstream `actions/setup-node@v6.5.0` release reference with dependency caching disabled. The repository does not impose a global GitHub Action SHA policy. V1 supports npm only. Yarn, pnpm, Bun, alternate Corepack selection, `npm install`, mutable lock generation, and caller-selected package-manager commands are rejected. `npm-ci` requires a committed `package-lock.json` with lockfile version 3 and runs only `npm ci --no-audit --no-fund`.

Npm home, cache, config, and temporary state live beneath registered workflow state. Package-manager cache transport remains disabled. The source manifest and lockfile are hashed before restore and reverified after every stage.

## Public browser environment

A selected compatibility fixture may allow a subset of:

- `NEXT_PUBLIC_API_URL`;
- `NEXT_PUBLIC_API_BASE_URL`;
- `NEXT_PUBLIC_PROJECT`.

Unknown keys, controls, multiline values, token-like content, GitHub/secret expressions, broad inherited environments, and values on profiles without an allowlist fail closed. Public values are process-local, never written to `.env*`, never printed, never retained in evidence, and removed with workflow state. `NEXT_TELEMETRY_DISABLED=1` is central internal state, not a public input.

## Static output

Build profiles require one normalized directory below the contracted working directory. Validation rejects missing, empty, escaped, symlinked, oversized, malformed, server, Pages Functions, Worker, OpenNext, Wrangler, or deployment output. A selected checked-in verifier is a regular tracked non-symlink file and executes only with fixed contract-owned arguments. The workflow may emit a deterministic digest but never uploads or deploys output.

StreamScapeWeb remains a browser-only static export whose Cloudflare Pages Git deployment stays outside GitHub Actions. Agent State frontend validation remains separate from backend, lifecycle, release, Flux, and Supabase behavior. Finance domain checks and manual Cypress/live-server proof remain Finance-owned.

## Cleanup and confidentiality

Node execution occurs in copied registered state. Terminal cleanup removes `node_modules`, `.next`, `out`, declared output, npm/cache/config/temp state, coverage, reports, generated inventories, Cypress screenshots/videos/cache, logs, bytecode, and every registered path without following symlinks. The workflow then rechecks exact source equality, manifest/lock integrity, and complete tracked/untracked cleanliness. Cleanup runs under `if: always()` and residue fails the workflow.

Private source, credentials, detailed command output, logs, and generated private-source material must not be exposed through public logs or public Actions artifacts. This confidentiality boundary is feature-specific; it is not a repository-wide rule that every public or non-private workflow must have zero artifacts.
