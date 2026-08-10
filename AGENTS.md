# AGENTS.md — StreamScapeTV/ci-workflows

## Repository identity

- Repository: `StreamScapeTV/ci-workflows`
- Agent State project key: `ci-workflows`
- Protected integration branch: `main`
- Shared organization-policy entry point: `StreamScapeTV/organization-rules@main/AGENTS.md`

The project key is exactly `ci-workflows`. Never replace it with a phase, wave, batch, issue, branch, pull request, task name, or display title.

## Required read order

Before any work:

1. read this file from the current protected `main` branch;
2. read and follow `StreamScapeTV/organization-rules@main/AGENTS.md` and its routed files;
3. read `RUNNERS.md` only when the bounded task requires CI capability selection;
4. read only the repository architecture, contracts, source, tests, and issue material needed for the assigned slice.

The organization entry point owns identity, Agent State transport, ownership/resources, branch/worktree behavior, review, merge, cleanup, environment, and generic security rules. Do not copy those rules into this repository or use a stale topic-branch copy as a substitute for current `organization-rules@main`.

## Local Codex startup

Local Codex workers use ordinary Git and a separate issue worktree.

- Fetch and prune remote metadata before starting or resuming.
- Verify current `origin/main`, the assigned remote branch/head, and the worktree head before editing.
- Never switch or edit the shared `main` checkout.
- Never use a blind `git pull`, rebase published history, force-push, destructive reset, broad clean, overwrite another worktree, or create a replacement branch when an existing issue branch is assigned.
- A remote/Agent State SHA mismatch requires a fresh read and reconciliation before editing.

Agent State coordination is independent of the Git worktree. This repository and its issue worktrees require no Supabase project directory, repository linkage, GitHub/Supabase integration, environment-variable setup, `supabase init`, or `supabase link`. Remote ChatGPT uses the official Supabase connector; local Codex uses the already provisioned local CLI direct-RPC path defined by current organization rules.

## Repository authority and scope

This private repository owns reusable GitHub Actions orchestration for supported StreamScapeTV repositories, including central source admission, semantic runner resolution, validation, publication mechanics, release support, and Flux orchestration. Consumer repositories retain thin event callers, minimum permissions, bounded product configuration, and product-owned scripts, contracts, policy, credentials, and deployment data.

`main` is the integration branch and initial bootstrap consumer channel. This repository is released by an exact compatible Git tag; it does not require a GitHub Release object, attached archive, container image, or Helm chart for its own release.

## Agent State boundary

- Ordinary coordination calls only the already deployed approved `agent_api.current_*_v2` functions through the transport defined by `organization-rules@main`.
- This repository contains no Agent State transport, Supabase project configuration, project mapping, credentials, decision logic, lifecycle workflow, issue-comment bridge, MCP/plugin setup, or local compatibility client.
- Do not restore issue #32's superseded lifecycle/ownership workflow architecture or the retired compatibility transport.
- Do not inspect or modify `StreamScapeTV/agent-state-supabase`, its hosted project, schema, migrations, RPC functions, grants, deployment, or credentials during `ci-workflows` work. Those changes are forbidden unless the owner separately and explicitly requests that project.
- Never invent an Agent State receipt, ownership grant, schema state, deployment result, or fallback transport.

## Reusable workflow architecture

- Public reusable workflows live directly under `.github/workflows/reusable-*.yml` and expose `workflow_call` only.
- Optional internal multi-job leaf workflows live under `.github/workflows/internal-*.yml`, may not call another reusable workflow, and must preserve the reviewed shallow call graph.
- Consumers own event triggers, concurrency, environments, minimum caller permissions, and bounded product configuration. Reusable workflows must not silently add scheduled, branch, manual publication, or trusted-dispatch paths.
- During bootstrap, consumers may call `@main`; tagged and full-SHA references remain supported. Privileged and production callers should migrate to immutable references after a stable tag when required by reviewed policy.
- Keep workflow YAML as short, ordered orchestration. Put non-trivial algorithms in named, typed, tested functions under `src/ci_workflows/` and expose them through thin composite actions or CLI adapters.
- Public inputs and outputs must match checked-in contracts and generated reference documentation. Inputs must be bounded and may not accept arbitrary shell commands, callbacks, registry hosts, runner labels, container engines, cluster targets, namespaces, service accounts, secret names, or unrestricted matrices.
- Public/internal workflow calls and composite-action calls must remain acyclic, accessible, shallow, and compatible with the supported consumer and product inventory.

