# Supabase Agent State transactional core

Issue #52 establishes Supabase PostgreSQL as the reviewed state and decision engine while #55 still owns organization-wide cutover and retirement. The schema is reconstructible from the ordered files under `supabase/migrations/`.

## Authority and isolation

Authoritative rows live in the private `agent_private` schema. Shared normalized tables are keyed by project; repositories do not receive copied table families. `PUBLIC`, `anon`, `authenticated`, and `service_role` have no table, sequence, or helper-function privileges. Row-level security is enabled and forced as defense in depth with no direct-access policies.

Ordinary approved agents use only these `service_role`-callable functions:

- `agent_api.command(jsonb)`
- `agent_api.resume(text, text, text)`
- `agent_api.context(text, text, text)`
- `agent_api.ownership_check(text, text, bigint, text, bigint, text)`

The connected administration capability can execute arbitrary SQL, but ordinary lifecycle operation must never use table-level `insert`, `update`, `delete`, `truncate`, `drop`, grants, policies, migrations, or `agent_private` helpers.

## Direct connector examples

A command is one bounded JSON object:

```sql
select agent_api.command(
  '{
    "contract_version": 1,
    "request_id": "issue52-example-start-0001",
    "action": "start",
    "project": "ci-workflows",
    "repository": "StreamScapeTV/ci-workflows",
    "session_name": "Agent 2",
    "task": "Implement one bounded issue",
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

Resume without mutation of work state:

```sql
select agent_api.resume(
  'ci-workflows',
  'StreamScapeTV/ci-workflows',
  'Agent 2'
);
```

Read bounded context or exact ownership:

```sql
select agent_api.context(
  'ci-workflows',
  'StreamScapeTV/ci-workflows',
  'gpt-agent-2-20260806-1800-a1b2'
);

select agent_api.ownership_check(
  'ci-workflows',
  'StreamScapeTV/ci-workflows',
  52,
  'issue/52-supabase-core-a1b2',
  57,
  '0123456789abcdef0123456789abcdef01234567'
);
```

The connector call contains only the approved `select agent_api...` statement. It does not invoke GitHub Actions, comments, runners, commits, Edge Functions, or `agentctl`.

## Transactions and collisions

Every command serializes immutable request IDs first. An identical canonical JSON request returns the stored response and receipt; changed data under the same request ID fails before mutation. Mutations then lock the mapped project, project row, and profile slot. This provides deterministic project-scoped ordering.

`start` inserts the request, session, issue/branch binding, evidence, and initial claims in one transaction. Exact and prefix file overlap is checked together; package, resource, manifest, and device claims collide by exact identity. A collision produces an immutable rejected receipt instead of partial ownership. `done` and `cancel` release every active claim and terminalize the binding in one transaction.

Requests, receipts, and events are immutable. Events are append-only. Terminal sessions remain historical but their inactive bindings and released claims do not block later work.

## Validation and boundaries

The command dispatcher rejects unknown fields, malformed project/repository/session/agent identities, unsafe paths, duplicate claim lists, stale base/PR/head assertions, unsupported transitions, and request-ID hash conflicts. Stable responses include `contract_version`, `request_id`, and `receipt_id`.

Issue #53 owns parking, activation, priorities, and multiple same-slot work. Issue #54 owns review requests, takeover, and trusted bots. Issue #55 owns metadata migration, organization-rules cutover, and retirement of the old API/workflow/client paths. None is implemented here.
