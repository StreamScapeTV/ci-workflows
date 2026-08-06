# Supabase Agent State core source-package validation

Status: **pre-deployment transition package**

Canonical destination: `StreamScapeTV/agent-state-supabase#3`

This document records only disposable-database and repository evidence. It does not claim that these migrations have been deployed to the live Supabase project. Under the revised owner architecture, production deployment must occur from the canonical repository through the reviewed Supabase GitHub integration.

## Source migration order

1. `supabase/migrations/20260806172100_agent_state_core_schema.sql`
2. `supabase/migrations/20260806172200_agent_state_core_rpc.sql`
3. `supabase/migrations/20260806172300_agent_state_core_indexes.sql`

## Disposable test scope

The isolated suite reconstructs two empty PostgreSQL 17 databases from the same ordered migrations and compares their normalized schema-only dumps. It then proves:

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
- zero active sessions and claims followed by deletion of both disposable databases and the temporary PostgreSQL runtime.

The exact commands and results are recorded in the PR description and final handoff after the exact head passes the repository self-check.

## Not yet performed

The following remain deliberately outstanding:

- re-homing into the canonical repository;
- Supabase GitHub integration connection;
- production migration application;
- production migration-ledger and schema/function hash parity;
- live direct-RPC smoke validation;
- live grants/RLS verification;
- live security and performance advisors;
- live smoke-data cleanup;
- canonical exact-head deployment checks.
