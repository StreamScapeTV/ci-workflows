# Parameterized Agent State command

`.github/workflows/agent-state-command.yml` is the manually dispatched Agent State path for user-assigned remote `Agent N` sessions. It is a trusted control workflow, not a reusable product workflow and not a consumer-source executor.

Ordinary consumers learn how to use it through `StreamScapeTV/organization-rules@main/AGENTS.md`. This document is the maintainer contract for the central implementation.

## Trust boundary

- The workflow runs only from protected `StreamScapeTV/ci-workflows@main` on the `agent-state-control` semantic runner intent.
- It accepts only an exact centrally mapped repository/project assertion and an explicitly authorized GitHub actor.
- It reads target repository metadata through one contents/read-only organization credential and never checks out target, issue, pull-request, branch, fork, or artifact source.
- It submits typed data to the existing Agent State API. Agent State remains the sole authority for identity, collisions, lifecycle, retries, replay, receipts, and ownership.
- It creates no lifecycle comment, label, consumer commit, product, deployment, or routine artifact.

## Dispatch inputs

All commands require `request_id`, `repository`, `project`, `action`, and the exact assigned `session_name` (`Agent N`). `Codex N` and `cod-agent-*` identities are rejected.

| Action | Additional required values | Optional assertions |
|---|---|---|
| `resume` | none | none |
| `start` | `agent_id`, `issue_number`, `task`, `base_sha`, `branch`, and at least one file or package | `claim_type`, `claim_mode` |
| `claim` | `agent_id`, `issue_number`, and at least one file or package | `claim_type`, `claim_mode` |
| `release` | `agent_id`, `issue_number`, and at least one file or package | none |
| `reconcile_base` | `agent_id`, `issue_number`, `branch` | current `base_sha` assertion |
| `block` | `agent_id`, `issue_number`, `reason` | none |
| `review` | `agent_id`, `issue_number`, `summary` | paired `pr_number` and exact `head_sha` |
| `done` | `agent_id`, `issue_number`, `summary` | paired `pr_number` and exact `head_sha` |
| `cancel` | `agent_id`, `issue_number`, `reason` | none |

`files` and `packages` are newline-delimited bounded lists. Paths must be canonical repository-relative paths with no traversal, absolute paths, control characters, or glob syntax. Duplicate values fail closed. `write + prefix` is forbidden.

The `request_id` is the idempotency authority. Reuse it only for the exact same logical retry or replay. A conflicting payload under the same ID is rejected by Agent State.

## Metadata and freshness

Before transport, the workflow:

1. revalidates the repository/project/integration-branch mapping through both the checked-in routing contract and Agent State;
2. resolves the current integration SHA through trusted GitHub metadata;
3. verifies the originating issue and its open/closed state when applicable;
4. verifies the exact current PR base and head whenever a PR assertion is supplied;
5. binds remote starts and later commands to the authenticated dispatch actor and originating issue.

The existing Agent State lifecycle compatibility endpoint is used only for remote `resume` and `reconcile_base`, because those operations have no equivalent direct remote route. Other actions use the current typed Agent State endpoints with the explicit `request_id`.

## Result contract

The workflow run is the response surface. It writes bounded step/job outputs and one concise job summary, and logs one sanitized `AGENT_STATE_COMMAND_RESULT` JSON object.

Important outputs include:

- `accepted`;
- `decision`;
- `instruction`;
- `receipt_id`;
- `agent_id`;
- `lifecycle_status`;
- `request_id`.

A queued or running job grants no ownership. `start` or `claim` grants ownership only when the terminal run succeeds, `accepted=true`, and a non-empty receipt is present. Rejections, retry exhaustion, API outage, stale source, unauthorized actors, mapping mismatch, and malformed inputs fail the job.

## Configuration

The central workflow uses three explicit masked secrets:

- `AGENT_STATE_API_URL` contains the private Agent State API location. It is a secret rather than a GitHub variable so the endpoint is masked in workflow environment rendering and logs.
- `AGENT_STATE_API_TOKEN` is optional and is used only when the Agent State deployment requires API authentication.
- `AGENT_STATE_GITHUB_TOKEN` is a read-only organization credential used only for target repository, branch, issue, and pull-request metadata.

No secret is inherited, selected by an input, exposed to target source, or written to an artifact. The workflow cannot operate until the required endpoint and read-only metadata credentials are configured centrally.
