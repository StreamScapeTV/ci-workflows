# AGENTS.md — StreamScapeTV/ci-workflows

## Repository authority

- Repository: `StreamScapeTV/ci-workflows`
- Agent State project key: `ci-workflows`
- Protected integration branch: `main`
- Shared organization policy: `StreamScapeTV/organization-rules@main/AGENTS.yaml`
- Local path inventory: `INVENTORY.yaml`
- Google Drive repository folder ID: `1--JcV6RK8jdIIP3ONWw420QDVpNTQ7L8`

Read the shared organization entry point and the authorities it routes. It owns
generic working-copy, Agent State, Google Drive, branch/PR, cross-project,
validation, merge, and cleanup rules. This file adds only `ci-workflows`-specific
constraints.

## Canonical feature specifications

- `feature-specs/**` is the canonical architecture and product-behavior authority
  for this repository. Read every applicable feature specification before
  planning, implementing, reviewing, or changing Central CI behavior.
- Current source, workflow YAML, tests, documentation, issues, pull requests,
  comments, historical behavior, and implementation convenience do not override
  an applicable feature specification.
- Implementing behavior that conflicts with an applicable feature specification
  is forbidden. If current code or requested work conflicts with a spec, stop the
  conflicting implementation and surface the mismatch instead of silently
  redefining the architecture.
- Changes to `feature-specs/**` require the owner's explicit permission. Do not
  infer permission from an issue, failing test, migration, review finding,
  implementation need, or cross-project request. When the owner explicitly
  authorizes a spec change, the durable carrier must state that authority and its
  bounded scope.
- Workflow YAML remains the exact runtime/API source of truth only within the
  architecture and behavior allowed by the applicable feature specifications.
  Runtime YAML cannot redefine or override those specifications.
- The feature-spec authority/index is `feature-specs/README.md`. The canonical
  simple release model is `feature-specs/release.md`.

## Workflow architecture

Keep Central CI small, conventional, and fixed-profile.

- Subject to the applicable `feature-specs/**`, workflow YAML is the exact
  runtime/API source of truth for each workflow's inputs, secrets, outputs,
  permissions, jobs, and behavior. Workflow YAML must not contradict or extend
  architecture beyond the canonical feature specifications.
- Product repositories remain thin callers. Expose only bounded semantic
  selectors and parameters required by a reviewed profile.
- Never expose arbitrary commands, script paths, environment maps, runner or
  host selectors, secret names, registry/network configuration, or equivalent
  escape hatches to callers.
- Keep GitHub-required workflow entrypoints in `.github/workflows/`. Do not
  mirror workflow APIs into generated contracts, compatibility registries, or
  parallel inventories.
- `INVENTORY.yaml` is the only local path inventory; keep it path-oriented.
- Put custom actions in `actions/` only for genuinely repeated Central mechanics
  that are awkward to express directly in the workflows. Do not create wrapper
  actions or frameworks for one workflow step or one technology command.
- Keep product command detail product-owned. Central selects fixed reviewed
  profiles rather than accepting caller-supplied execution detail.
- Generic Repository CI uses `repository-plan.yml` as the semantic parent and
  `repository.yml` as the single-OS child executor. A tracked repository plan may
  select only reviewed OS identifiers; Central resolves each child host class and
  keeps one Agent State parent lifecycle.

## Validation

Keep self-validation proportional to the changed behavior:

1. parse affected workflow/action YAML;
2. run the smallest focused repository tests needed by the change;
3. self-review the complete affected Central path;
4. when runtime behavior materially changes, prove it with a real product
   consumer before treating the candidate as final.

During iterative Android/Apple real-consumer proof of a Central change, prefer
generic `validation.repository/test` with the repository-owned fixed `.ci/test.sh`
entrypoint, bounded safe selectors, and only the reviewed semantic inputs needed
by that repository. For ordinary
Agent State validation of a moving branch, pass its stable human branch name
(for example `develop` or the issue branch) as `ref`; do not substitute the
current commit SHA. A newer ordinary request supersedes an older active request
only within the same repository/ref/tag and semantic workflow-family lane;
independent families such as Android and Apple, or validation and release, must
remain eligible to run concurrently. Record the source SHA observed by the run
as exact evidence identity, not ordinary request/concurrency identity. Reserve
broad native `full` validation for the final merge-intended
state of that stable branch/PR lane, unless issue or product authority explicitly
requires broader evidence earlier. Read the exact profile/input contract from
the selected workflow YAML; do not copy it into this file or `INVENTORY.yaml`.

A green self-check proves Central source consistency, not an external product,
publication, deployment, signing, or physical-device result that did not run.

## Simplicity

Prefer deletion and direct workflow behavior over compatibility machinery. Do
not add a framework, adapter, contract mirror, inventory, workflow, action, or
test layer unless a current Central capability genuinely needs it.