## Authority boundaries

- Flux remains the sole authority for desired state, target and product allowlists, SOPS data, Kubernetes credentials, reconciliation policy, canary selection, live health, and rollback acceptance. This repository owns only reviewed orchestration around exact Flux-owned policy source.
- Product repositories retain toolchain pins, product commands, schemas, assertions, test selection, signing, release inputs, and deployment-specific data.
- Shared organization policy changes belong only in `StreamScapeTV/organization-rules`.

## Runner and central self-check boundary

- Semantic runner intent remains authoritative. Ordinary Python, policy, source-admission, and GitOps validation use the `portable` capability; consumers do not select concrete runner labels or hosts.
- The Central self-check rejects fork source and verifies an absolute pre-provisioned CPython 3.12 Linux runtime before checkout. It installs or elevates no host runtime, applies the repository's digest-locked validation dependency bootstrap, and uses the verified absolute interpreter for every later Python command.
- General Linux validation grants no signing, provisioning, simulator, physical-device, notarization, store, registry, Kubernetes, production, or Agent State credential or authority.
- Do not restore the retired emergency macOS exception or copy it into another workflow. Apple-specific work continues to use separately reviewed `apple` capacity.

## Security, artifacts, and cleanup

- Use explicit workflow and job permissions, explicit named secrets, workflow-scoped authentication files, and no `secrets: inherit`.
- Never execute untrusted pull-request, fork, issue-comment, `pull_request_target`, `workflow_run`, or mutable source in a privileged context.
- Privileged modes require exact admitted source, exact checkout assertions, detached credential-free state where applicable, and `persist-credentials: false`.
- Central workflows select semantic runner profiles and internal implementations. Consumers do not select concrete runner labels, hosts, Docker versus Buildah, storage drivers, devices, clusters, namespaces, or service accounts.
- Routine workflows retain zero GitHub Actions artifacts. Any exception must be named, bounded, justified, redacted, registered in contract, and tested.
- Cleanup runs under `if: always()` and fails closed when credentials, authentication files, containers, images, charts, caches, generated output, device/simulator state, result bundles, or temporary workspace state remain.

## Publication and release safety

- Product publication is admitted only from an exact approved Git tag and exact tagged source SHA.
- Immutable image and chart versions use the exact approved tag. Never publish `latest`.
- Historical tags build the exact historical commit without rewriting branches.
- Publication and deployment remain separate. Product release workflows do not receive Kubernetes or SOPS credentials and do not mutate clusters.
- Published images and charts require independent remote read-back. Replays are idempotent, and conflicting immutable content fails closed.

## Validation contract

- The canonical Central self-check validates the exact final pull-request head against the current base.
- Workflow/action parsing, action and tool pins, permissions, trust classes, source admission, runner profiles, call graphs, readability, public API compatibility, documentation, inventory, fixtures, discovered tests, cleanup, and artifact policy must remain green.
- A changed head invalidates older evidence. Queued, skipped, stale, cancelled, timed-out, missing, or partially successful checks are not passing evidence.
- Before integration, inspect the complete current-base diff, public contracts, permissions, trust boundaries, artifacts, cleanup behavior, generated drift, and every review thread.
- Merge only the unchanged validated head with expected-head protection, then verify `main`, record exact evidence, and remove the merged issue branch.