# Supabase Agent State core operator guide

This guide describes source validation and canonical reconciliation planning. It is not production-deployment evidence.

## Normal repository validation

Run the fast source/contract/manifest checks directly:

```bash
python3 -m unittest -v tests.test_supabase_agent_state_core
```

Run the repository's normal discovered suite:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Normal discovery must not download or compile PostgreSQL. The database reconstruction test remains visible in discovery but exits with an explicit unittest skip **before temporary runtime creation** unless the separate reconstruction opt-in is set. That discovery skip is only a signal that the heavyweight integration proof is a separately required command; it is never reconstruction success.

## Explicit PostgreSQL 17 reconstruction

Canonical issue #3 must separately run the full fail-closed disposable reconstruction suite with an explicitly approved PostgreSQL 17 environment:

```bash
AGENT_STATE_RUN_POSTGRES_RECONSTRUCTION=1 \
AGENT_STATE_POSTGRES_BIN=/absolute/path/to/postgresql-17/bin \
python3 -m unittest -v tests.test_supabase_agent_state_core_database
```

`AGENT_STATE_POSTGRES_BIN` must point to a complete vetted PostgreSQL 17 binary directory. If that directory is missing or invalid, the explicit reconstruction fails clearly. A complete PostgreSQL 17 installation already on `PATH` may also satisfy the runtime requirement.

A network source download is **not** implicitly authorized by reconstruction. If canonical issue #3 separately authorizes building the pinned PostgreSQL 17.6 source, use:

```bash
AGENT_STATE_RUN_POSTGRES_RECONSTRUCTION=1 \
AGENT_STATE_ALLOW_POSTGRES_SOURCE_DOWNLOAD=1 \
python3 -m unittest -v tests.test_supabase_agent_state_core_database
```

The download path accepts only the pinned PostgreSQL 17.6 archive and verifies SHA-256 `2910b85283674da2dae6ac13fe5ebbaaf3c482446396cba32e6728d3cc736d86` before extraction or build. Download, checksum, build, startup, migration, assertion, or cleanup failure fails the reconstruction command. It must not be silently skipped or represented as passed.

The suite creates only disposable local PostgreSQL state. It drops both temporary databases and removes the temporary runtime tree. It must never contact the connected production Supabase project.

## Source history versus current live ledger

PR #57 contains this clean target/source migration order:

1. `20260806172100_agent_state_core_schema.sql`
2. `20260806172200_agent_state_core_rpc.sql`
3. `20260806172300_agent_state_core_indexes.sql`

The current connected project already has the provisional schema and these six reported ledger entries:

1. `20260806174835 agent_state_core_schema`
2. `20260806175059 agent_state_core_rpc`
3. `20260806175243 agent_state_core_rpc_hardening`
4. `20260806175336 agent_state_claim_regex_fix`
5. `20260806175433 agent_state_error_projection_fix`
6. `20260806175808 agent_state_foreign_key_indexes`

The three clean files are a reviewed **target/source package**, not three migrations that may be appended to the existing project. The live schema already exists under a different provisional ledger. Canonical `agent-state-supabase#3` must therefore select and independently review one migration-ledger reconciliation strategy before production deployment.

## Permitted reconciliation classes

This transition package does not select or execute a strategy. Canonical issue #3 may review one of these classes:

1. **Reconstruct the observed live baseline.** Reconstruct the exact source corresponding to the six already-applied versions and statements as the canonical baseline, then continue with reviewed forward migrations.
2. **Owner-authorized reset/recreation.** Explicitly reset or recreate the provisional schema and migration ledger under owner authorization, then deploy the clean history.
3. **Reviewed baseline or repair procedure.** Use another reviewed baseline or repair procedure that proves exactly one authoritative migration history and exact source/live parity.

Direct ad-hoc repair is forbidden. Do not use `execute_sql`, `apply_migration`, SQL editor, table editor, connector DDL, manual migration-ledger mutation, or silent migration version/name/filename rewriting to force the histories to appear aligned.

Machine-readable details and gates are in `contracts/supabase-agent-state-core-rehome.json`:

- `live_reconciliation_required=true`;
- `production_deployment_allowed=false`;
- `ordinary_rpc_use_allowed=false`;
- `strategy_selected_by_this_package=null`;
- `direct_ad_hoc_repair_forbidden=true`;
- `silent_migration_filename_rewriting_forbidden=true`.

## Re-homing sequence

1. Independently review the exact PR #57 head and `contracts/supabase-agent-state-core-files.json`.
2. Copy non-migration implementation, contracts, fixtures, tests, and documentation exactly as canonical review inputs.
3. Treat the three clean migration files as candidate target/source inputs; do not assume or manufacture their place in the production ledger.
4. Run normal repository discovery in the canonical repository; this must have no PostgreSQL download/build side effect.
5. Run the separate explicit PostgreSQL 17 reconstruction command successfully in a vetted isolated environment.
6. Inspect the reported six-entry live ledger and select one permitted reconciliation strategy through canonical issue #3 review.
7. Only after that strategy is accepted and proves exactly one authoritative history may production deployment be enabled.
8. After canonical deployment, verify migration history, object/function hashes, grants, project mappings, bounded RPC behavior, security/performance advisors, and smoke-data cleanup.
9. Reconcile transitional Supabase files in `ci-workflows` through a separate reviewed decision so two canonical histories do not remain.

## Ordinary RPC boundary

The source package documents the intended future `agent_api.*` surface, but ordinary Agent State use is currently forbidden. Do not use the provisional RPCs for coordination until canonical live proof and final cutover explicitly enable them.

After that future cutover, bounded calls may use `agent_api.command`, `agent_api.resume`, `agent_api.context`, and `agent_api.ownership_check`; ordinary operation must still never directly mutate private tables or migration state.

## Remaining production evidence

Disposable results can prove clean source reconstruction and database behavior only. Canonical completion still requires an accepted live-ledger reconciliation strategy, GitHub-managed production deployment, migration-ledger verification, live object/function/grant parity, bounded smoke RPCs, security and performance advisors, cleanup, and exact-head deployment checks in `agent-state-supabase`.
