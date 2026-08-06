# Supabase Agent State transactional core

Issue `StreamScapeTV/ci-workflows#52` is a transition source package for canonical issue `StreamScapeTV/agent-state-supabase#3`. It implements only the current single-work transactional core. The canonical repository must independently review both this source package and the already-existing provisional live migration ledger before choosing a production history.

## Authority and schemas

The source package defines two schemas:

- `agent_private` contains normalized authoritative tables keyed by project.
- `agent_api` contains the only candidate runtime functions.

The private schema models projects, repository and integration-branch mappings, Agent/Codex profiles and slots, work sessions, issue/branch/PR bindings, exact base/head/merge evidence, claims, immutable requests, receipts, and append-only events. It does not create per-project table families.

`PUBLIC`, `anon`, `authenticated`, and `service_role` receive no private-schema, table, sequence, or helper-function privileges. Row-level security is enabled and forced without direct-access policies as defense in depth. The source contract grants `service_role` execute permission only on:

- `agent_api.command(jsonb)`
- `agent_api.resume(text, text, text)`
- `agent_api.context(text, text, text)`
- `agent_api.ownership_check(text, text, bigint, text, bigint, text)`

These functions are **not approved for ordinary coordination yet**. Ordinary RPC use remains disabled until the canonical program completes live proof and final cutover.

## Transactions and locks

Each command first locks its immutable request ID. Exact replay of the same canonical JSON returns the stored response and receipt. Changed JSON under the same request ID fails before work-state mutation.

Mutation commands then lock the project and relevant project/profile/slot/session rows:

- `start` inserts its request, session, binding, evidence, initial claims, receipt, and event in one transaction.
- `done` and `cancel` release all active claims and terminalize the work in one transaction.
- injected statement failure rolls back the request ledger and every earlier mutation in that command.
- concurrent overlapping claims converge to one accepted owner and one immutable rejected receipt.
- concurrent disjoint claims both succeed, although the project lock serializes their critical sections.

The issue intentionally permits only one unfinished work session per `(project, profile, slot)`. Parking, multiple same-slot work, activation, separate review-request ownership, takeover, trusted bots, and cutover remain outside this package.

## Validation and redaction

The dispatcher rejects unknown fields, malformed request shapes, mismatched project/repository identity, invalid Agent/Codex identity, unsafe or ambiguous paths, duplicate claims, invalid prefix use, stale base/PR/head assertions, invalid transitions, overlong text, and sensitive credential-like text.

Public responses contain stable project, action, request, receipt, bounded session, claim, and collision data. They do not contain private UUIDs, request hashes, connection strings, authorization values, SQL, event payloads, or connector internals. Context returns at most 50 event metadata records and omits event payloads.

## Clean target/source migration history

The reviewed target/source migration order in PR #57 is:

1. `20260806172100_agent_state_core_schema.sql`
2. `20260806172200_agent_state_core_rpc.sql`
3. `20260806172300_agent_state_core_indexes.sql`

These three files are a clean reconstruction target for an empty disposable PostgreSQL 17 database. They are **not directly deployable as new migrations to the current connected project**, because that project already contains the provisional schema and a different six-entry migration ledger.

## Observed provisional live ledger

The connected project is reported to contain these provisional, unaccepted entries:

1. `20260806174835 agent_state_core_schema`
2. `20260806175059 agent_state_core_rpc`
3. `20260806175243 agent_state_core_rpc_hardening`
4. `20260806175336 agent_state_claim_regex_fix`
5. `20260806175433 agent_state_error_projection_fix`
6. `20260806175808 agent_state_foreign_key_indexes`

The schema already exists. Therefore canonical issue #3 must choose and independently review a live-ledger reconciliation strategy **before any production deployment is allowed**. This source package does not choose or execute a strategy.

Permitted strategy classes are limited to:

1. reconstruct the exact six already-applied versions and statements as the canonical baseline, followed by reviewed forward migrations;
2. an explicit owner-authorized reset or recreation of the provisional schema and ledger, followed by the reviewed clean history;
3. another explicitly reviewed baseline or repair procedure that proves one authoritative migration history and exact source/live parity.

Direct ad-hoc repair through SQL editor, connector DDL, table editor, or untracked migration-ledger mutation is forbidden.

## Disposable reconstruction model

Normal repository discovery:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

must not download or compile PostgreSQL. The database test visibly skips its reconstruction class unless the explicit reconstruction opt-in is set.

Canonical issue #3 must separately run the fail-closed reconstruction command in a vetted isolated PostgreSQL 17 environment:

```bash
AGENT_STATE_RUN_POSTGRES_RECONSTRUCTION=1 \
AGENT_STATE_POSTGRES_BIN=/absolute/path/to/postgresql-17/bin \
python3 -m unittest -v tests.test_supabase_agent_state_core_database
```

When explicitly enabled, the suite must run; lack of a valid PostgreSQL 17 toolchain or inability to obtain the pinned source is a failure, not a passing or silently skipped reconstruction result. Disposable reconstruction proves the clean target/source history only; it does not reconcile the existing production migration ledger.

## Exclusions

This package does not implement parking, priorities, multiple same-slot work, separate review requests, reviewer assignment, takeover, trusted bots, migration/cutover policy, or retirement of old paths. Those remain in later canonical program issues.
