# Supabase Agent State core operator guide

This guide describes source validation and later re-homing. It is not production-deployment evidence.

## Validate the source package

Run the fast repository and manifest checks:

```bash
python3 -m unittest -v tests.test_supabase_agent_state_core
```

Run the isolated PostgreSQL reconstruction and RPC suite:

```bash
python3 -m unittest -v tests.test_supabase_agent_state_core_database
```

Run the repository's normal discovered suite:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The database suite uses PostgreSQL 17. Set `AGENT_STATE_POSTGRES_BIN` to a vetted complete PostgreSQL 17 binary directory, or let the suite search `PATH`. Otherwise it downloads PostgreSQL 17.6 from the official PostgreSQL source server, verifies SHA-256 `2910b85283674da2dae6ac13fe5ebbaaf3c482446396cba32e6728d3cc736d86`, builds a private test-only installation, creates two empty clusters/databases, applies the committed migration order, and deletes all temporary state.

No production Supabase project is contacted by these commands.

## Direct RPC form after canonical deployment

After the package is accepted, re-homed to `StreamScapeTV/agent-state-supabase#3`, merged there, and deployed by the reviewed Supabase GitHub integration, ordinary operations use only bounded calls such as:

```sql
select agent_api.command(
  '{
    "contract_version": 1,
    "request_id": "example-start-0001",
    "action": "start",
    "project": "ci-workflows",
    "repository": "StreamScapeTV/ci-workflows",
    "session_name": "Agent 2",
    "task": "One bounded issue",
    "issue_number": 52,
    "branch": "issue/52-supabase-core-a1b2",
    "branch_nonce": "a1b2",
    "base_sha": "0123456789abcdef0123456789abcdef01234567",
    "claims": [
      {"kind": "file", "mode": "prefix", "value": "supabase/migrations"}
    ]
  }'::jsonb
);
```

Bounded reads are:

```sql
select agent_api.resume('ci-workflows', 'StreamScapeTV/ci-workflows', 'Agent 2');
select agent_api.context('ci-workflows', 'StreamScapeTV/ci-workflows', '<agent-id>');
select agent_api.ownership_check(
  'ci-workflows',
  'StreamScapeTV/ci-workflows',
  52,
  'issue/52-supabase-core-a1b2',
  57,
  '<exact-head-sha>'
);
```

Ordinary operation must not perform table-level insert, update, delete, truncate, drop, grant, policy, migration, or `agent_private` helper calls.

## Canonical migration and deployment sequence

1. Independently review the exact PR #57 head and its file manifest.
2. Copy the accepted files exactly according to `contracts/supabase-agent-state-core-rehome.json` onto one branch for `agent-state-supabase#3`.
3. Verify every copied file SHA-256 against `contracts/supabase-agent-state-core-files.json`.
4. Run both isolated test commands in the canonical repository.
5. Review the exact migration order; do not edit an accepted/deployed migration in place.
6. Connect the canonical repository to the intended Supabase project through Supabase's GitHub integration.
7. Merge only the unchanged validated canonical head so the integration applies the reviewed history.
8. Verify migration history, object definitions and hashes, grants, project mappings, RPC behavior, advisors, and smoke-data cleanup.
9. Reconcile the transition files in `ci-workflows` only through a separate reviewed decision; do not maintain two canonical histories.

## Remaining production evidence

Local/disposable results prove source reconstruction and database behavior only. Canonical completion still requires GitHub-integration deployment, production migration-ledger verification, live object/function/grant parity, bounded smoke RPCs, security and performance advisors, cleanup, and exact-head deployment checks in `agent-state-supabase`.
