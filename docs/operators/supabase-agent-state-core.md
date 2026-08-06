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

Normal discovery must not download or compile PostgreSQL. The database reconstruction test is present in the discovery namespace but visibly skips before creating a runtime unless the explicit reconstruction opt-in is set.

## Explicit PostgreSQL 17 reconstruction

Canonical issue #3 must separately run the full fail-closed disposable reconstruction suite with an explicitly approved PostgreSQL 17 environment:

```bash
AGENT_STATE_RUN_POSTGRES_RECONSTRUCTION=1 \
AGENT_STATE_POSTGRES_BIN=/absolute/path/to/postgresql-17/bin \
python3 -m unittest -v tests.test_supabase_agent_state_core_database
```

`AGENT_STATE_POSTGRES_BIN` should point to a complete vetted PostgreSQL 17 binary directory. With the explicit opt-in set, the runtime may otherwise use a complete PostgreSQL 17 installation from `PATH` or obtain the pinned PostgreSQL 17.6 source archive and verify SHA-256 `2910b85283674da2dae6ac13fe5ebbaaf3c482446396cba32e6728d3cc736d86`.

When the explicit opt-in is present, toolchain or source-download failure is a reconstruction failure. It must not be silently skipped or represented as passed. The suite creates only local disposable PostgreSQL state and must not contact the production Supabase project.

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

Therefore the clean three-file history **must not be connected to the existing project and applied as three new production migrations**. Canonical issue #3 must first choose and review an explicit live-ledger reconciliation strategy.

## Permitted reconciliation classes

This transition package does not select a strategy. Canonical issue #3 may review one of these classes:

1. **Reconstruct the observed live baseline.** Recreate the exact six already-applied versions and statements as the canonical baseline, then add reviewed forward migrations.
2. **Owner-authorized reset/recreation.** Explicitly reset or recreate the provisional schema and migration ledger under owner authorization, then deploy the clean history.
3. **Reviewed baseline or repair procedure.** Use another reviewed procedure that proves one authoritative migration history and exact source/live parity.

Direct ad-hoc repair through `execute_sql`, `apply_migration`, SQL editor, table editor, manual ledger edits, or similar untracked production changes is forbidden.

Machine-readable details and gates are in `contracts/supabase-agent-state-core-rehome.json`:

- `live_reconciliation_required=true`;
- `production_deployment_allowed=false`;
- `ordinary_rpc_use_allowed=false`;
- `strategy_selected_by_this_package=null`.

## Re-homing sequence

1. After issue #60 merges, reconcile draft PR #57 normally with the exact current `main` without rewriting history.
2. Obtain a fresh exact-head central self-check and verify the corrected package.
3. Independently review the exact PR #57 head and `contracts/supabase-agent-state-core-files.json`.
4. Copy non-migration implementation, contracts, fixtures, tests, and documentation exactly as canonical review inputs.
5. Treat the three clean migration files as candidate target/source inputs; do not assume they are the production ledger.
6. Run normal repository discovery and the explicit PostgreSQL 17 reconstruction command in the canonical repository.
7. Inspect the reported six-entry live ledger and select a permitted reconciliation strategy through canonical issue #3 review.
8. Only after that strategy is accepted and proves one authoritative history may the canonical repository be connected for production deployment.
9. After canonical deployment, verify migration history, object/function hashes, grants, project mappings, bounded RPC behavior, security/performance advisors, and smoke-data cleanup.
10. Reconcile transitional Supabase files in `ci-workflows` through a separate reviewed decision so two canonical histories do not remain.

## Ordinary RPC boundary

The source package documents the intended future `agent_api.*` surface, but ordinary Agent State use is currently forbidden. Do not use the provisional RPCs for normal coordination until canonical live proof and final cutover explicitly enable them.

After that future cutover, bounded calls may use `agent_api.command`, `agent_api.resume`, `agent_api.context`, and `agent_api.ownership_check`; ordinary operation must still never directly mutate private tables or migration state.

## Remaining production evidence

Disposable results can prove clean source reconstruction and database behavior only. Canonical completion still requires an accepted live-ledger reconciliation strategy, GitHub-managed production deployment, migration-ledger verification, live object/function/grant parity, bounded smoke RPCs, security and performance advisors, cleanup, and exact-head deployment checks in `agent-state-supabase`.
