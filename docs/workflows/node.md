# Node validation workflow

Public API: `validation.node` `1.0.0`  
Workflow: `.github/workflows/reusable-node.yml`  
Stable check: `CI / Node validation`

The reusable workflow validates one exact admitted source SHA through checked-in Node command profiles. It is read-only, npm-only, cache-disabled by default, artifact-free, and product-neutral.

## Profiles

| Profile | Restore | Commands | Output |
|---|---|---|---|
| `locked-node` | exact `npm ci --no-audit --no-fund` | reviewed quality, type, and test stages | none |
| `next-static-export` | exact npm lockfile v3 | reviewed quality, test, build, and checked-in verifier | browser-only static output |
| `frontend-contract-static` | exact npm lockfile v3 | reviewed contract tests and static build | bounded generic static output |
| `node-source-audit` | none | one checked-in audit hook with fixed arguments | none |

All profiles use semantic `portable`. Callers cannot supply concrete labels, hosts, engines, caches, registries, package-manager commands, matrices, or infrastructure identities.

## Public inputs

- `admitted_sha`: exact lowercase source SHA already admitted by `source.resolve`.
- `validation_profile`: one of the four reviewed profiles.
- exactly one of `version_file` or exact `node_version`, as permitted by the selected fixture.
- `working_directory`, `install_profile`, `command_profile`, `script_path`, `static_output_directory`, and `output_verifier_path`: values that must exactly match checked-in compatibility data.
- `public_environment`: canonical JSON containing only reviewed browser-public keys allowed by the selected fixture.
- `artifact_exception_id`: reserved central input; v1 requires it to be empty and retains zero routine artifacts.

The API never accepts arbitrary command text, shell, arguments, callbacks, modules, functions, environment-file paths, refs, runners, engines, registries, secrets, database URLs, Cloudflare deployment, Workers, Wrangler, signing, devices, Flux targets, Kubernetes targets, publication, or artifact uploads.

## Public outputs

- `result`;
- exact `node_version` and `npm_version`;
- `install_result`, `test_summary`, and `build_result`;
- `output_verified` and optional deterministic `output_digest`;
- `clean_tree`, `cleanup_result`, and `artifact_exception_used`;
- deterministic bounded `evidence_id`.

No command output, public browser value, host path, package cache, source content, deployment value, token, or secret is exposed.

## Immutable private helper reuse

Private consumers do not need a second token or permission to clone `StreamScapeTV/ci-workflows`. `validation.node` follows the already-proven private-action sharing model used by `source.resolve`: central composite actions are invoked directly as immutable private action references rather than checking the central repository out into the caller workspace.

The Node workflow pins these central helpers to the reviewed commit `70e08d4ddf8930046632a7135950e924b82e22bf` and records the same identities in `contracts/action-tool-lock.json`:

- `actions/validate-node`;
- `actions/exact-checkout`;
- `actions/prepare-workspace`;
- `actions/render-evidence`; and
- `actions/cleanup-workspace`.

The composites resolve their central scripts and libraries relative to `GITHUB_ACTION_PATH`, so the private central implementation is supplied by the immutable action checkout itself. The public workflow has no `actions/checkout` step for the central repository, no `.ciw` clone, no caller-visible central source selector, and no new workflow secret.

`exact-checkout` still checks out only the admitted caller repository/source SHA. Its optional token defaults to the caller-scoped `github.token` and is never persisted. Central helper access therefore remains separate from caller-source checkout and from product dependency credentials.

## Runtime and restore

The planner resolves exactly one canonical `MAJOR.MINOR.PATCH` from an exact `.nvmrc`, exact `.node-version`, or a reviewed exact API value. Ranges, aliases, prefixes, comments, multiple values, malformed files, and contradictory sources fail closed. `package.json` Node/npm engine bounds must accept the resolved runtime.

The workflow uses official `actions/setup-node` pinned to a full commit SHA and records its human release in the central action lock. V1 supports npm only. Yarn, pnpm, Bun, alternate Corepack selection, `npm install`, mutable lock generation, and caller-selected package-manager commands are rejected. `npm-ci` requires a committed `package-lock.json` with lockfile version 3 and runs only `npm ci --no-audit --no-fund`.

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

## Cleanup

Node execution occurs in copied registered state. Terminal cleanup removes `node_modules`, `.next`, `out`, declared output, npm/cache/config/temp state, coverage, reports, generated inventories, Cypress screenshots/videos/cache, logs, bytecode, and every registered path without following symlinks. The workflow then rechecks exact source equality, manifest/lock integrity, complete tracked/untracked cleanliness, and zero Actions artifacts. Cleanup runs under `if: always()` and residue fails the workflow.
