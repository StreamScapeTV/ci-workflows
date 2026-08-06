# Supabase Agent State transactional core

Issue `StreamScapeTV/ci-workflows#52` is a transition source package for canonical issue `StreamScapeTV/agent-state-supabase#3`. It implements only the current single-work transactional core. The canonical migration and deployment history will live in `agent-state-supabase` after independent review and re-homing.

## Authority and schemas

The package creates two schemas:

- `agent_private` contains normalized authoritative tables keyed by project.
- `agent_api` contains the only approved runtime functions.

The private schema models projects, repository and integration-branch mappings, Agent/Codex profiles and slots, work sessions, issue/branch/PR bindings, exact base/head/merge evidence, claims, immutable requests, receipts, and append-only events. It does not create per-project table families.

`PUBLIC`, `anon`, `authenticated`, and `service_role` receive no private-schema, table, sequence, or helper-function privileges. Row-level security is enabled and forced without direct-access policies as defense in depth. `service_role` receives execute permission only on:

- `agent_api.command(jsonb)`
- `agent_api.resume(text, text, text)`
- `agent_api.context(text, text, text)`
- `agent_api.ownership_check(text, text, bigint, text, bigint, text)`

## Transactions and locks

Each command first locks its immutable request ID. Exact replay of the same canonical JSON returns the stored response and receipt. Changed JSON under the same request ID fails before work-state mutation.

Mutation commands then lock the project and relevant project/profile/slot/session rows. This produces deterministic project-scoped ordering:

- `start` inserts its request, session, binding, evidence, initial claims, receipt, and event in one transaction.
- `done` and `cancel` release all active claims and terminalize the work in one transaction.
- injected statement failure rolls back the request ledger and every earlier mutation in that command.
- concurrent overlapping claims converge to one accepted owner and one immutable rejected receipt.
- concurrent disjoint claims both succeed, although the project lock serializes their critical sections.

The issue intentionally permits only one unfinished work session per `(project, profile, slot)`. Parking, multiple same-slot work, and activation belong to the later structured-work issue.

## Validation and redaction

The dispatcher rejects unknown fields, malformed request shapes, mismatched project/repository identity, invalid Agent/Codex identity, unsafe or ambiguous paths, duplicate claims, invalid prefix use, stale base/PR/head assertions, invalid transitions, overlong text, and sensitive credential-like text.

Public responses contain stable project, action, request, receipt, bounded session, claim, and collision data. They do not contain private UUIDs, request hashes, connection strings, authorization values, SQL, event payloads, or connector internals. Context returns at most 50 event metadata records and omits event payloads.

## Reconstructibility

The ordered migration history is:

1. `20260806172100_agent_state_core_schema.sql`
2. `20260806172200_agent_state_core_rpc.sql`
3. `20260806172300_agent_state_core_indexes.sql`

The disposable-database test reconstructs two empty PostgreSQL 17 databases solely from these files, compares normalized schema dumps, exercises the full RPC and failure matrix, and drops both databases. It uses an installed PostgreSQL 17 toolchain when available or downloads and builds the pinned PostgreSQL 17.6 source archive after SHA-256 verification.

## Exclusions

This package does not implement parking, priorities, multiple same-slot work, review assignment, takeover, trusted bots, migration of old metadata, organization-rules cutover, or retirement of the old API/workflow/client paths. Those remain in the canonical program issues corresponding to former #53, #54, and #55.
