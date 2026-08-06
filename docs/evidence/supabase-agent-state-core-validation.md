# Supabase Agent State core validation

Validation date: 2026-08-06

Connected project: `Agent State` (`eu-west-1`, PostgreSQL 17.6)

## Applied migration chain

| Live version | Migration |
|---|---|
| `20260806174835` | `agent_state_core_schema` |
| `20260806175059` | `agent_state_core_rpc` |
| `20260806175243` | `agent_state_core_rpc_hardening` |
| `20260806175336` | `agent_state_claim_regex_fix` |
| `20260806175433` | `agent_state_error_projection_fix` |
| `20260806175808` | `agent_state_foreign_key_indexes` |

Each live DDL change is represented by the same ordered migration under `supabase/migrations/`.

## Live direct-RPC proof

Isolated records under issues `900001`–`900003` proved:

- bounded no-work read and command `resume`;
- atomic `start` with exact/prefix file and package/resource/manifest/device claims;
- exact replay returning the original receipt;
- conflicting request-ID reuse returning `agent_state:request_id_reuse_conflict` without mutation;
- `claim`, selective `release`, `reconcile_base`, `block`, first `review`, `done`, and `cancel`;
- bounded `context` and exact `ownership_check` reads;
- prefix/exact collision returning one immutable `accepted=false` receipt;
- `done` and `cancel` releasing every remaining claim transactionally.

Project-scoped transaction advisory locks, row locks, and unique active-binding/session indexes serialize concurrent contenders. The collision proof confirms the deterministic post-lock outcome: one owner and one rejected receipt.

## Privilege proof

`anon` and `authenticated` have no private-schema usage, table read/write, or RPC execute permission. `service_role` has no private-schema usage or table read/write permission and has execute permission only on the four approved `agent_api` RPCs. No direct table grants exist.

## Advisors

The final security advisor returned only `INFO` notices for private tables with forced RLS and no policies. This is intentional: the private schema is ungranted and has no direct-access policy by design.

The final performance advisor returned no missing foreign-key-index notices after the index migration. It returned only `INFO` notices that new indexes have not yet accumulated usage statistics, which is expected immediately after creation.

No unresolved security or performance finding is material to this schema.

## Cleanup

All isolated work, request, receipt, claim, event, binding, evidence, and session rows are removed after validation. Seeded project/profile/slot mappings and the reviewed migration history remain.
