# Organization Agent Workflow

Status: adopted operating direction; transport implementation is tracked by [ci-workflows issue #37](https://github.com/StreamScapeTV/ci-workflows/issues/37) and the deferred review-role design is tracked by [agent-state issue #184](https://github.com/StreamScapeTV/agent-state/issues/184).

## Purpose

This article is the central human-readable guide for how automated agents work across the StreamScapeTV organization. It defines the shared workflow boundary between repository instructions, reusable CI workflows, Agent State, runners, reviews, merges, and cleanup.

It is not a live session database. Agent State remains authoritative for current sessions, claims, collisions, receipts, and lifecycle decisions.

## What agents read today

Before working in any repository, an agent must read that repository's root `AGENTS.md` and any repository-specific files explicitly linked from it. The target repository's `AGENTS.md` is currently the mandatory entry point because consumer repositories have not yet completed the migration to a short central-policy reference.

Today there is no single file that safely replaces every repository's `AGENTS.md`. Generic workflow rules are still duplicated across repositories. Until each consumer migration is completed, local `AGENTS.md` rules remain binding.

The intended end state is:

1. this central article and versioned contracts in `StreamScapeTV/ci-workflows` define organization-wide workflow, runner, CI transport, and collaboration rules;
2. Agent State defines authoritative identity, session, claim, collision, receipt, review-request, and lifecycle semantics;
3. each product repository keeps a short `AGENTS.md` containing only repository identity, integration branch, local technical rules, and the central policy version or digest it follows.

## Authority boundaries

### `ci-workflows`

Owns reusable GitHub Actions orchestration, parameter validation, semantic runner selection, trusted transport, redacted job summaries, workflow contracts, and organization-wide operating documentation.

### Agent State

Owns project mapping, execution identity, sessions, claims, shared resources, collisions, retries, replay, receipts, lifecycle transitions, and pull-request ownership decisions.

GitHub Actions must not reimplement Agent State decisions.

### Product repositories

Own product code, product-specific commands and tests, architecture rules, toolchain pins, schemas, device requirements, release products, and repository-specific acceptance evidence.

### Flux

Owns live desired state, cluster policy, target allowlists, credentials, reconciliation, health, and rollback decisions.

## Agent identities

The currently supported implementation identities remain the numeric assignments documented by Agent State:

- `Agent N` for remote ChatGPT implementation sessions;
- `Codex N` for local Codex implementation sessions.

A dedicated `Agent R` or `Review Agent` role is a deferred Agent State design tracked by `StreamScapeTV/agent-state#184`. It must not be assumed available until that issue is implemented and released.

## Startup and resume

Before selecting new implementation work, an agent must obtain a fresh Agent State resume result for the exact project, profile, and assigned slot.

The agent must resume active work returned by Agent State before selecting unrelated work, subject to the current Agent State policy. Ownership is determined from the Agent State response, not from GitHub labels, issue-comment order, branch names, or remembered chat context.

Agents must not scan historical Agent State issue comments to reconstruct current ownership.

## Planned manual Agent State command workflow

The organization will provide one manually triggered workflow in `StreamScapeTV/ci-workflows`, tracked by issue #37.

The workflow will use `workflow_dispatch` and accept bounded typed inputs for one Agent State operation, such as:

- immutable request identifier;
- project and repository assertions;
- allowed lifecycle action;
- session name and agent identity where required;
- issue or pull-request origin where required;
- exact branch and base/head SHA values;
- normalized file claims and package/shared-resource keys;
- bounded summary or reason.

The workflow will:

- execute protected `ci-workflows` code only;
- use the semantic Agent State control runner profile;
- call existing Agent State API/client behavior;
- return the sanitized result through the GitHub Actions conclusion, job summary, outputs, and logs;
- use the explicit request identifier for idempotency;
- retain zero routine artifacts;
- avoid creating lifecycle issue comments or labels by default;
- avoid checking out or executing consumer, issue, pull-request, or fork source;
- avoid continuously monitoring Agent State.

Until issue #37 is implemented and released, agents must use the current repository-approved Agent State transport described by the target repository's `AGENTS.md`.

## Implementation lifecycle

Unless a repository has a stricter local rule, the implementation owner is responsible for the complete lifecycle:

1. read the target repository's `AGENTS.md` and linked local authority;
2. obtain a fresh Agent State resume result;
3. prepare or select one bounded issue;
4. obtain accepted start and claim receipts before editing;
5. create and use one bounded issue branch and pull request;
6. publish coherent checkpoints without rewriting another agent's work;
7. validate the exact final head and current integration base;
8. inspect the complete diff and resolve failures or review findings;
9. merge the unchanged validated head with expected-head protection;
10. verify the integration branch contains the delivered tree;
11. mark the Agent State implementation session `done`, or `cancel` abandoned work;
12. release claims and remove the exact merged branch and temporary resources.

An agent must not leave a completed implementation indefinitely in a generic review state.

## Review policy

Human review is not an organization-wide default at this time.

Review must eventually be explicit as one of:

- no separate review required;
- agent review required;
- human review required.

The dedicated review-request and `Agent R` model is deferred to `StreamScapeTV/agent-state#184`.

Until that model exists:

- the implementation owner performs the required final diff inspection and completes merge and cleanup;
- a separate agent review occurs only when explicitly assigned by the repository owner;
- human review occurs only when explicitly required by repository policy or the owner;
- waiting for CI is not itself evidence that another reviewer has been assigned.

## Runner selection

Consumer repositories and agents should express the workflow or product intent, not choose concrete self-hosted runner labels or container engines.

`ci-workflows` owns semantic runner profiles and their current concrete mappings. Examples include portable validation, Agent State control, Apple/Xcode, physical devices, Docker-capable work, and resource-sized OCI build profiles.

A product agent should select the documented reusable workflow/profile. It must not guess runner labels from another repository or copy concrete labels into consumer workflow code.

## Communication and projections

The following are projections or evidence, not the live ownership database:

- GitHub issue comments;
- labels;
- pull-request descriptions;
- Wiki or documentation pages;
- workflow job summaries;
- commit statuses.

Agents should use Agent State for current coordination and GitHub for bounded work tracking, code review evidence, checks, and merge history.

The manual workflow's result is a response to one command. It is not a continuously updated dashboard.

## Consumer `AGENTS.md` target shape

After central-policy migration, a product repository's root `AGENTS.md` should be short and contain at least:

- repository and Agent State project identity;
- protected integration branch;
- central policy version or digest and reference;
- repository-specific technical and security rules;
- repository-specific validation, release, deployment, signing, device, or evidence links;
- any stricter local exception.

It should not copy the full organization lifecycle, Agent State transport implementation, runner mapping, or generic cleanup policy.

## Change control

Organization-wide workflow changes are made in `StreamScapeTV/ci-workflows` through bounded issues, branches, pull requests, exact-head validation, and release/version updates.

Agent State semantic changes are made separately in `StreamScapeTV/agent-state` and are not implied by a workflow documentation change.

Current tracking:

- `StreamScapeTV/ci-workflows#37` — parameterized manual Agent State command workflow;
- `StreamScapeTV/agent-state#184` — deferred explicit review requests and dedicated Review Agent role.
