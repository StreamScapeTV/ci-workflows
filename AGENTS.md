# AGENTS.md — StreamScapeTV/ci-workflows

## Repository identity

- Repository: `StreamScapeTV/ci-workflows`
- Agent State project key: `ci-workflows`
- Protected integration branch: `main`
- Sole shared organization-policy entry point: `StreamScapeTV/organization-rules@main/AGENTS.md`

The project key is exactly `ci-workflows`. Never replace it with a phase, wave, batch, issue, branch, pull request, task name, or display title.

Before any work, read this file and then the current shared organization entry point. Read local `RUNNERS.md` when the bounded task requires CI capability selection, followed only by the repository architecture, contracts, source, tests, and issue material needed for the assigned slice. This file defines only central-CI product authority and stricter workflow requirements; the shared entry point owns the generic collaboration and development lifecycle.

## Repository authority and scope

This private repository owns reusable GitHub Actions orchestration for supported StreamScapeTV repositories, including central source admission, semantic runner resolution, validation, publication mechanics, release support, and Flux orchestration. Consumer repositories retain thin event callers, minimum permissions, bounded product configuration, and product-owned scripts, contracts, policy, credentials, and deployment data.

`main` is the integration branch and initial bootstrap consumer channel. This repository is released by an exact compatible Git tag; it does not require a GitHub Release object, attached archive, container image, or Helm chart for its own release.

## Agent State boundary

- This repository contains no Agent State transport, Supabase project configuration, project mapping, credentials, decision logic, lifecycle workflow, issue-comment bridge, MCP or plugin setup, or local compatibility client.
- Do not restore issue #32's superseded lifecycle or ownership workflow architecture or the retired compatibility transport.
- Do not inspect or modify `StreamScapeTV/agent-state-supabase`, its hosted project, schema, migrations, RPC functions, grants, deployment, or credentials during `ci-workflows` work. Those changes require a separate explicit assignment in that project.
- Never invent an Agent State receipt, ownership grant, schema state, deployment result, or fallback transport.

## Reusable workflow architecture

- Public reusable workflows live directly under `.github/workflows/reusable-*.yml` and expose `workflow_call` only.
- Optional internal multi-job leaf workflows live under `.github/workflows/internal-*.yml`, may not call another reusable workflow, and must preserve the reviewed shallow call graph.
- Consumers own event triggers, concurrency, environments, minimum caller permissions, and bounded product configuration. Reusable workflows must not silently add scheduled, branch, manual publication, or trusted-dispatch paths.
- During bootstrap, consumers may call `@main`; tagged and full-SHA references remain supported. Privileged and production callers should migrate to immutable references after a stable tag when required by reviewed policy.
- Keep workflow YAML as short, ordered orchestration. Put non-trivial algorithms in named, typed, tested functions under `src/ci_workflows/` and expose them through thin composite actions or CLI adapters.
- Public inputs and outputs must match checked-in contracts and generated reference documentation. Inputs must be bounded and may not accept arbitrary shell commands, callbacks, registry hosts, runner labels, container engines, cluster targets, namespaces, service accounts, secret names, or unrestricted matrices.
- Public and internal workflow calls and composite-action calls must remain acyclic, accessible, shallow, and compatible with the supported consumer and product inventory.

### Function-first implementation rule

- Python functions under `src/ci_workflows/` are the implementation layer. Workflow YAML owns orchestration only; composite actions and CLI adapters stay thin and delegate reusable behavior to named tested functions.
- Central function/workflow/action names describe technologies or capabilities, not product identities. Product paths, tasks, scripts, and options remain bounded caller inputs and product-owned behavior.
- Secrets are read only from fixed named environment variables chosen by the central implementation. Never log secret values or accept a caller-selected secret-variable name as a public input.
- Ordinary validation must not depend on immutable-digest, remote read-back, provenance-ledger, canary, or rollback machinery unless the bounded workflow actually requires publication/deployment semantics. Publication-specific safeguards in this repository remain authoritative where explicitly required.
- Do not add GitHub Actions cache as a workflow feature for local runners. Flux owns runner-side caching, persistent/shared storage, and deployed runner infrastructure.

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
- Cleanup runs under `if: always()` and fails closed when credentials, authentication files, containers, images, charts, caches, generated output, device or simulator state, result bundles, or temporary workspace state remain.

## Publication and release safety

- Product publication is admitted only from an exact approved Git tag and exact tagged source SHA.
- Immutable image and chart versions use the exact approved tag. Runner-image releases additionally publish the same built artifact under the mutable `latest` alias after the exact versioned tag is published; `latest` is a convenience deployment alias and is never release/source authority.
- The repository Git tag `latest` is not a valid release-version input for the runner-image workflow; an ordinary version/tag such as `1.0` remains the release authority and produces both `:1.0` and `:latest`.
- Historical tags build the exact historical commit without rewriting branches. Replaying an historical runner-image tag may therefore also move the mutable `latest` alias to that replayed artifact; use replay deliberately.
- Publication and deployment remain separate. Product release workflows do not receive Kubernetes or SOPS credentials and do not mutate clusters.
- Published images and charts require independent remote read-back. Runner-image publication must read back both the exact versioned tag and `latest` before the image job succeeds. Replays are idempotent for exact versioned content, and conflicting immutable content fails closed.

## Product validation contract

- The canonical Central self-check validates the completed final pull-request candidate against its current base.
- Workflow and action parsing, action and tool pins, permissions, trust classes, source admission, runner profiles, call graphs, readability, public API compatibility, documentation, inventory, fixtures, discovered tests, cleanup, and artifact policy must remain green.
