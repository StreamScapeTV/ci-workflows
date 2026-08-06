# Supabase Agent State core source-package validation

Status: **pre-deployment transition package with unresolved live-ledger reconciliation**

Canonical destination: `StreamScapeTV/agent-state-supabase#3`

This document records source-package and disposable-database requirements. It does not claim that PR #57's clean migration history is the live production migration history, and it does not authorize production deployment.

## Clean source-package migration order

1. `supabase/migrations/20260806172100_agent_state_core_schema.sql`
2. `supabase/migrations/20260806172200_agent_state_core_rpc.sql`
3. `supabase/migrations/20260806172300_agent_state_core_indexes.sql`

These are reviewed target/source migrations for clean reconstruction. They are not directly deployable as three new migrations to the currently connected project.

## Observed provisional live ledger

The connected project is reported to already contain the provisional Agent State schema and these six migration-ledger entries:

| Version | Name |
|---|---|
| `20260806174835` | `agent_state_core_schema` |
| `20260806175059` | `agent_state_core_rpc` |
| `20260806175243` | `agent_state_core_rpc_hardening` |
| `20260806175336` | `agent_state_claim_regex_fix` |
| `20260806175433` | `agent_state_error_projection_fix` |
| `20260806175808` | `agent_state_foreign_key_indexes` |

This live state is provisional and unaccepted as canonical deployment evidence. It is not equivalent to the clean three-file source history.

Machine-readable policy is in `contracts/supabase-agent-state-core-rehome.json` and requires:

- `live_reconciliation_required=true`;
- `production_deployment_allowed=false` until canonical issue #3 accepts a reviewed reconciliation strategy;
- `ordinary_rpc_use_allowed=false` until final canonical live proof and cutover.

No reconciliation strategy is selected by this package.

## Permitted canonical reconciliation classes

Canonical issue #3 may independently review and select one of these classes:

1. reconstruct the exact six already-applied migration versions and statements as the canonical baseline, followed by reviewed forward migrations;
2. perform an explicit owner-authorized reset or recreation of the provisional schema and ledger, followed by the clean history;
3. use another explicitly reviewed baseline or repair procedure that proves one authoritative migration history.

Direct ad-hoc repair or untracked ledger manipulation is forbidden.

## Disposable test scope

The explicit isolated suite reconstructs two empty PostgreSQL 17 databases from the same clean ordered migrations and compares normalized schema-only dumps. It is designed to prove:

- positive and negative coverage for every current action;
- atomic start with initial claims;
- atomic done/cancel with complete claim release;
- rollback of start and terminal operations under injected database failures;
- exact replay and conflicting request-ID reuse;
- concurrent overlapping and disjoint claims;
- file exact/prefix and package/resource/manifest/device collisions;
- malformed shape, unknown fields, overlong input, invalid identities, project mismatch, unsafe paths, duplicate claims, stale base/PR/head, wrong issue/branch, invalid transitions, and ambiguous release rejection;
- direct-access denial for ordinary roles;
- immutable requests/receipts and append-only events;
- bounded, redacted receipts, context, ownership, and event metadata;
- zero active sessions and claims followed by deletion of both disposable databases and temporary PostgreSQL state.

Disposable reconstruction does not prove or repair production ledger parity.

## Test execution model

Normal central discovery is network-free with respect to this package:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The database reconstruction class is explicitly skipped unless `AGENT_STATE_RUN_POSTGRES_RECONSTRUCTION=1` is set, so ordinary discovery must not download or compile PostgreSQL.

Canonical issue #3 must separately run:

```bash
AGENT_STATE_RUN_POSTGRES_RECONSTRUCTION=1 \
AGENT_STATE_POSTGRES_BIN=/absolute/path/to/postgresql-17/bin \
python3 -m unittest -v tests.test_supabase_agent_state_core_database
```

That command is fail-closed. When opt-in is set, a missing or invalid PostgreSQL 17 toolchain is not a pass and is not silently skipped.

## Current source execution evidence

Previous source-only checks on the package passed, but any final acceptance must be tied to the corrected exact head after issue #60 is merged and this branch is normally reconciled with current `main`.

The explicit PostgreSQL reconstruction requirement remains outstanding and must not be represented as passed until it actually completes in a vetted isolated environment.

## Not yet allowed or performed

The following remain deliberately outstanding:

- canonical issue #3 selection and review of a live-ledger reconciliation strategy;
- re-homing/reconciliation into the canonical repository;
- Supabase GitHub integration connection;
- production deployment;
- production migration-ledger and object/function hash parity;
- live bounded RPC smoke validation;
- live grants/RLS verification;
- live security and performance advisors;
- live smoke-data cleanup;
- ordinary RPC cutover;
- fresh exact-head central self-check after issue #60 merges and PR #57 is reconciled to exact current `main`.

No production DDL, ledger repair, schema reset, or provisional RPC use is authorized by this evidence.
