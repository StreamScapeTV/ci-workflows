# AGENTS.md — StreamScapeTV/ci-workflows

## Repository

- Repository: `StreamScapeTV/ci-workflows`
- Agent State project key: `ci-workflows`
- Protected branch: `main`
- Shared organization rules: `StreamScapeTV/organization-rules@main/AGENTS.md`
- Repository inventory: `INVENTORY.yaml`

Read this file, the shared organization rules, and `INVENTORY.yaml` before changing CI.

## Architecture

Keep this repository small and conventional.

- A workflow is a workflow. Do not use a `reusable-` filename prefix.
- Workflow YAML is the source of truth for `workflow_call` inputs, secrets, outputs, permissions, jobs, and behavior.
- Do not mirror workflow YAML into contracts, generated API references, validation manifests, compatibility registries, or extra inventories.
- `INVENTORY.yaml` is the only repository inventory. It contains paths only, not duplicated workflow APIs.
- Product repositories remain thin callers and own their prepare/build/test/release commands.
- A technology workflow is checkout/setup -> direct product commands -> optional Agent State/R2 helpers.
- Prefer deletion over compatibility machinery.

## Shared actions

Only repeated mechanics that are awkward to duplicate in every pipeline belong in custom actions:

- `actions/agent-state` — Agent State claim/start/finish RPC calls.
- `actions/r2-upload` — gzip one local CI log and PUT it once to Cloudflare R2.

Do not add technology wrapper actions for checkout, planning, runner selection, command execution, evidence, or one Python/shell function. Use standard upstream actions and normal workflow steps.

## Workflows

The callable technology workflows are:

- `.github/workflows/apple.yml`
- `.github/workflows/android.yml`
- `.github/workflows/python.yml`
- `.github/workflows/node.yml`
- `.github/workflows/flutter.yml`
- `.github/workflows/gitops.yml`

They run caller-owned `prepare_command`, `build_command`, `test_command`, and `release_command` values directly. Empty stages are skipped; at least one command is required.

Agent State CI stays small:

`request_ci_run -> Agent State row -> ci-broker -> central-ci-dispatch.yml -> technology workflow -> agent-state start -> commands -> r2-upload -> agent-state finish`

Do not add `.github/central-ci.json`, source-SHA evidence, profile manifests, a private technology executor, or R2 read-back/hash receipts.

## Validation

Self-CI is deliberately small:

1. parse workflow/action YAML;
2. run focused Agent State/R2/broker tests;
3. prove the technology workflows with real product consumers.

Do not rebuild a contract-test framework around the workflows.

## Simplicity check

Before keeping or adding a workflow, action, source file, contract, adapter, test, or verification step, ask whether a real product command, Agent State call, R2 upload, broker, or runner-image build actually needs it. If not, delete it or do not add it.
