# AGENTS.md — StreamScapeTV/ci-workflows

## Repository

- Repository: `StreamScapeTV/ci-workflows`
- Agent State project key: `ci-workflows`
- Protected branch: `main`
- Shared organization rules: `StreamScapeTV/organization-rules@main/AGENTS.md`
- Repository inventory: `INVENTORY.yaml`

Read this file, the shared organization rules, and `INVENTORY.yaml` before changing CI.

## Working copy and scope guardrails

- Before any source work in an agent environment, hydrate the **complete exact current working ref** through Central `source.snapshot`. If the issue branch does not exist yet, create it from the authoritative base through GitHub first; then request/refresh `source.snapshot` for that exact branch/ref, download `repositories/<repo>/<ref>/source.zip` plus `manifest.json` from Google Drive, verify the manifest source SHA against the authoritative GitHub ref and verify the archive digest, and unpack that full archive as the working tree. If a fresh environment is needed after the branch advances, snapshot and redownload that exact branch again. Do not reconstruct the working branch piecemeal from GitHub file fetches.
- Google Drive is read-optimized transport only. GitHub remains branch/commit/PR authority and Agent State remains current ownership/coordination authority.
- Keep implementation and validation proportional to the exact issue. Before broadening into a repository-wide refactor, a large new test matrix, generated/framework machinery, or work likely to create or touch hundreds of files, stop and ask the owner first. Do not add broad testing or architecture merely because it is possible.

## Architecture

Keep this repository small and conventional.

- A workflow is a workflow. Do not use a `reusable-` filename prefix.
- Workflow YAML is the source of truth for `workflow_call` inputs, secrets, outputs, permissions, jobs, and behavior.
- Do not mirror workflow YAML into contracts, generated API references, validation manifests, compatibility registries, or extra inventories.
- `INVENTORY.yaml` is the only repository inventory. It contains paths only, not duplicated workflow APIs.
- Product repositories remain thin callers. Central technology workflows own their fixed executable profile behavior; callers may supply only the bounded semantic selectors/parameters declared by that workflow.
- A technology workflow is checkout/setup -> fixed reviewed profile -> literal secret scrub -> optional Agent State/Google Drive helpers. Never add arbitrary command/script/env-map inputs as a compatibility escape hatch.
- Prefer deletion over compatibility machinery.

## Shared actions

Only repeated mechanics that are awkward to duplicate in every pipeline belong in custom actions:

- `actions/agent-state` — Agent State claim/start/finish RPC calls.
- `actions/google-drive` — upload one private file under a fixed Drive root at `<repository>/<ref>/<file-name>`; exact filenames are updated in place so the same helper serves run logs and source snapshots.
- `actions/private-git` — establish the fixed Central-owned Tailscale connection to the owner private Git/Forgejo service using the owner-configured OAuth environment, hard-coded `tag:github-ci`, and fixed TCP/443 reachability check. It exposes no product-facing network/tag/host inputs. Workflows invoke it only when the selected job/profile actually needs private Git/Forgejo access.

Do not add technology wrapper actions for checkout, planning, runner selection, command execution, evidence, or one Python/shell function. Use standard upstream actions and normal workflow steps.

## Workflows

The callable technology workflows are:

- `.github/workflows/apple.yml`
- `.github/workflows/android.yml`
- `.github/workflows/python.yml`
- `.github/workflows/node.yml`
- `.github/workflows/flutter.yml`

They expose bounded technology profiles and fixed executable behavior. The broker/Agent State path never forwards shell commands. GitOps validation is retired unless a future real consumer justifies a new reviewed issue.

Agent State CI stays small:

`request_ci_run -> Agent State row -> ci-broker -> central-ci-dispatch.yml -> technology workflow -> agent-state start -> fixed profile -> literal secret scrub -> google-drive -> agent-state finish`

Concurrency belongs to the **outer trigger/dispatch workflow**, not inside reusable technology workflows. The Agent State path uses one stable branch/tag `active_key` and `cancel-in-progress: true`, so a newer accepted run supersedes older work for the same repository/ref. A direct private-repository caller must define equivalent outer concurrency for its own branch/ref. Do not add branch-wide concurrency inside `apple.yml`, `android.yml`, or another reusable technology workflow, because sibling profiles intentionally launched inside one outer run must be allowed to coexist. Temporary proof workflows that can start expensive sibling jobs follow the same rule: one issue/run-scoped outer concurrency group with `cancel-in-progress: true`, never per-sibling cancellation.

The same relay also accepts `workflow_key=source.snapshot`. Central checks out the requested repository/ref, archives exactly the tracked Git tree to `source.zip`, writes `manifest.json`, and uses the same Google Drive action under the fixed repositories root. The second exact-name manifest upload must preserve the same Drive file identity; no separate snapshot framework belongs here.

Do not add `.github/central-ci.json`, source-SHA evidence to ordinary validation rows, profile manifests, a private technology executor, or R2/Cloudflare storage.

## Validation

Self-CI is deliberately small:

1. parse workflow/action YAML;
2. run focused Agent State/Google Drive/private-Git/broker tests;
3. self-review the complete affected workflow/runtime/release path, then prove materially changed technology workflows with real product consumers;
4. prove `source.snapshot` by raw-downloading and opening the Drive ZIP through the ordinary Google Drive connector.

Do not rebuild a contract-test framework around the workflows.

## Simplicity check

Before keeping or adding a workflow, action, source file, contract, adapter, test, or verification step, ask whether a real product command, Agent State call, Google Drive upload, private Git/Forgejo access, broker, source snapshot, or runner-image build actually needs it. If not, delete it or do not add it.

## Merge self-review

Before opening a PR, review the complete affected workflow/runtime/release path from caller input through source checkout, fixed profile execution, private-log scrubbing/upload, Agent State terminalization, and any release boundary touched by the change. Fix small regressions directly on the issue branch; do not create a permanent mirrored contract/test bureaucracy.
