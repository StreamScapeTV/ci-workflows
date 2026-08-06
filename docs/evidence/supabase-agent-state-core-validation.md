# Supabase Agent State core source-package validation

Status: **pre-deployment transition package with unresolved live-ledger reconciliation**

Canonical destination: `StreamScapeTV/agent-state-supabase#3`

This document records source-package and disposable-database requirements. It does not claim that PR #57's clean migration history is the live production migration history, and it does not authorize production deployment or ordinary RPC use.

## Clean source-package migration order

1. `supabase/migrations/20260806172100_agent_state_core_schema.sql`
2. `supabase/migrations/20260806172200_agent_state_core_rpc.sql`
3. `supabase/migrations/20260806172300_agent_state_core_indexes.sql`

These are reviewed target/source migrations for clean reconstruction. They cannot be applied as three new migrations to the currently connected project because that project already contains the provisional schema under a different ledger.

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
- `production_deployment_allowed=false` until canonical issue #3 accepts a reviewed reconciliation strategy proving one authoritative history;
- `ordinary_rpc_use_allowed=false` until final canonical live proof and cutover;
- `direct_ad_hoc_repair_forbidden=true`;
- `silent_migration_filename_rewriting_forbidden=true`;
- `manual_ledger_mutation_forbidden=true`.

No reconciliation strategy is selected by this package.

## Permitted canonical reconciliation classes

Canonical issue #3 may independently review and select one of these classes:

1. reconstruct the exact source corresponding to the six already-applied migration versions and statements as the canonical baseline, followed by reviewed forward migrations;
2. perform an explicit owner-authorized reset or recreation of the provisional schema and ledger, followed by the clean history;
3. use another explicitly reviewed baseline or repair procedure that proves exactly one authoritative migration history and exact source/live parity.

Direct ad-hoc repair, production ledger mutation, direct production DDL, and silent migration version/name/filename rewriting are forbidden.

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

Normal repository-wide discovery is network-free with respect to this package:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The database reconstruction class produces a visible unittest skip before temporary runtime creation unless `AGENT_STATE_RUN_POSTGRES_RECONSTRUCTION=1` is set. That discovery skip is expected orchestration behavior only and is not reconstruction success.

Canonical issue #3 must separately run the fail-closed reconstruction requirement with a vetted PostgreSQL 17 runtime:

```bash
AGENT_STATE_RUN_POSTGRES_RECONSTRUCTION=1 \
AGENT_STATE_POSTGRES_BIN=/absolute/path/to/postgresql-17/bin \
python3 -m unittest -v tests.test_supabase_agent_state_core_database
```

When opt-in is set, a missing or invalid vetted PostgreSQL 17 runtime fails clearly. It is not silently skipped.

An authorized source build is a separate explicit path, not an automatic fallback:

```bash
AGENT_STATE_RUN_POSTGRES_RECONSTRUCTION=1 \
AGENT_STATE_ALLOW_POSTGRES_SOURCE_DOWNLOAD=1 \
python3 -m unittest -v tests.test_supabase_agent_state_core_database
```

That path verifies the pinned PostgreSQL 17.6 source SHA-256 `2910b85283674da2dae6ac13fe5ebbaaf3c482446396cba32e6728d3cc736d86`. Download or checksum failure fails reconstruction.

## Current source execution evidence

Only commands that actually complete on the corrected exact head may be reported as passing. The explicit PostgreSQL reconstruction requirement remains **unexecuted** unless it successfully reaches reconstruction, behavior assertions, and cleanup in a vetted isolated environment.

Normal-discovery visibility of the reconstruction skip must never be converted into a reconstruction pass claim.

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
- successful explicit PostgreSQL 17 reconstruction on the final accepted source;
- any future exact-head central self-check required after the runner-contract dependency is resolved.

No production DDL, migration-ledger repair, schema reset, migration rename, provisional RPC coordination, or other live mutation is authorized or performed by this evidence.
